import asyncio
import logging
from decimal import Decimal
from pathlib import Path

import pytest
from fake_exchange import (
    BTC,
    RULES,
    TRADING,
    Failure,
    FakeExchange,
    api_error,
    income,
    no_sleep,
    open_long,
)

from quantdeck_engine.trading.balance import BaseBalance
from quantdeck_engine.trading.executor import SymbolTrader, TradingHalted
from quantdeck_engine.trading.halt import HaltRecord, HaltStore
from quantdeck_engine.trading.order_ids import ClientIdFactory, Role, parse_client_id
from quantdeck_engine.trading.risk import KILL_ATTEMPTS, LiftRefused, RiskGuard, loss_from_income
from quantdeck_engine.trading.rules import Side
from quantdeck_engine.trading.sizing import EntryRejected

D = Decimal

ORDER = ("POST", "/fapi/v1/order")
CANCEL_ALL = ("DELETE", "/fapi/v1/allOpenOrders")
CANCEL_ALL_ALGO = ("DELETE", "/fapi/v1/algoOpenOrders")


def make(exchange: FakeExchange, store: HaltStore) -> tuple[RiskGuard, SymbolTrader]:
    client = exchange.client()
    base = BaseBalance(client, "USDT")
    guard = RiskGuard(client, TRADING, base, store, sleep=no_sleep)
    trader = SymbolTrader(
        client,
        BTC,
        TRADING,
        RULES,
        base,
        guard,
        ClientIdFactory(),
        sleep=no_sleep,
    )
    guard.attach([trader])
    return guard, trader


@pytest.fixture
def store(tmp_path: Path) -> HaltStore:
    return HaltStore(tmp_path / "state" / "halt.json")


def lose(exchange: FakeExchange, loss: str) -> None:
    """Today: a realized loss plus 10 in commission, on a 5000 base."""
    exchange.wallet = D(5000) - D(loss)
    exchange.incomes = [
        income("REALIZED_PNL", str(-(D(loss) - 10))),
        income("COMMISSION", "-10"),
    ]


def with_protected_long(exchange: FakeExchange) -> None:
    open_long(exchange)
    exchange.add_stop("SELL", "59400")


# Today's loss (D-24)


def test_loss_counts_pnl_fees_and_funding_net_of_gains() -> None:
    incomes = [
        income("REALIZED_PNL", "-120"),
        income("REALIZED_PNL", "30"),  # gains offset losses
        income("COMMISSION", "-5"),
        income("FUNDING_FEE", "-2"),
        income("SPECIAL_FUNDING_FEE", "-1"),
        income("INSURANCE_CLEAR", "-3"),
        income("TRANSFER", "-1000"),  # not a trading result
        income("COMMISSION_REBATE", "4"),  # rebates don't make room
        income("COMMISSION", "-0.1", asset="BNB"),  # another asset
    ]
    assert loss_from_income(incomes, "USDT") == D(101)


def test_loss_is_negative_after_a_net_gain() -> None:
    assert loss_from_income([income("REALIZED_PNL", "80")], "USDT") == D(-80)
    assert str(loss_from_income([], "USDT")) == "0"


async def test_daily_loss_from_the_exchange(store: HaltStore) -> None:
    exchange = FakeExchange()
    lose(exchange, "100")
    daily = await make(exchange, store)[0].daily_loss()
    assert (daily.base, daily.loss, daily.limit, daily.room) == (D(5000), D(100), D(150), D(50))
    assert not daily.reached


async def test_unknown_income_type_is_warned_once(
    store: HaltStore, caplog: pytest.LogCaptureFixture
) -> None:
    exchange = FakeExchange()
    exchange.incomes = [income("NEW_KIND_OF_FEE", "-1")]
    guard = make(exchange, store)[0]
    with caplog.at_level(logging.WARNING):
        await guard.daily_loss()
        await guard.daily_loss()
    assert caplog.text.count("NEW_KIND_OF_FEE is unknown") == 1


# Entry gate (D-24)


async def test_entry_risk_is_the_smaller_of_per_trade_and_room(store: HaltStore) -> None:
    exchange = FakeExchange()
    guard = make(exchange, store)[0]
    assert await guard.entry_risk(D(50)) == D(50)
    lose(exchange, "120")
    assert await guard.entry_risk(D(50)) == D(30)


async def test_entry_gate_trims_the_quantity(store: HaltStore) -> None:
    exchange = FakeExchange()
    lose(exchange, "130")  # room 20
    plan = await make(exchange, store)[1].open_position(Side.LONG, D(59400))
    assert plan.quantity == D("0.033")


async def test_entry_at_the_limit_is_refused_and_fires_the_kill_switch(
    store: HaltStore,
) -> None:
    exchange = FakeExchange()
    guard, trader = make(exchange, store)
    lose(exchange, "150")
    with pytest.raises(TradingHalted):
        await trader.open_position(Side.LONG, D(59400))
    assert exchange.calls(*ORDER) == []
    await guard._pending_kill  # the kill switch runs once the trader's lock is free
    assert guard.halted
    assert store.load() is not None


async def test_entries_are_refused_while_halted(store: HaltStore) -> None:
    exchange = FakeExchange()
    guard, trader = make(exchange, store)
    await guard.trip("by hand")
    with pytest.raises(TradingHalted, match="by hand"):
        await trader.open_position(Side.LONG, D(59400))
    assert exchange.calls(*ORDER) == []


# Open positions at the entry gate (D-24)


async def test_room_counts_open_positions_on_other_symbols(store: HaltStore) -> None:
    exchange = FakeExchange()
    lose(exchange, "100")  # room 50
    exchange.add_position("ETHUSDT", "1", "3000")
    exchange.add_stop("SELL", "2980", "qd_s_ETHUSDT_1_00", symbol="ETHUSDT")
    guard = make(exchange, store)[0]
    # 20 to the stop, plus 1.49 to close at 2980
    assert await guard.open_risk() == D("21.49")
    assert await guard.entry_risk(D(50)) == D("28.51")


async def test_open_risk_takes_the_stop_that_fires_first(store: HaltStore) -> None:
    exchange = FakeExchange()
    exchange.add_position("ETHUSDT", "-2", "3000")  # short
    exchange.add_stop("BUY", "3060", "qd_s_ETHUSDT_1_00", symbol="ETHUSDT")
    exchange.add_stop("BUY", "3030", "qd_s_ETHUSDT_2_00", symbol="ETHUSDT")
    exchange.add_position("SOLUSDT", "10", "100")
    exchange.add_stop("SELL", "105", "qd_s_SOLUSDT_1_00", symbol="SOLUSDT")  # above entry
    guard = make(exchange, store)[0]
    # ETH: 60 to the 3030 stop plus 3.03 to close; SOL: no loss, 0.525 to close
    assert await guard.open_risk() == D("63.555")


async def test_entry_is_skipped_when_open_positions_leave_no_room(
    store: HaltStore, caplog: pytest.LogCaptureFixture
) -> None:
    exchange = FakeExchange()
    lose(exchange, "100")  # room 50
    exchange.add_position("ETHUSDT", "1", "3000")
    exchange.add_stop("SELL", "2950", "qd_s_ETHUSDT_1_00", symbol="ETHUSDT")  # 51.475 at risk
    guard, trader = make(exchange, store)
    with caplog.at_level(logging.WARNING):
        assert await trader.open_position(Side.LONG, D(59400)) is None
    assert exchange.calls(*ORDER) == []
    assert "no room left" in caplog.text
    assert not guard.halted  # below the limit: only this entry is skipped


async def test_a_position_without_a_stop_blocks_entries(
    store: HaltStore, caplog: pytest.LogCaptureFixture
) -> None:
    exchange = FakeExchange()
    exchange.add_position("ETHUSDT", "1", "3000")
    trader = make(exchange, store)[1]
    with caplog.at_level(logging.WARNING):
        assert await trader.open_position(Side.LONG, D(59400)) is None
    assert exchange.calls(*ORDER) == []
    assert "ETHUSDT LONG position has no exchange-side stop" in caplog.text


async def test_the_next_gate_counts_the_entry_just_made(store: HaltStore) -> None:
    exchange = FakeExchange()
    lose(exchange, "100")  # room 50
    guard, trader = make(exchange, store)
    assert await trader.open_position(Side.LONG, D(59400)) is not None
    # Its 0.083 at a 600 stop distance, plus the fee to close, now fills the room.
    with pytest.raises(EntryRejected, match="no room left"):
        await guard.entry_risk(D(50))


async def test_entries_take_turns_through_the_slot(store: HaltStore) -> None:
    guard = make(FakeExchange(), store)[0]
    steps: list[str] = []

    async def enter(name: str) -> None:
        async with guard.entry_slot():
            steps.append(f"{name} gate")
            await asyncio.sleep(0)  # an order request
            steps.append(f"{name} stop")

    await asyncio.gather(enter("BTCUSDT"), enter("ETHUSDT"))
    assert steps == ["BTCUSDT gate", "BTCUSDT stop", "ETHUSDT gate", "ETHUSDT stop"]


# Kill switch (D-27, D-29)


async def test_check_below_the_limit_does_nothing(store: HaltStore) -> None:
    exchange = FakeExchange()
    with_protected_long(exchange)
    lose(exchange, "149.99")
    guard = make(exchange, store)[0]
    await guard.check()
    assert not guard.halted
    assert exchange.position_amount != 0
    assert store.load() is None


async def test_kill_switch_closes_then_cancels_and_records(
    store: HaltStore, caplog: pytest.LogCaptureFixture
) -> None:
    exchange = FakeExchange()
    with_protected_long(exchange)
    exchange.open_orders = [{"clientOrderId": "x1", "type": "LIMIT", "side": "BUY"}]
    lose(exchange, "150")
    guard = make(exchange, store)[0]
    with caplog.at_level(logging.ERROR):
        await guard.check()

    assert guard.halted and "daily loss limit" in guard.halt_reason
    assert "KILL SWITCH" in caplog.text
    (close,) = exchange.calls(*ORDER)
    assert close["reduceOnly"] == "true" and close["side"] == "SELL"
    assert parse_client_id(close["newClientOrderId"]).role is Role.KILL
    assert exchange.position_amount == 0
    assert exchange.open_orders == [] and exchange.algo_orders == []
    # The stop stays until the position is closed.
    keys = [(m, p) for m, p, _ in exchange.requests]
    assert keys.index(ORDER) < keys.index(CANCEL_ALL_ALGO)
    assert keys.index(ORDER) < keys.index(("DELETE", "/fapi/v1/algoOrder"))
    record = store.load()
    assert record is not None and record.reason == guard.halt_reason and record.halted_at


async def test_kill_switch_that_cannot_close_keeps_the_stop(
    store: HaltStore, caplog: pytest.LogCaptureFixture
) -> None:
    exchange = FakeExchange()
    with_protected_long(exchange)
    exchange.fail(*ORDER, *[Failure(api_error(400, -1000, "rejected"))] * KILL_ATTEMPTS)
    guard = make(exchange, store)[0]
    with caplog.at_level(logging.CRITICAL):
        assert await guard.trip("by hand") is False
    assert len(exchange.calls(*ORDER)) == KILL_ATTEMPTS
    assert exchange.position_amount != 0
    assert len(exchange.algo_orders) == 1  # still protected
    assert exchange.calls(*CANCEL_ALL_ALGO) == []
    assert "COULD NOT FLATTEN BTCUSDT" in caplog.text
    assert guard.halted and store.load() is not None


async def test_kill_switch_retries_a_failed_close(store: HaltStore) -> None:
    exchange = FakeExchange()
    with_protected_long(exchange)
    exchange.fail(*ORDER, Failure(api_error(400, -1000, "rejected")))
    assert await make(exchange, store)[0].trip("by hand") is True
    assert exchange.position_amount == 0
    assert len(exchange.calls(*ORDER)) == 2


async def test_record_write_failure_still_flattens(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "state").write_text("a file where the directory should be")
    exchange = FakeExchange()
    with_protected_long(exchange)
    guard = make(exchange, HaltStore(tmp_path / "state" / "halt.json"))[0]
    with caplog.at_level(logging.CRITICAL):
        assert await guard.trip("by hand") is True
    assert exchange.position_amount == 0
    assert guard.halted
    assert "Could not write the halt record" in caplog.text


# Startup (D-27)


async def test_start_with_a_record_is_halted_and_flattens(store: HaltStore) -> None:
    record = HaltRecord(reason="fired yesterday", halted_at="2026-10-15T20:00:00+00:00")
    store.save(record)
    exchange = FakeExchange()
    with_protected_long(exchange)  # left over from a kill switch that couldn't finish
    guard = make(exchange, store)[0]
    await guard.start()
    assert guard.halt_reason == "fired yesterday"
    assert exchange.position_amount == 0
    assert store.load() == record  # kept as it was
    assert exchange.calls("GET", "/fapi/v1/income") == []


async def test_start_with_an_unreadable_record_is_halted(store: HaltStore) -> None:
    store.path.parent.mkdir(parents=True)
    store.path.write_text("{", encoding="utf-8")
    guard = make(FakeExchange(), store)[0]
    await guard.start()
    assert guard.halted


async def test_start_at_the_limit_fires_the_kill_switch(store: HaltStore) -> None:
    exchange = FakeExchange()
    lose(exchange, "150")
    guard = make(exchange, store)[0]
    await guard.start()
    assert guard.halted and store.load() is not None


async def test_start_below_the_limit_is_not_halted(store: HaltStore) -> None:
    guard = make(FakeExchange(), store)[0]
    await guard.start()
    assert not guard.halted and store.load() is None


# Lifting the halt (D-27, D-33)


async def test_lift_clears_the_halt_and_the_record(
    store: HaltStore, caplog: pytest.LogCaptureFixture
) -> None:
    exchange = FakeExchange()
    guard, trader = make(exchange, store)
    await guard.trip("by hand")
    assert guard.halted_at and store.load() is not None
    with caplog.at_level(logging.WARNING):
        assert await guard.lift() is True
    assert not guard.halted and guard.halted_at is None
    assert store.load() is None
    assert "halt lifted by the user" in caplog.text
    assert await trader.open_position(Side.LONG, D(59400)) is not None


async def test_lift_without_a_halt_does_nothing(store: HaltStore) -> None:
    guard = make(FakeExchange(), store)[0]
    assert await guard.lift() is False


async def test_lift_is_refused_while_the_limit_is_reached(store: HaltStore) -> None:
    exchange = FakeExchange()
    lose(exchange, "150")
    guard = make(exchange, store)[0]
    await guard.check()
    assert guard.halted
    with pytest.raises(LiftRefused, match="next UTC day"):
        await guard.lift()
    assert guard.halted and store.load() is not None


async def test_lift_after_a_halt_carried_over_from_an_earlier_day(store: HaltStore) -> None:
    store.save(HaltRecord(reason="fired yesterday", halted_at="2026-10-15T20:00:00+00:00"))
    guard = make(FakeExchange(), store)[0]
    await guard.start()
    assert guard.halted_at == "2026-10-15T20:00:00+00:00"
    assert await guard.lift() is True
    assert store.load() is None
