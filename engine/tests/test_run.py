import asyncio
import json
import logging
from collections.abc import Callable
from contextlib import suppress
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from fake_exchange import SETTINGS, SYMBOLS, TRADING, FakeExchange, open_long
from websockets.asyncio.client import connect

from quantdeck_engine.binance.rest import UnknownSymbol
from quantdeck_engine.binance.streams import kline_from_row
from quantdeck_engine.binance.user_stream import (
    AccountUpdate,
    OrderUpdate,
    UserStreamConnected,
    parse_order_update,
)
from quantdeck_engine.config import ConfigError, SymbolConfig, parse_settings, strategy_config
from quantdeck_engine.lock import EngineAlreadyRunning, InstanceLock
from quantdeck_engine.run import Engine, RunFailed, run_engine, start_engine
from quantdeck_engine.server import ClientServer, ConnectionFile, LogBuffer
from quantdeck_engine.snapshot import fixed_point
from quantdeck_engine.startup import StartupFailed
from quantdeck_engine.symbols import PauseNotSaved, SymbolChangeRefused, SymbolStore
from quantdeck_engine.times import iso_utc
from quantdeck_engine.trading.executor import TradingHalted
from quantdeck_engine.trading.halt import HaltRecord, HaltStore
from quantdeck_engine.trading.order_ids import Role, parse_client_id
from quantdeck_engine.trading.risk import RiskChecker
from quantdeck_engine.trading.rules import Side
from quantdeck_engine.trading.sizing import DailyLoss
from quantdeck_engine.trading.state import PositionClosed, resync
from quantdeck_engine.trading.strategy_runner import Signal

D = Decimal

ALGO = ("POST", "/fapi/v1/algoOrder")
LEVERAGE = ("POST", "/fapi/v1/leverage")
DAILY = DailyLoss(base=D(5000), loss=D(0), limit=D(150))


@pytest.fixture
def store(tmp_path: Path) -> HaltStore:
    return HaltStore(tmp_path / "state" / "halt.json")


@pytest.fixture
def symbol_store(tmp_path: Path) -> SymbolStore:
    return SymbolStore(tmp_path / "state" / "symbols.json")


async def start(
    exchange: FakeExchange,
    store: HaltStore,
    symbol_store: SymbolStore,
    symbols: dict[str, SymbolConfig] = SYMBOLS,
    forward_dir: Path | None = None,
    paused: dict[str, str] | None = None,
) -> Engine:
    return await start_engine(
        exchange.client(),
        SETTINGS,
        TRADING,
        store,
        symbols,
        symbol_store,
        paused=paused,
        forward_dir=forward_dir or symbol_store.path.parent.parent / "forward",
    )


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


async def test_hedge_mode_is_refused(store: HaltStore, symbol_store: SymbolStore) -> None:
    with pytest.raises(StartupFailed, match="hedge mode"):
        await start(FakeExchange(hedge_mode=True), store, symbol_store)


async def test_unknown_symbol_is_refused(store: HaltStore, symbol_store: SymbolStore) -> None:
    symbols = {"ETHUSDT": SymbolConfig("ETHUSDT", 5)}
    with pytest.raises(UnknownSymbol, match="ETHUSDT is not in exchangeInfo"):
        await start(FakeExchange(), store, symbol_store, symbols)


async def test_symbol_margined_in_another_asset_is_refused(
    store: HaltStore, symbol_store: SymbolStore
) -> None:
    with pytest.raises(StartupFailed, match="margined in USDC"):
        await start(FakeExchange(margin_asset="USDC"), store, symbol_store)


async def test_flat_account_gets_the_configured_leverage(
    store: HaltStore, symbol_store: SymbolStore
) -> None:
    exchange = FakeExchange(leverage=3)
    engine = await start(exchange, store, symbol_store)
    assert len(exchange.calls(*LEVERAGE)) == 1
    assert exchange.leverage == 5
    assert list(engine.traders) == ["BTCUSDT"]
    assert not engine.guard.halted


async def test_engine_starts_with_no_symbols(store: HaltStore, symbol_store: SymbolStore) -> None:
    # It still watches the daily loss limit (D-47).
    engine = await start(FakeExchange(), store, symbol_store, {})
    assert engine.traders == {} and engine.asset == "USDT"
    assert engine.snapshot()["symbols"] == []


async def test_position_without_a_stop_gets_an_emergency_stop(
    store: HaltStore, symbol_store: SymbolStore
) -> None:
    exchange = FakeExchange()
    open_long(exchange)
    await start(exchange, store, symbol_store)
    assert len(exchange.calls(*ALGO)) == 1
    assert exchange.position_amount == D("0.083")


async def test_halt_record_starts_halted_and_flattens(
    store: HaltStore, symbol_store: SymbolStore
) -> None:
    store.save(HaltRecord(reason="test halt", halted_at="2026-10-16T00:00:00+00:00"))
    exchange = FakeExchange()
    open_long(exchange)
    exchange.add_stop("SELL", "59400")
    engine = await start(exchange, store, symbol_store)
    assert engine.guard.halted
    assert exchange.position_amount == 0
    assert exchange.algo_orders == []


# Events that call for a risk check (D-30)


async def test_reconnect_and_funding_request_a_check(
    store: HaltStore, symbol_store: SymbolStore
) -> None:
    engine = await start(FakeExchange(), store, symbol_store)
    engine.on_event(AccountUpdate(event_time=0, reason="ORDER", balances=(), positions=()))
    assert not engine.checker.requested
    engine.on_event(AccountUpdate(event_time=0, reason="FUNDING_FEE", balances=(), positions=()))
    assert engine.checker.requested

    engine = await start(FakeExchange(), store, symbol_store)
    engine.on_event(UserStreamConnected())
    assert engine.checker.requested


def order_update(execution_type: str, symbol: str = "BTCUSDT") -> OrderUpdate:
    o = {"s": symbol, "c": "qd_e_BTCUSDT_1_00", "S": "BUY", "o": "MARKET", "x": execution_type}
    o |= {"X": "FILLED", "i": 7, "ps": "BOTH", "R": False, "q": "0.083", "p": "0", "sp": "0"}
    o |= {"ap": "60000", "l": "0.083", "L": "60000", "z": "0.083", "rp": "0", "t": 1, "T": 1}
    return parse_order_update({"E": 1, "o": o})


async def test_fills_and_reconnects_ask_for_the_trades(
    store: HaltStore, symbol_store: SymbolStore
) -> None:
    # D-51: read again from the exchange, never added from the event.
    engine = await start(FakeExchange(), store, symbol_store)
    engine.on_event(order_update("NEW"))
    assert not engine.history._wake.is_set()
    engine.on_event(order_update("TRADE", "ETHUSDT"))
    assert engine.history._requested == {"ETHUSDT"} and not engine.history._requested_all
    engine.on_event(UserStreamConnected())
    assert engine.history._requested_all
    assert engine.trades() is None  # until the first read


async def test_closed_position_requests_a_check_and_cancels_its_stop(
    store: HaltStore, symbol_store: SymbolStore
) -> None:
    exchange = FakeExchange()
    engine = await start(exchange, store, symbol_store)
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


async def test_snapshot_of_a_protected_position(
    store: HaltStore, symbol_store: SymbolStore
) -> None:
    exchange = FakeExchange()
    open_long(exchange)  # 0.083 at 60000
    exchange.add_stop("SELL", "59400")
    engine = await start(exchange, store, symbol_store)
    await resync(exchange.client(), engine.state)
    engine.mark_prices["BTCUSDT"] = D("60100.5")

    snapshot = engine.snapshot()
    json.dumps(snapshot)  # JSON-ready as it is
    assert snapshot["network"] == "testnet"
    assert snapshot["started_at"] == iso_utc(engine.started_at)
    assert "strategy" not in snapshot  # per symbol since D-47
    assert snapshot["halt"] == {"halted": False, "reason": None, "since": None}
    assert snapshot["daily_loss"] is None  # until the first check
    assert snapshot["balances"] == {"USDT": "5000"}
    (symbol,) = snapshot["symbols"]
    assert symbol["symbol"] == "BTCUSDT" and symbol["halted"] is None
    assert symbol["leverage"] == 5
    assert symbol["strategy"] is None
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
        "unrealized_pnl": "8.34",
        "loss_with_unrealized": "-8.34",
    }


async def test_daily_loss_shows_the_unrealized_total_for_display_only(
    store: HaltStore, symbol_store: SymbolStore
) -> None:
    flat = await start(FakeExchange(), store, symbol_store)
    flat.checker.latest = DAILY
    daily = flat.snapshot()["daily_loss"]
    assert (daily["unrealized_pnl"], daily["loss_with_unrealized"]) == (None, None)

    exchange = FakeExchange(other_symbols={"ETHUSDT": 3})
    open_long(exchange)  # 0.083 at 60000
    exchange.add_stop("SELL", "59400")
    exchange.add_position("ETHUSDT", "-1.5", "2500")
    exchange.add_stop("BUY", "2600", "qd_s_ETHUSDT_1_00", symbol="ETHUSDT")
    symbols = {**SYMBOLS, "ETHUSDT": SymbolConfig("ETHUSDT", 3)}
    engine = await start(exchange, store, symbol_store, symbols)
    await resync(exchange.client(), engine.state)
    engine.checker.latest = DailyLoss(base=D(5000), loss=D("12.5"), limit=D(150))

    engine.mark_prices["BTCUSDT"] = D("60100.5")  # +8.3415
    daily = engine.snapshot()["daily_loss"]
    # A total missing one position would mislead: none until every mark price is in.
    assert (daily["unrealized_pnl"], daily["loss_with_unrealized"]) == (None, None)

    engine.mark_prices["ETHUSDT"] = D("2510")  # the short loses 15
    daily = engine.snapshot()["daily_loss"]
    assert daily["unrealized_pnl"] == "-6.66"  # -6.6585
    assert daily["loss_with_unrealized"] == "19.16"  # 12.5 + 6.6585
    assert daily["loss"] == "12.5"  # what the limit and the kill switch count (D-24)


STEP = 4 * 60 * 60_000
OPEN_NOW = 1_791_993_600_000  # the 4h candle still open at FakeExchange's server time


def with_klines(exchange: FakeExchange) -> FakeExchange:
    exchange.klines = [
        [t, "60000", "60100", "59900", "60050", "1", t + STEP - 1]
        for t in range(OPEN_NOW - 6 * STEP, OPEN_NOW + 1, STEP)
    ]
    return exchange


DONCHIAN = strategy_config("donchian", "4h", {"entry_period": 3, "exit_period": 2}, "test")
BTC_DONCHIAN = SymbolConfig("BTCUSDT", 5, DONCHIAN)
SETTING = {
    "name": "donchian",
    "interval": "4h",
    "params": {"entry_period": "3", "exit_period": "2"},
}


async def test_strategy_warms_up_at_startup_and_shows_in_the_snapshot(
    store: HaltStore, symbol_store: SymbolStore, tmp_path: Path
) -> None:
    exchange = with_klines(FakeExchange())
    symbols = {"BTCUSDT": BTC_DONCHIAN}

    engine = await start(exchange, store, symbol_store, symbols, forward_dir=tmp_path)

    (warm_up,) = exchange.calls("GET", "/fapi/v1/klines")
    assert (warm_up["interval"], warm_up["limit"]) == ("4h", "6")  # 4 candles and 2 spare
    runner = engine.runners["BTCUSDT"]
    assert runner._record is not None
    assert runner._record.path == tmp_path / "BTCUSDT_4h_donchian_entry_period=3_exit_period=2.csv"
    snapshot = engine.snapshot()
    # The latest warm-up candle is decided on once the engine runs.
    assert snapshot["symbols"][0]["strategy"] == {
        **SETTING,
        "last_candle": None,
        "last_signal": None,
        "stopped": None,
    }

    last = kline_from_row(exchange.klines[-2], "BTCUSDT", "4h")
    runner.last_candle = last
    runner.last_signal = Signal(last.open_time, "enter_long", D(59900), "entered 0.083 at ~60000")
    runner.stopped = "RuntimeError: a bug"
    shown = engine.snapshot()["symbols"][0]["strategy"]
    json.dumps(shown)
    assert shown == {
        **SETTING,
        "last_candle": {"open_time": "2026-10-14T12:00:00+00:00", "close": "60050"},
        "last_signal": {
            "candle_open_time": "2026-10-14T12:00:00+00:00",
            "action": "enter_long",
            "stop_price": "59900",
            "outcome": "entered 0.083 at ~60000",
        },
        "stopped": "RuntimeError: a bug",
    }


async def test_stop_waits_for_a_kill_switch_in_progress(
    store: HaltStore, symbol_store: SymbolStore
) -> None:
    engine = await start(FakeExchange(), store, symbol_store)
    async with engine.guard._kill_lock:  # as a running kill switch holds it
        stopping = asyncio.create_task(engine.stop())
        await asyncio.sleep(0.01)
        assert not engine.stop_requested.is_set()
    await asyncio.wait_for(stopping, 1)
    assert engine.stop_requested.is_set()


def test_amounts_are_never_in_exponent_notation() -> None:
    assert fixed_point(D("0E-8")) == "0.00000000"
    assert fixed_point(D("1E+3")) == "1000"


async def test_emergency_close_halts_and_shows_in_the_snapshot(
    store: HaltStore, symbol_store: SymbolStore
) -> None:
    exchange = FakeExchange()
    open_long(exchange)
    exchange.add_stop("SELL", "59400")
    engine = await start(exchange, store, symbol_store)
    assert await engine.emergency_close() is True
    assert exchange.position_amount == 0
    halt = engine.snapshot()["halt"]
    assert halt["halted"] is True
    assert "emergency close requested by the user" in halt["reason"]
    assert halt["since"]
    assert await engine.lift_halt() is True
    assert engine.snapshot()["halt"]["halted"] is False


# Symbol changes from clients (D-47)

DONCHIAN_ARGS = {
    "name": "donchian",
    "interval": "4h",
    "params": {"entry_period": "3", "exit_period": "2"},
}


class FailingStore(SymbolStore):
    def save(self, symbols: object, paused: object = None) -> None:
        raise PermissionError("symbols.json is open in another program")


async def forever() -> None:
    await asyncio.Event().wait()


async def test_add_a_symbol(store: HaltStore, symbol_store: SymbolStore) -> None:
    exchange = with_klines(FakeExchange(leverage=3))
    engine = await start(exchange, store, symbol_store, {})

    done = await engine.set_symbol({"symbol": "BTCUSDT", "leverage": 2, "strategy": DONCHIAN_ARGS})

    assert done == "BTCUSDT added: 2x, donchian 4h (entry_period=3, exit_period=2)"
    added = SymbolConfig("BTCUSDT", 2, DONCHIAN)
    assert exchange.leverage == 2
    assert symbol_store.load() == {"BTCUSDT": added}
    assert engine.symbols == {"BTCUSDT": added}
    assert list(engine.traders) == ["BTCUSDT"]
    assert list(engine.state.symbols) == ["BTCUSDT"]
    assert engine.guard._traders == [engine.traders["BTCUSDT"]]  # the kill switch covers it
    assert engine.runners["BTCUSDT"]._pending is not None  # decides once running
    shown = engine.snapshot()["symbols"][0]
    assert (shown["leverage"], shown["strategy"]["name"]) == (2, "donchian")


async def test_adding_a_symbol_with_a_position_is_refused(
    store: HaltStore, symbol_store: SymbolStore
) -> None:
    exchange = FakeExchange(leverage=3)
    open_long(exchange)
    engine = await start(exchange, store, symbol_store, {})
    with pytest.raises(SymbolChangeRefused, match="open LONG position 0.083"):
        await engine.set_symbol({"symbol": "BTCUSDT", "leverage": 2})
    assert engine.symbols == {} and engine.traders == {}
    assert symbol_store.load() is None
    assert exchange.calls(*LEVERAGE) == []


@pytest.mark.parametrize(
    ("exchange", "message"),
    [
        (FakeExchange(symbol="ETHUSDT"), "BTCUSDT is not in exchangeInfo"),
        (FakeExchange(contract_type="CURRENT_QUARTER"), "only trading perpetuals"),
        (FakeExchange(status="SETTLING"), "only trading perpetuals"),
        (FakeExchange(margin_asset="USDC"), "margined in USDC"),
    ],
)
async def test_symbols_the_engine_cant_trade_are_refused(
    store: HaltStore, symbol_store: SymbolStore, exchange: FakeExchange, message: str
) -> None:
    engine = await start(exchange, store, symbol_store, {})
    with pytest.raises(SymbolChangeRefused, match=message):
        await engine.set_symbol({"symbol": "BTCUSDT", "leverage": 2})
    assert engine.symbols == {} and symbol_store.load() is None


async def test_at_most_ten_symbols(store: HaltStore, symbol_store: SymbolStore) -> None:
    engine = await start(FakeExchange(), store, symbol_store, {})
    engine.symbols = {f"COIN{i}USDT": SymbolConfig(f"COIN{i}USDT", 2) for i in range(10)}
    with pytest.raises(SymbolChangeRefused, match="at most 10 symbols"):
        await engine.set_symbol({"symbol": "BTCUSDT", "leverage": 2})


async def test_values_out_of_bounds_are_refused(
    store: HaltStore, symbol_store: SymbolStore
) -> None:
    engine = await start(FakeExchange(), store, symbol_store)
    with pytest.raises(ConfigError, match="leverage must be between 1 and 10"):
        await engine.set_symbol({"symbol": "BTCUSDT", "leverage": 11})
    assert engine.symbols == SYMBOLS


async def test_same_settings_change_nothing(store: HaltStore, symbol_store: SymbolStore) -> None:
    exchange = FakeExchange()
    engine = await start(exchange, store, symbol_store)
    requests = len(exchange.requests)
    args = {"symbol": "BTCUSDT", "leverage": 5, "strategy": None}
    assert await engine.set_symbol(args) == "BTCUSDT unchanged"
    assert len(exchange.requests) == requests
    assert symbol_store.load() is None


async def test_new_leverage_keeps_the_strategy_running(
    store: HaltStore, symbol_store: SymbolStore
) -> None:
    exchange = with_klines(FakeExchange())
    engine = await start(exchange, store, symbol_store, {"BTCUSDT": BTC_DONCHIAN})
    runner = engine.runners["BTCUSDT"]

    done = await engine.set_symbol({"symbol": "BTCUSDT", "leverage": 3, "strategy": DONCHIAN_ARGS})

    assert done.startswith("BTCUSDT changed: 3x")
    assert exchange.leverage == 3
    assert engine.traders["BTCUSDT"]._config.leverage == 3  # entries are sized with it
    assert engine.runners["BTCUSDT"] is runner  # no new warm-up, no repeated decision
    assert symbol_store.load() == {"BTCUSDT": SymbolConfig("BTCUSDT", 3, DONCHIAN)}


async def test_new_strategy_replaces_the_runner(
    store: HaltStore, symbol_store: SymbolStore, tmp_path: Path
) -> None:
    exchange = with_klines(FakeExchange())
    engine = await start(exchange, store, symbol_store, {"BTCUSDT": BTC_DONCHIAN}, tmp_path)
    old = asyncio.create_task(forever())
    engine._runner_tasks["BTCUSDT"] = old  # as if the engine were running
    other = {
        "name": "donchian",
        "interval": "1h",
        "params": {"entry_period": "4", "exit_period": "2"},
    }

    await engine.set_symbol({"symbol": "BTCUSDT", "leverage": 5, "strategy": other})

    assert old.cancelled()
    runner = engine.runners["BTCUSDT"]
    assert runner.strategy.interval == "1h"
    assert runner._pending is not None  # decides on the latest candle once it runs
    # A new setting gets its own record (D-47).
    assert runner._record is not None
    assert runner._record.path == tmp_path / "BTCUSDT_1h_donchian_entry_period=4_exit_period=2.csv"
    assert exchange.calls(*LEVERAGE) == []


async def test_removing_the_strategy_stops_its_runner(
    store: HaltStore, symbol_store: SymbolStore
) -> None:
    engine = await start(
        with_klines(FakeExchange()), store, symbol_store, {"BTCUSDT": BTC_DONCHIAN}
    )
    await engine.set_symbol({"symbol": "BTCUSDT", "leverage": 5, "strategy": None})
    assert engine.runners == {}
    assert engine.snapshot()["symbols"][0]["strategy"] is None


async def test_change_with_an_open_position_is_refused(
    store: HaltStore, symbol_store: SymbolStore
) -> None:
    exchange = FakeExchange()
    open_long(exchange)
    exchange.add_stop("SELL", "59400")
    engine = await start(exchange, store, symbol_store)
    with pytest.raises(SymbolChangeRefused, match="change only while it is flat"):
        await engine.set_symbol({"symbol": "BTCUSDT", "leverage": 3})
    assert exchange.leverage == 5
    assert engine.symbols == SYMBOLS
    assert symbol_store.load() is None


async def test_failed_save_changes_nothing(store: HaltStore, tmp_path: Path) -> None:
    exchange = with_klines(FakeExchange())
    failing = FailingStore(tmp_path / "symbols.json")
    engine = await start(exchange, store, failing, {"BTCUSDT": BTC_DONCHIAN}, tmp_path)
    runner = engine.runners["BTCUSDT"]

    with pytest.raises(PermissionError):
        await engine.set_symbol({"symbol": "BTCUSDT", "leverage": 3, "strategy": None})

    assert [c["leverage"] for c in exchange.calls(*LEVERAGE)] == ["3", "5"]  # put back
    assert engine.symbols == {"BTCUSDT": BTC_DONCHIAN}
    assert engine.traders["BTCUSDT"]._config.leverage == 5
    assert engine.runners["BTCUSDT"] is runner

    with pytest.raises(PermissionError):
        await engine.remove_symbol({"symbol": "BTCUSDT"})
    assert list(engine.traders) == ["BTCUSDT"] and engine.runners["BTCUSDT"] is runner


async def test_failed_save_adds_nothing(store: HaltStore, tmp_path: Path) -> None:
    engine = await start(FakeExchange(), store, FailingStore(tmp_path / "symbols.json"), {})
    with pytest.raises(PermissionError):
        await engine.set_symbol({"symbol": "BTCUSDT", "leverage": 2})
    assert engine.symbols == {} and engine.traders == {} and engine.state.symbols == {}


async def test_remove_a_flat_symbol(store: HaltStore, symbol_store: SymbolStore) -> None:
    exchange = with_klines(FakeExchange())
    engine = await start(exchange, store, symbol_store, {"BTCUSDT": BTC_DONCHIAN})
    old = asyncio.create_task(forever())
    engine._runner_tasks["BTCUSDT"] = old

    assert await engine.remove_symbol({"symbol": "BTCUSDT"}) == "BTCUSDT removed"

    assert old.cancelled()
    assert engine.symbols == {} and engine.traders == {} and engine.runners == {}
    assert engine.state.symbols == {} and engine.guard._traders == []
    assert symbol_store.load() == {}
    assert engine.snapshot()["symbols"] == []
    await engine.on_change(PositionClosed("BTCUSDT"))  # a late event for it is ignored


async def test_remove_with_an_open_position_is_refused(
    store: HaltStore, symbol_store: SymbolStore
) -> None:
    exchange = FakeExchange()
    open_long(exchange)
    exchange.add_stop("SELL", "59400")
    engine = await start(exchange, store, symbol_store)
    with pytest.raises(SymbolChangeRefused, match="change only while it is flat"):
        await engine.remove_symbol({"symbol": "BTCUSDT"})
    assert list(engine.traders) == ["BTCUSDT"]
    assert symbol_store.load() is None
    with pytest.raises(SymbolChangeRefused, match="not one of the engine's symbols"):
        await engine.remove_symbol({"symbol": "ETHUSDT"})


async def test_other_symbols_are_left_alone(store: HaltStore, symbol_store: SymbolStore) -> None:
    exchange = with_klines(FakeExchange(other_symbols={"ETHUSDT": 3}))
    engine = await start(exchange, store, symbol_store, {"BTCUSDT": BTC_DONCHIAN})
    btc_trader, btc_runner = engine.traders["BTCUSDT"], engine.runners["BTCUSDT"]

    await engine.set_symbol({"symbol": "ETHUSDT", "leverage": 2})
    assert list(engine.traders) == ["BTCUSDT", "ETHUSDT"]
    assert exchange.other_symbols["ETHUSDT"] == 2
    assert [c["symbol"] for c in exchange.calls(*LEVERAGE)] == ["ETHUSDT"]
    assert engine.traders["BTCUSDT"] is btc_trader
    assert engine.runners == {"BTCUSDT": btc_runner}

    await engine.remove_symbol({"symbol": "ETHUSDT"})
    assert engine.traders == {"BTCUSDT": btc_trader}
    assert engine.runners == {"BTCUSDT": btc_runner}
    assert symbol_store.load() == {"BTCUSDT": BTC_DONCHIAN}


async def test_a_client_changes_symbols_over_the_connection(
    store: HaltStore, symbol_store: SymbolStore, tmp_path: Path
) -> None:
    exchange = FakeExchange(other_symbols={"ETHUSDT": 3})
    engine = await start(exchange, store, symbol_store)
    file = ConnectionFile(tmp_path / "connection.json")
    server = ClientServer(engine, LogBuffer(), connection_file=file, publish_interval=0.01)
    serving = asyncio.create_task(server.run())
    try:
        await asyncio.wait_for(server.listening.wait(), 2)
        token = json.loads(file.path.read_text())["token"]
        async with connect(f"ws://127.0.0.1:{server.port}") as ws:
            await ws.send(json.dumps({"type": "hello", "token": token}))

            async def send(command_id: int, name: str, args: object) -> dict[str, Any]:
                message = {"type": "command", "id": command_id, "name": name, "args": args}
                await ws.send(json.dumps(message))
                async with asyncio.timeout(2):
                    while (reply := json.loads(await ws.recv()))["type"] != "result":
                        pass
                return reply

            added = await send(1, "set_symbol", {"symbol": "ETHUSDT", "leverage": 2})
            too_high = await send(2, "set_symbol", {"symbol": "ETHUSDT", "leverage": 20})
            removed = await send(3, "remove_symbol", {"symbol": "ETHUSDT"})
    finally:
        serving.cancel()
        with suppress(asyncio.CancelledError):
            await serving
    assert (added["ok"], added["message"]) == (True, "ETHUSDT added: 2x, no strategy")
    assert too_high["ok"] is False and "leverage must be between 1 and 10" in too_high["message"]
    assert (removed["ok"], removed["message"]) == (True, "ETHUSDT removed")
    assert list(engine.traders) == ["BTCUSDT"]


# The user's pause and per-symbol close (D-50)

ORDER = ("POST", "/fapi/v1/order")
ETH = SymbolConfig("ETHUSDT", 3)


async def test_pause_refuses_entries_and_survives_a_restart(
    store: HaltStore, symbol_store: SymbolStore
) -> None:
    exchange = FakeExchange()
    engine = await start(exchange, store, symbol_store)

    assert await engine.pause_symbol({"symbol": "BTCUSDT"}) == (
        "BTCUSDT paused: no new entries until resumed"
    )

    since = engine.traders["BTCUSDT"].paused_at
    assert since is not None
    assert engine.snapshot()["symbols"][0]["paused_at"] == since
    assert symbol_store.load() == SYMBOLS  # the settings are unchanged
    assert symbol_store.load_paused() == {"BTCUSDT": since}
    with pytest.raises(TradingHalted, match="paused by the user"):
        await engine.traders["BTCUSDT"].open_position(Side.LONG, D(59400))
    assert exchange.calls(*ORDER) == []
    assert not engine.guard.halted  # the account trades on
    assert await engine.pause_symbol({"symbol": "BTCUSDT"}) == (
        f"BTCUSDT already paused since {since}"
    )

    restarted = await start(exchange, store, symbol_store, paused=symbol_store.load_paused())
    assert restarted.traders["BTCUSDT"].paused_at == since


async def test_pause_keeps_the_position_and_its_stop(
    store: HaltStore, symbol_store: SymbolStore
) -> None:
    exchange = FakeExchange()
    open_long(exchange)
    exchange.add_stop("SELL", "59400")
    engine = await start(exchange, store, symbol_store)
    await engine.pause_symbol({"symbol": "BTCUSDT"})
    assert exchange.position_amount == D("0.083")
    assert len(exchange.algo_orders) == 1
    assert exchange.calls(*ORDER) == []


async def test_resume_lifts_only_the_users_pause(
    store: HaltStore, symbol_store: SymbolStore
) -> None:
    exchange = FakeExchange()
    engine = await start(exchange, store, symbol_store)
    assert await engine.resume_symbol({"symbol": "BTCUSDT"}) == "BTCUSDT not paused"

    await engine.pause_symbol({"symbol": "BTCUSDT"})
    assert await engine.resume_symbol({"symbol": "BTCUSDT"}) == (
        "BTCUSDT resumed: new entries from its strategy's next signal"
    )
    assert engine.traders["BTCUSDT"].paused_at is None
    assert symbol_store.load_paused() == {}

    trader = engine.traders["BTCUSDT"]
    trader.halt("stop not confirmed")
    await engine.pause_symbol({"symbol": "BTCUSDT"})
    await engine.emergency_close()
    done = await engine.resume_symbol({"symbol": "BTCUSDT"})
    assert "still halted by the engine: stop not confirmed" in done
    assert "the kill switch still halts the account" in done
    assert trader.halted and engine.guard.halted


async def test_close_symbol_closes_with_reduce_only_and_pauses(
    store: HaltStore, symbol_store: SymbolStore
) -> None:
    exchange = FakeExchange()
    open_long(exchange)
    exchange.add_stop("SELL", "59400")
    engine = await start(exchange, store, symbol_store)

    done = await engine.close_symbol({"symbol": "BTCUSDT"})

    since = engine.traders["BTCUSDT"].paused_at
    assert done == f"BTCUSDT closed LONG 0.083; paused since {since}"
    (close,) = exchange.calls(*ORDER)
    assert (close["side"], close["reduceOnly"], close["quantity"]) == ("SELL", "true", "0.083")
    assert parse_client_id(close["newClientOrderId"]).role == Role.EXIT
    assert exchange.position_amount == 0
    assert exchange.algo_orders == []  # its stop is cancelled
    assert symbol_store.load_paused() == {"BTCUSDT": since}
    assert not engine.guard.halted

    # Flat and already paused: nothing to close, and the pause keeps its time.
    assert await engine.close_symbol({"symbol": "BTCUSDT"}) == (
        f"BTCUSDT had no position; paused since {since}"
    )
    assert len(exchange.calls(*ORDER)) == 1


async def test_close_symbol_leaves_other_symbols_trading(
    store: HaltStore, symbol_store: SymbolStore
) -> None:
    exchange = FakeExchange(other_symbols={"ETHUSDT": 3})
    open_long(exchange)
    exchange.add_stop("SELL", "59400")
    exchange.add_position("ETHUSDT", "1.5", "2500")
    exchange.add_stop("SELL", "2400", "qd_s_ETHUSDT_1_00", symbol="ETHUSDT")
    engine = await start(exchange, store, symbol_store, {**SYMBOLS, "ETHUSDT": ETH})

    await engine.close_symbol({"symbol": "BTCUSDT"})

    assert exchange.position_amount == 0
    assert [p["positionAmt"] for p in exchange.other_positions] == ["1.5"]
    assert [a["symbol"] for a in exchange.algo_orders] == ["ETHUSDT"]
    assert engine.traders["ETHUSDT"].paused_at is None
    assert symbol_store.load_paused() == {"BTCUSDT": engine.traders["BTCUSDT"].paused_at}


async def test_settings_changes_keep_the_pause_and_removal_forgets_it(
    store: HaltStore, symbol_store: SymbolStore
) -> None:
    exchange = FakeExchange()
    engine = await start(exchange, store, symbol_store)
    await engine.pause_symbol({"symbol": "BTCUSDT"})
    since = engine.traders["BTCUSDT"].paused_at

    await engine.set_symbol({"symbol": "BTCUSDT", "leverage": 3})
    assert engine.traders["BTCUSDT"].paused_at == since
    assert symbol_store.load_paused() == {"BTCUSDT": since}

    await engine.remove_symbol({"symbol": "BTCUSDT"})
    assert symbol_store.load_paused() == {}
    await engine.set_symbol({"symbol": "BTCUSDT", "leverage": 3})
    assert engine.traders["BTCUSDT"].paused_at is None


async def test_unknown_symbols_are_refused(store: HaltStore, symbol_store: SymbolStore) -> None:
    engine = await start(FakeExchange(), store, symbol_store)
    for command in (engine.pause_symbol, engine.close_symbol, engine.resume_symbol):
        with pytest.raises(SymbolChangeRefused, match="ETHUSDT is not one of the engine's"):
            await command({"symbol": "ETHUSDT"})
    assert symbol_store.load() is None


async def test_failed_save_of_a_pause_changes_nothing(store: HaltStore, tmp_path: Path) -> None:
    engine = await start(FakeExchange(), store, FailingStore(tmp_path / "symbols.json"))
    trader = engine.traders["BTCUSDT"]
    with pytest.raises(PermissionError):
        await engine.pause_symbol({"symbol": "BTCUSDT"})
    assert trader.paused_at is None

    trader.pause("2026-10-22T03:00:00+00:00")
    with pytest.raises(PermissionError):
        await engine.resume_symbol({"symbol": "BTCUSDT"})
    assert trader.paused_at == "2026-10-22T03:00:00+00:00"


async def test_a_pause_that_cant_be_saved_never_blocks_the_close(
    store: HaltStore, tmp_path: Path
) -> None:
    exchange = FakeExchange()
    open_long(exchange)
    exchange.add_stop("SELL", "59400")
    engine = await start(exchange, store, FailingStore(tmp_path / "symbols.json"))

    with pytest.raises(PauseNotSaved, match="closed LONG 0.083.*a restart would trade BTCUSDT"):
        await engine.close_symbol({"symbol": "BTCUSDT"})

    assert exchange.position_amount == 0
    assert engine.traders["BTCUSDT"].paused_at is not None  # holds until a restart
