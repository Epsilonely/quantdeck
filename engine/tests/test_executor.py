import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal

import httpx
import pytest
from fake_exchange import (
    Failure,
    FakeExchange,
    FixedGate,
    api_error,
    make_trader,
    open_long,
)

from quantdeck_engine.binance.rest import BinanceAPIError, outcome_unknown
from quantdeck_engine.times import day_start_ms
from quantdeck_engine.trading.balance import BaseBalance, base_from_income
from quantdeck_engine.trading.executor import (
    STOP_ATTEMPTS,
    OrderOutcomeUnclear,
    TradingHalted,
)
from quantdeck_engine.trading.order_ids import Role, parse_client_id
from quantdeck_engine.trading.rules import Side

D = Decimal

ORDER = ("POST", "/fapi/v1/order")
ALGO = ("POST", "/fapi/v1/algoOrder")


def roles(params: list[dict[str, str]], key: str) -> list[str]:
    return [parse_client_id(p[key]).role for p in params]


# Unknown outcomes (D-20)


@pytest.mark.parametrize(
    ("error", "unknown"),
    [
        (httpx.ReadTimeout("timeout"), True),
        (httpx.ConnectError("refused"), True),
        (BinanceAPIError(503, None, "Unknown error", "/p"), True),
        (BinanceAPIError(408, None, "timeout", "/p"), True),
        (BinanceAPIError(400, -1007, "Timeout waiting for response", "/p"), True),
        (BinanceAPIError(400, -2019, "Margin is insufficient.", "/p"), False),
        (BinanceAPIError(429, -1003, "Too many requests", "/p"), False),
    ],
)
def test_outcome_unknown(error: Exception, unknown: bool) -> None:
    assert outcome_unknown(error) is unknown


# Base balance (D-23)


def test_base_undoes_todays_income_for_the_asset() -> None:
    incomes = [
        {"asset": "USDT", "income": "-49.8"},
        {"asset": "USDT", "income": "-2.5"},
        {"asset": "USDT", "income": "100"},  # transfers count too
        {"asset": "BNB", "income": "-0.1"},
    ]
    assert base_from_income(D("5047.7"), incomes, "USDT") == D(5000)


def test_day_starts_at_utc_midnight() -> None:
    assert day_start_ms(1_759_480_000_123) == 1_759_449_600_000  # 2025-10-03 00:00 UTC


async def test_base_balance_pages_through_income_and_caches_per_day() -> None:
    exchange = FakeExchange(wallet=D(4000))
    exchange.incomes = [{"asset": "USDT", "income": "-1"}] * 1500
    now = [1_759_480_000.0]
    base = BaseBalance(exchange.client(), "USDT", clock=lambda: now[0])

    assert await base.get() == D(5500)
    assert [p["page"] for p in exchange.calls("GET", "/fapi/v1/income")] == ["1", "2"]
    assert await base.get() == D(5500)  # same day: not fetched again
    assert len(exchange.calls("GET", "/fapi/v1/income")) == 2

    now[0] += 24 * 60 * 60
    exchange.incomes = []
    assert await base.get() == D(4000)


# Startup (D-16, D-18, D-19, D-26)


async def test_prepare_applies_margin_type_and_leverage_when_flat() -> None:
    exchange = FakeExchange(margin_type="CROSSED", leverage=20)
    await make_trader(exchange).prepare()
    assert exchange.margin_type == "ISOLATED"
    assert exchange.leverage == 5


async def test_prepare_leaves_matching_settings_alone() -> None:
    exchange = FakeExchange()
    await make_trader(exchange).prepare()
    assert exchange.calls("POST", "/fapi/v1/marginType") == []
    assert exchange.calls("POST", "/fapi/v1/leverage") == []


async def test_prepare_cancels_leftover_orders_when_flat() -> None:
    exchange = FakeExchange()
    exchange.add_stop("SELL", "59400")
    exchange.open_orders = [{"clientOrderId": "x1", "type": "LIMIT", "side": "BUY"}]
    await make_trader(exchange).prepare()
    assert exchange.algo_orders == []
    assert exchange.open_orders == []


async def test_prepare_adopts_a_position_with_a_stop() -> None:
    exchange = FakeExchange()
    open_long(exchange)
    exchange.add_stop("SELL", "59400")
    trader = make_trader(exchange)
    await trader.prepare()
    assert exchange.calls(*ALGO) == []
    assert not trader.halted


async def test_prepare_places_an_emergency_stop(caplog: pytest.LogCaptureFixture) -> None:
    exchange = FakeExchange()
    open_long(exchange)
    with caplog.at_level(logging.ERROR):
        await make_trader(exchange).prepare()
    (stop,) = exchange.calls(*ALGO)
    # Risk 1% of 5000 = 50; 50 / 0.083 = 602.41 below the entry, rounded toward it.
    assert stop["triggerPrice"] == "59397.6"
    assert stop["side"] == "SELL"
    assert "no exchange-side stop" in caplog.text


async def test_prepare_ignores_a_stop_on_the_wrong_side() -> None:
    exchange = FakeExchange()
    open_long(exchange)
    exchange.add_stop("BUY", "61000")  # would close a short, not this long
    await make_trader(exchange).prepare()
    assert len(exchange.calls(*ALGO)) == 1


async def test_prepare_closes_when_the_emergency_stop_is_crossed() -> None:
    exchange = FakeExchange(mark=D(59000))  # already below 59397.6
    open_long(exchange)
    trader = make_trader(exchange)
    await trader.prepare()
    assert exchange.calls(*ALGO) == []
    (close,) = exchange.calls(*ORDER)
    assert close["reduceOnly"] == "true" and close["side"] == "SELL"
    assert exchange.position_amount == 0
    assert trader.halted


async def test_prepare_closes_when_the_emergency_stop_is_beyond_liquidation() -> None:
    exchange = FakeExchange()
    open_long(exchange)
    exchange.liquidation_price = D(59500)
    trader = make_trader(exchange)
    await trader.prepare()
    assert exchange.calls(*ALGO) == []
    assert exchange.position_amount == 0
    assert trader.halted


async def test_prepare_halts_on_mismatched_settings_but_still_protects() -> None:
    exchange = FakeExchange(leverage=20)
    open_long(exchange)
    trader = make_trader(exchange)
    await trader.prepare()
    assert trader.halted and "D-16" in trader.halt_reason
    assert exchange.leverage == 20  # never changed under an open position
    assert len(exchange.calls(*ALGO)) == 1


# Entries (D-17, D-18, D-19, D-21, D-22)


async def test_entry_then_stop_with_exact_parameters() -> None:
    exchange = FakeExchange()
    trader = make_trader(exchange)
    plan = await trader.open_position(Side.LONG, D(59400))

    assert plan is not None and plan.quantity == D("0.083")
    (entry,) = exchange.calls(*ORDER)
    assert entry["type"] == "MARKET"
    assert entry["side"] == "BUY"
    assert entry["quantity"] == "0.083"
    assert entry["reduceOnly"] == "false"
    assert entry["newOrderRespType"] == "RESULT"
    assert parse_client_id(entry["newClientOrderId"]).role is Role.ENTRY

    (stop,) = exchange.calls(*ALGO)
    assert stop["algoType"] == "CONDITIONAL"
    assert stop["type"] == "STOP_MARKET"
    assert stop["side"] == "SELL"
    assert stop["triggerPrice"] == "59400"
    assert stop["closePosition"] == "true"
    assert stop["workingType"] == "MARK_PRICE"
    assert stop["priceProtect"] == "false"
    assert "quantity" not in stop and "reduceOnly" not in stop
    assert parse_client_id(stop["clientAlgoId"]).role is Role.STOP


async def test_short_entry_uses_sell_and_a_buy_stop() -> None:
    exchange = FakeExchange()
    await make_trader(exchange).open_position(Side.SHORT, D(60600))
    assert exchange.calls(*ORDER)[0]["side"] == "SELL"
    assert exchange.calls(*ALGO)[0]["side"] == "BUY"
    assert exchange.position_amount == D("-0.083")


async def test_entry_sizes_from_the_base_balance() -> None:
    exchange = FakeExchange(wallet=D(4950))
    exchange.incomes = [{"asset": "USDT", "income": "-50"}]  # base 5000, not 4950
    plan = await make_trader(exchange).open_position(Side.LONG, D(59400))
    assert plan.quantity == D("0.083")


async def test_entry_risk_is_capped_by_the_gate() -> None:
    exchange = FakeExchange()
    # Room 20 instead of 50: 20 / 600 = 0.0333 -> 0.033.
    plan = await make_trader(exchange, FixedGate(D(20))).open_position(Side.LONG, D(59400))
    assert plan.quantity == D("0.033")


async def test_entry_rejected_by_sizing_places_nothing() -> None:
    exchange = FakeExchange()
    plan = await make_trader(exchange).open_position(Side.LONG, D(60100))  # wrong side
    assert plan is None
    assert exchange.calls(*ORDER) == []


class SlotGate(FixedGate):
    """Records whether the entry slot was held whenever something is noted."""

    def __init__(self) -> None:
        super().__init__()
        self.held = False
        self.seen: list[tuple[str, bool]] = []

    @asynccontextmanager
    async def entry_slot(self) -> AsyncIterator[None]:
        self.held = True
        try:
            yield
        finally:
            self.held = False

    async def entry_risk(self, per_trade: Decimal) -> Decimal:
        self.seen.append(("gate", self.held))
        return await super().entry_risk(per_trade)


async def test_entry_holds_the_slot_from_the_gate_through_the_stop() -> None:
    # The next entry's gate must see this one's position with its stop (D-24).
    exchange = FakeExchange()
    gate = SlotGate()

    def noted(what: str) -> httpx.Response:
        gate.seen.append((what, gate.held))
        if what == "entry":  # the fill the fake has just made
            client_id = exchange.calls(*ORDER)[-1]["newClientOrderId"]
            return httpx.Response(200, json=exchange.orders[client_id])
        return httpx.Response(200, json={})

    exchange.fail(*ORDER, Failure(lambda: noted("entry"), after=True))
    exchange.fail(*ALGO, Failure(lambda: noted("stop"), after=True))
    assert await make_trader(exchange, gate).open_position(Side.LONG, D(59400)) is not None
    assert gate.seen == [("gate", True), ("entry", True), ("stop", True)]
    assert not gate.held


async def test_entry_is_skipped_while_a_position_is_open() -> None:
    exchange = FakeExchange()
    open_long(exchange)
    assert await make_trader(exchange).open_position(Side.LONG, D(59400)) is None
    assert exchange.calls(*ORDER) == []


async def test_definite_entry_rejection_returns_none() -> None:
    exchange = FakeExchange()
    exchange.fail(*ORDER, Failure(api_error(400, -2019, "Margin is insufficient.")))
    assert await make_trader(exchange).open_position(Side.LONG, D(59400)) is None
    assert exchange.position_amount == 0
    assert exchange.calls(*ALGO) == []


async def test_halted_trader_refuses_entries() -> None:
    trader = make_trader(FakeExchange())
    trader.halt("test")
    with pytest.raises(TradingHalted):
        await trader.open_position(Side.LONG, D(59400))


# Stop failures (D-19)


async def test_stop_is_retried_with_the_same_id() -> None:
    exchange = FakeExchange()
    exchange.fail(*ALGO, Failure(api_error(400, -1021, "Timestamp outside recvWindow")))
    plan = await make_trader(exchange).open_position(Side.LONG, D(59400))
    assert plan is not None
    first, second = exchange.calls(*ALGO)
    assert first["clientAlgoId"] == second["clientAlgoId"]
    assert len(exchange.algo_orders) == 1


async def test_stop_placed_despite_a_lost_response_is_found_not_duplicated() -> None:
    exchange = FakeExchange()
    exchange.fail(*ALGO, Failure(httpx.ReadTimeout("lost"), after=True))
    plan = await make_trader(exchange).open_position(Side.LONG, D(59400))
    assert plan is not None
    assert len(exchange.calls(*ALGO)) == 1  # found open on the check before a retry
    assert len(exchange.algo_orders) == 1


async def test_stop_failing_every_attempt_closes_and_halts() -> None:
    exchange = FakeExchange()
    error = Failure(api_error(400, -4045, "Reach max stop order limit."))
    exchange.fail(*ALGO, *[error] * STOP_ATTEMPTS)
    trader = make_trader(exchange)
    assert await trader.open_position(Side.LONG, D(59400)) is None
    assert len(exchange.calls(*ALGO)) == STOP_ATTEMPTS
    assert roles(exchange.calls(*ORDER), "newClientOrderId") == [Role.ENTRY, Role.EXIT]
    assert exchange.calls(*ORDER)[1]["reduceOnly"] == "true"
    assert exchange.position_amount == 0
    assert trader.halted


async def test_stop_that_would_trigger_closes_without_retrying() -> None:
    exchange = FakeExchange()
    exchange.fail(*ALGO, Failure(api_error(400, -2021, "Order would immediately trigger.")))
    trader = make_trader(exchange)
    assert await trader.open_position(Side.LONG, D(59400)) is None
    assert len(exchange.calls(*ALGO)) == 1
    assert exchange.position_amount == 0


# Unknown entry outcomes (D-20)


async def test_executed_entry_with_a_lost_response_is_found_by_lookup() -> None:
    exchange = FakeExchange()
    exchange.fail(*ORDER, Failure(httpx.ReadTimeout("lost"), after=True))
    plan = await make_trader(exchange).open_position(Side.LONG, D(59400))
    assert plan is not None
    assert len(exchange.calls(*ORDER)) == 1  # never resent
    assert exchange.position_amount == D("0.083")
    assert len(exchange.algo_orders) == 1


async def test_entry_not_found_is_resent_once_with_the_same_id() -> None:
    exchange = FakeExchange()
    exchange.fail(*ORDER, Failure(api_error(503, None, "Unknown error")))
    plan = await make_trader(exchange).open_position(Side.LONG, D(59400))
    assert plan is not None
    first, second = exchange.calls(*ORDER)
    assert first["newClientOrderId"] == second["newClientOrderId"]
    assert exchange.position_amount == D("0.083")


async def test_entry_still_unclear_after_a_resend_halts() -> None:
    exchange = FakeExchange()
    timeout = Failure(httpx.ReadTimeout("lost"))
    exchange.fail(*ORDER, timeout, timeout)
    trader = make_trader(exchange)
    with pytest.raises(OrderOutcomeUnclear):
        await trader.open_position(Side.LONG, D(59400))
    assert len(exchange.calls(*ORDER)) == 2
    assert trader.halted


async def test_unclear_entry_that_did_fill_gets_an_emergency_stop() -> None:
    exchange = FakeExchange()
    exchange.fail(*ORDER, Failure(httpx.ReadTimeout("lost"), after=True))
    exchange.fail("GET", "/fapi/v1/order", Failure(httpx.ConnectError("down")))
    trader = make_trader(exchange)
    with pytest.raises(OrderOutcomeUnclear):
        await trader.open_position(Side.LONG, D(59400))
    assert trader.halted
    assert exchange.position_amount == D("0.083")
    (stop,) = exchange.calls(*ALGO)
    assert stop["triggerPrice"] == "59397.6"  # emergency stop: one risk amount away (D-26)


# Closing (D-18)


async def test_close_uses_reduce_only_and_cancels_the_stop() -> None:
    exchange = FakeExchange()
    trader = make_trader(exchange)
    await trader.open_position(Side.LONG, D(59400))
    await trader.close_position()
    close = exchange.calls(*ORDER)[-1]
    assert close["reduceOnly"] == "true"
    assert close["side"] == "SELL"
    assert close["quantity"] == "0.083"
    assert exchange.position_amount == 0
    assert exchange.algo_orders == []


async def test_close_when_a_stop_got_there_first() -> None:
    exchange = FakeExchange()
    trader = make_trader(exchange)
    await trader.open_position(Side.LONG, D(59400))

    def stop_fires_first() -> httpx.Response:
        exchange.position_amount = D(0)  # the stop filled just before our close arrived
        exchange.algo_orders.clear()
        return api_error(400, -2022, "ReduceOnly Order is rejected.")

    exchange.fail(*ORDER, Failure(stop_fires_first))
    await trader.close_position()  # no error: the position is gone either way
    assert exchange.position_amount == 0


async def test_close_splits_orders_above_the_market_max_quantity() -> None:
    exchange = FakeExchange()
    open_long(exchange, amount="250")
    exchange.add_stop("SELL", "50000")
    trader = make_trader(exchange)
    await trader.close_position()
    assert [c["quantity"] for c in exchange.calls(*ORDER)] == ["120", "120", "10"]
    assert exchange.position_amount == 0


async def test_on_position_closed_cancels_the_leftover_stop() -> None:
    exchange = FakeExchange()
    exchange.add_stop("SELL", "59400")
    await make_trader(exchange).on_position_closed()
    assert exchange.algo_orders == []


async def test_on_position_closed_keeps_the_stop_if_still_open() -> None:
    exchange = FakeExchange()
    open_long(exchange)
    exchange.add_stop("SELL", "59400")
    await make_trader(exchange).on_position_closed()
    assert len(exchange.algo_orders) == 1


async def test_stop_is_placed_even_if_the_mark_price_read_fails() -> None:
    exchange = FakeExchange()
    mark = ("GET", "/fapi/v1/premiumIndex")
    # The first read sizes the entry; the second, before the stop, fails.
    exchange.fail(
        *mark,
        Failure(lambda: exchange._route(mark, {})),
        Failure(httpx.ConnectError("down")),
    )
    plan = await make_trader(exchange).open_position(Side.LONG, D(59400))
    assert plan is not None
    assert len(exchange.algo_orders) == 1


async def test_entry_found_still_working_halts_and_protects() -> None:
    exchange = FakeExchange()

    def accepted_then_fills() -> httpx.Response:
        # The lookup sees the order accepted but not filled; it fills right after.
        open_long(exchange)
        return httpx.Response(200, json={"status": "NEW", "executedQty": "0"})

    exchange.fail(*ORDER, Failure(httpx.ReadTimeout("lost")))
    exchange.fail("GET", "/fapi/v1/order", Failure(accepted_then_fills))
    trader = make_trader(exchange)
    assert await trader.open_position(Side.LONG, D(59400)) is None
    assert trader.halted
    assert len(exchange.algo_orders) == 1  # the late fill got an emergency stop
