import asyncio
import json
import logging
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fake_exchange import SETTINGS, TRADING, FakeExchange, open_long

from quantdeck_engine.binance.rest import UnknownSymbol
from quantdeck_engine.binance.user_stream import AccountUpdate, UserStreamConnected
from quantdeck_engine.config import SymbolConfig, TradingConfig, parse_settings
from quantdeck_engine.lock import EngineAlreadyRunning, InstanceLock
from quantdeck_engine.run import Engine, RunFailed, run_engine, start_engine
from quantdeck_engine.snapshot import fixed_point
from quantdeck_engine.startup import StartupFailed
from quantdeck_engine.trading.halt import HaltRecord, HaltStore
from quantdeck_engine.trading.risk import RiskChecker
from quantdeck_engine.trading.sizing import DailyLoss
from quantdeck_engine.trading.state import PositionClosed, resync

D = Decimal

ALGO = ("POST", "/fapi/v1/algoOrder")
LEVERAGE = ("POST", "/fapi/v1/leverage")
DAILY = DailyLoss(base=D(5000), loss=D(0), limit=D(150))


@pytest.fixture
def store(tmp_path: Path) -> HaltStore:
    return HaltStore(tmp_path / "state" / "halt.json")


async def until(condition: Callable[[], bool]) -> None:
    for _ in range(1000):
        if condition():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("condition not met")


class CountingGuard:
    """Stands in for `RiskGuard.check`; queued errors are raised first."""

    def __init__(self, *errors: Exception) -> None:
        self.calls = 0
        self.errors = list(errors)

    async def check(self) -> DailyLoss:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return DAILY


# Instance lock (D-31)


def test_second_engine_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "state" / "engine.lock"
    with InstanceLock(path):
        with pytest.raises(EngineAlreadyRunning), InstanceLock(path):
            pass


def test_lock_is_released_on_exit(tmp_path: Path) -> None:
    path = tmp_path / "engine.lock"
    with InstanceLock(path):
        pass
    with InstanceLock(path):
        pass


async def test_run_refuses_mainnet() -> None:
    mainnet = parse_settings(
        {"BINANCE_API_KEY": "k", "BINANCE_API_SECRET": "s", "BINANCE_TESTNET": "false"}
    )
    with pytest.raises(RunFailed, match="testnet-only"):
        await run_engine(mainnet, TRADING)


# Startup (D-05, D-16, D-19, D-26, D-27)


async def test_hedge_mode_is_refused(store: HaltStore) -> None:
    exchange = FakeExchange(hedge_mode=True)
    with pytest.raises(StartupFailed, match="hedge mode"):
        await start_engine(exchange.client(), SETTINGS, TRADING, store)


async def test_unknown_symbol_is_refused(store: HaltStore) -> None:
    trading = TradingConfig(
        risk_per_trade_percent=D(1),
        daily_loss_limit_percent=D(3),
        max_notional_multiple=D(2),
        symbols={"ETHUSDT": SymbolConfig("ETHUSDT", 5)},
    )
    with pytest.raises(UnknownSymbol, match="ETHUSDT is not in exchangeInfo"):
        await start_engine(FakeExchange().client(), SETTINGS, trading, store)


async def test_flat_account_gets_the_configured_leverage(store: HaltStore) -> None:
    exchange = FakeExchange(leverage=3)
    engine = await start_engine(exchange.client(), SETTINGS, TRADING, store)
    assert len(exchange.calls(*LEVERAGE)) == 1
    assert exchange.leverage == 5
    assert list(engine.traders) == ["BTCUSDT"]
    assert not engine.guard.halted


async def test_position_without_a_stop_gets_an_emergency_stop(store: HaltStore) -> None:
    exchange = FakeExchange()
    open_long(exchange)
    await start_engine(exchange.client(), SETTINGS, TRADING, store)
    assert len(exchange.calls(*ALGO)) == 1
    assert exchange.position_amount == D("0.083")


async def test_halt_record_starts_halted_and_flattens(store: HaltStore) -> None:
    store.save(HaltRecord(reason="test halt", halted_at="2026-10-16T00:00:00+00:00"))
    exchange = FakeExchange()
    open_long(exchange)
    exchange.add_stop("SELL", "59400")
    engine = await start_engine(exchange.client(), SETTINGS, TRADING, store)
    assert engine.guard.halted
    assert exchange.position_amount == 0
    assert exchange.algo_orders == []


# Events that call for a risk check (D-30)


async def started(exchange: FakeExchange, store: HaltStore) -> Engine:
    return await start_engine(exchange.client(), SETTINGS, TRADING, store)


async def test_reconnect_and_funding_request_a_check(store: HaltStore) -> None:
    engine = await started(FakeExchange(), store)
    engine.on_event(AccountUpdate(event_time=0, reason="ORDER", balances=(), positions=()))
    assert not engine.checker.requested
    engine.on_event(AccountUpdate(event_time=0, reason="FUNDING_FEE", balances=(), positions=()))
    assert engine.checker.requested

    engine = await started(FakeExchange(), store)
    engine.on_event(UserStreamConnected())
    assert engine.checker.requested


async def test_closed_position_requests_a_check_and_cancels_its_stop(store: HaltStore) -> None:
    exchange = FakeExchange()
    engine = await started(exchange, store)
    exchange.add_stop("SELL", "59400")  # left behind by a position that just closed
    await engine.on_change(PositionClosed("BTCUSDT"))
    assert engine.checker.requested
    assert exchange.algo_orders == []


# The risk checker (D-30)


async def test_checker_runs_at_start_and_once_for_requests_close_together() -> None:
    guard = CountingGuard()
    delays: list[float] = []

    async def record(seconds: float) -> None:
        delays.append(seconds)

    checker = RiskChecker(guard, interval=60, delay=3, sleep=record)  # type: ignore[arg-type]
    task = asyncio.create_task(checker.run())
    try:
        await until(lambda: guard.calls == 1)
        assert checker.latest == DAILY
        checker.request()
        checker.request()
        await until(lambda: guard.calls == 2)
        await asyncio.sleep(0.01)
        assert guard.calls == 2
        assert delays == [3]  # waits for income records to appear
    finally:
        task.cancel()


async def test_checker_runs_every_interval_without_requests() -> None:
    guard = CountingGuard()
    checker = RiskChecker(guard, interval=0.01)  # type: ignore[arg-type]
    task = asyncio.create_task(checker.run())
    try:
        await until(lambda: guard.calls >= 3)
    finally:
        task.cancel()


async def test_checker_keeps_running_after_a_failed_check(
    caplog: pytest.LogCaptureFixture,
) -> None:
    guard = CountingGuard(httpx.ConnectError("down"))
    checker = RiskChecker(guard, interval=0.01)  # type: ignore[arg-type]
    task = asyncio.create_task(checker.run())
    try:
        with caplog.at_level(logging.WARNING):
            await until(lambda: checker.latest is not None)
        assert "Risk check failed (down)" in caplog.text
    finally:
        task.cancel()


# What clients see and send (D-33)


async def test_snapshot_of_a_protected_position(store: HaltStore) -> None:
    exchange = FakeExchange()
    open_long(exchange)  # 0.083 at 60000
    exchange.add_stop("SELL", "59400")
    client = exchange.client()
    engine = await start_engine(client, SETTINGS, TRADING, store)
    await resync(client, engine.state)
    engine.mark_prices["BTCUSDT"] = D("60100.5")

    snapshot = engine.snapshot()
    json.dumps(snapshot)  # JSON-ready as it is
    assert snapshot["network"] == "testnet"
    assert snapshot["halt"] == {"halted": False, "reason": None, "since": None}
    assert snapshot["daily_loss"] is None  # until the first check
    assert snapshot["balances"] == {"USDT": "5000"}
    (symbol,) = snapshot["symbols"]
    assert symbol["symbol"] == "BTCUSDT" and symbol["halted"] is None
    assert symbol["mark_price"] == "60100.5"
    assert symbol["position"] == {
        "amount": "0.083",
        "entry_price": "60000",
        "unrealized_pnl": "8.3415",
    }
    assert symbol["orders"] == []
    assert [(s["side"], s["trigger_price"], s["close_position"]) for s in symbol["stops"]] == [
        ("SELL", "59400", True)
    ]

    engine.checker.latest = await engine.guard.check()
    assert engine.snapshot()["daily_loss"] == {
        "asset": "USDT",
        "base": "5000",
        "loss": "0",
        "limit": "150",
    }


async def test_stop_waits_for_a_kill_switch_in_progress(store: HaltStore) -> None:
    engine = await started(FakeExchange(), store)
    async with engine.guard._kill_lock:  # as a running kill switch holds it
        stopping = asyncio.create_task(engine.stop())
        await asyncio.sleep(0.01)
        assert not engine.stop_requested.is_set()
    await asyncio.wait_for(stopping, 1)
    assert engine.stop_requested.is_set()


def test_amounts_are_never_in_exponent_notation() -> None:
    assert fixed_point(D("0E-8")) == "0.00000000"
    assert fixed_point(D("1E+3")) == "1000"


async def test_emergency_close_halts_and_shows_in_the_snapshot(store: HaltStore) -> None:
    exchange = FakeExchange()
    open_long(exchange)
    exchange.add_stop("SELL", "59400")
    engine = await start_engine(exchange.client(), SETTINGS, TRADING, store)
    assert await engine.emergency_close() is True
    assert exchange.position_amount == 0
    halt = engine.snapshot()["halt"]
    assert halt["halted"] is True
    assert "emergency close requested by the user" in halt["reason"]
    assert halt["since"]
    assert await engine.lift_halt() is True
    assert engine.snapshot()["halt"]["halted"] is False
