import asyncio
import json
from decimal import Decimal
from typing import Any

import httpx
from fake_exchange import Failure, FakeExchange, income

from quantdeck_engine.times import DAY_MS
from quantdeck_engine.trading.trades import (
    Fill,
    OrderRole,
    TradeHistory,
    TradeRole,
    order_trades,
    role_from_client_id,
)

D = Decimal

NOW_MS = 1_792_000_000_000
MINUTE = 60_000
TRADES = ("GET", "/fapi/v1/userTrades")
ORDERS = ("GET", "/fapi/v1/allOrders")
ALGOS = ("GET", "/fapi/v1/allAlgoOrders")


def fill(
    trade_id: int,
    order_id: int,
    side: str = "BUY",
    price: str = "60000.0",
    qty: str = "0.083",
    *,
    pnl: str = "0",
    fee: str = "1.992",
    fee_asset: str = "USDT",
    at: int = NOW_MS - MINUTE,
    symbol: str = "BTCUSDT",
) -> dict[str, Any]:
    """A `userTrades` row."""
    return {
        "symbol": symbol,
        "id": trade_id,
        "orderId": order_id,
        "side": side,
        "price": price,
        "qty": qty,
        "quoteQty": str(D(price) * D(qty)),
        "realizedPnl": pnl,
        "commission": fee,
        "commissionAsset": fee_asset,
        "time": at,
        "buyer": side == "BUY",
        "maker": False,
        "positionSide": "BOTH",
    }


def order(order_id: int, client_id: str, symbol: str = "BTCUSDT") -> dict[str, Any]:
    """An `allOrders` row."""
    return {"symbol": symbol, "orderId": order_id, "clientOrderId": client_id, "type": "MARKET"}


def history(exchange: FakeExchange, symbols: list[str] | None = None) -> TradeHistory:
    return TradeHistory(
        exchange.client(), lambda: symbols or ["BTCUSDT"], "USDT", clock=lambda: NOW_MS / 1000
    )


def rows(trades: TradeHistory) -> list[tuple[str, str, str]]:
    assert trades.payload is not None
    return [(t["symbol"], t["role"], t["side"]) for t in trades.payload["trades"]]


# Roles from client order IDs (D-21)


def test_roles_from_client_ids() -> None:
    assert role_from_client_id("qd_e_BTCUSDT_1759480000123_01") is TradeRole.ENTRY
    assert role_from_client_id("qd_x_BTCUSDT_1759480000123_01") is TradeRole.EXIT
    assert role_from_client_id("qd_s_BTCUSDT_1759480000123_01") is TradeRole.STOP
    assert role_from_client_id("qd_k_BTCUSDT_1759480000123_01") is TradeRole.KILL
    assert role_from_client_id("autoclose-1759480000123") is TradeRole.LIQUIDATION
    assert role_from_client_id("adl_autoclose") is TradeRole.ADL
    # A stop's triggered order may carry an ID the exchange made: found through its algo order.
    assert role_from_client_id("web_abc123") is None


# Rows


def test_an_orders_fills_make_one_row_newest_first() -> None:
    fills = [
        Fill.from_user_trade(r)
        for r in [
            fill(1, 10, at=NOW_MS - 10 * MINUTE),
            fill(2, 11, "SELL", "60000.0", "0.05", pnl="-5", fee="1.2", at=NOW_MS - MINUTE),
            fill(3, 11, "SELL", "60001.5", "0.033", pnl="-3.3", fee="0.0001", fee_asset="BNB"),
        ]
    ]
    roles = {("BTCUSDT", 10): OrderRole(TradeRole.ENTRY, "qd_e_BTCUSDT_1_00")}
    exit_row, entry_row = order_trades(fills, roles)

    assert (exit_row.order_id, exit_row.side, exit_row.fills) == (11, "SELL", 2)
    assert exit_row.time_ms == NOW_MS - MINUTE  # the first fill
    assert exit_row.quantity == D("0.083")
    # (3000.0 + 1980.0495) / 0.083 = 60000.596..., to the fills' 0.1
    assert exit_row.price == D("60000.6")
    assert exit_row.realized_pnl == D("-8.3")
    assert exit_row.fees == {"USDT": D("1.2"), "BNB": D("0.0001")}
    assert (exit_row.role, exit_row.client_id) == (TradeRole.OTHER, None)  # not known yet
    assert (entry_row.order_id, entry_row.role) == (10, TradeRole.ENTRY)

    shown = exit_row.to_json()
    json.dumps(shown)
    assert shown == {
        "time": "2026-10-14T17:45:40+00:00",
        "symbol": "BTCUSDT",
        "order_id": 11,
        "client_id": None,
        "role": "other",
        "side": "SELL",
        "quantity": "0.083",
        "price": "60000.6",
        "realized_pnl": "-8.3",
        "fees": {"BNB": "0.0001", "USDT": "1.2"},
        "fills": 2,
    }


# Reading the exchange (D-51)


async def test_fills_and_their_roles_from_the_exchange() -> None:
    exchange = FakeExchange()
    exchange.user_trades = [
        fill(1, 7, "BUY", at=NOW_MS - 2 * DAY_MS),
        fill(2, 8, "SELL", "61000.0", pnl="83", at=NOW_MS - DAY_MS),
        fill(3, 9, "BUY", at=NOW_MS - 8 * DAY_MS),  # older than the window
    ]
    exchange.past_orders = [
        order(7, "qd_e_BTCUSDT_1759480000123_00"),
        order(8, "qd_x_BTCUSDT_1759480000456_00"),
    ]
    trades = history(exchange)
    await trades.refresh()

    assert rows(trades) == [("BTCUSDT", "exit", "SELL"), ("BTCUSDT", "entry", "BUY")]
    assert trades.payload is not None
    assert trades.payload["totals"] == {"realized_pnl": "83", "fees": {"USDT": "3.984"}}
    assert (trades.payload["window_days"], trades.payload["asset"]) == (7, "USDT")
    (read,) = exchange.calls(*TRADES)
    assert int(read["endTime"]) - int(read["startTime"]) < 7 * DAY_MS
    (lookup,) = exchange.calls(*ORDERS)
    assert lookup["orderId"] == "7"  # from the oldest unknown order on

    await trades.refresh()
    assert len(exchange.calls(*ORDERS)) == 1  # a role is found once per order


async def test_a_stop_fill_is_found_through_its_algo_order() -> None:
    exchange = FakeExchange()
    exchange.user_trades = [fill(1, 7, "SELL", "59400.0", pnl="-50")]
    exchange.past_orders = [order(7, "ios_exchange_made_id")]
    # Placed 10 days ago with its entry, so only the second week back finds it.
    exchange.past_algo_orders = [
        {
            "symbol": "BTCUSDT",
            "clientAlgoId": "qd_s_BTCUSDT_1759480000123_00",
            "algoStatus": "FINISHED",
            "actualOrderId": "7",
            "createTime": NOW_MS - 10 * DAY_MS,
        }
    ]
    trades = history(exchange)
    await trades.refresh()

    assert rows(trades) == [("BTCUSDT", "stop", "SELL")]
    assert trades.payload is not None
    assert trades.payload["trades"][0]["client_id"] == "qd_s_BTCUSDT_1759480000123_00"
    assert len(exchange.calls(*ALGOS)) == 2


async def test_liquidations_and_unknown_orders() -> None:
    exchange = FakeExchange()
    exchange.user_trades = [fill(1, 7, "SELL"), fill(2, 8, "BUY", at=NOW_MS)]
    exchange.past_orders = [order(7, "autoclose-1792000000000"), order(8, "manual_order")]
    trades = history(exchange)
    await trades.refresh()

    assert rows(trades) == [("BTCUSDT", "other", "BUY"), ("BTCUSDT", "liquidation", "SELL")]
    assert trades.payload is not None
    assert trades.payload["trades"][0]["client_id"] == "manual_order"
    assert len(exchange.calls(*ALGOS)) == 13  # every week of the 90 days orders are kept

    await trades.refresh()
    assert len(exchange.calls(*ALGOS)) == 13  # not looked for again


async def test_a_symbol_the_engine_no_longer_has_is_found_from_income() -> None:
    exchange = FakeExchange()
    exchange.incomes = [
        {**income("COMMISSION", "-1"), "symbol": "ETHUSDT"},
        income("REALIZED_PNL", "5"),
    ]
    exchange.user_trades = [fill(1, 7, symbol="ETHUSDT")]
    exchange.past_orders = [order(7, "qd_e_ETHUSDT_1759480000123_00", "ETHUSDT")]
    trades = history(exchange)
    await trades.refresh()

    assert rows(trades) == [("ETHUSDT", "entry", "BUY")]
    (records,) = exchange.calls("GET", "/fapi/v1/income")
    assert records["incomeType"] == "COMMISSION"

    exchange.user_trades = []  # its fills left the window: it isn't read any more
    await trades.refresh()
    await trades.refresh()
    assert [c["symbol"] for c in exchange.calls(*TRADES)] == ["BTCUSDT", "ETHUSDT"] * 2 + [
        "BTCUSDT"
    ]


async def test_fills_leave_the_list_as_the_window_moves() -> None:
    exchange = FakeExchange()
    exchange.user_trades = [fill(1, 7, at=NOW_MS - 6 * DAY_MS)]
    exchange.past_orders = [order(7, "qd_e_BTCUSDT_1759480000123_00")]
    now = [NOW_MS]
    trades = TradeHistory(
        exchange.client(), lambda: ["BTCUSDT", "ETHUSDT"], "USDT", clock=lambda: now[0] / 1000
    )
    await trades.refresh()
    assert rows(trades) == [("BTCUSDT", "entry", "BUY")]

    now[0] += 2 * DAY_MS
    await trades.refresh(["ETHUSDT"])  # BTCUSDT isn't read again, but its fill is too old now
    assert rows(trades) == []


async def test_nothing_is_shown_until_every_symbol_is_read() -> None:
    exchange = FakeExchange()
    exchange.user_trades = [fill(1, 7)]
    exchange.past_orders = [order(7, "qd_e_BTCUSDT_1759480000123_00")]
    exchange.fail(*TRADES, Failure(httpx.ConnectError("down")))
    trades = history(exchange)

    await trades.refresh()
    assert trades.payload is None  # an empty list would claim there were no trades
    await trades.refresh(["BTCUSDT"])
    assert trades.payload is None  # one symbol read on request doesn't complete it
    await trades.refresh()
    assert rows(trades) == [("BTCUSDT", "entry", "BUY")]

    shown = trades.payload
    exchange.fail(*TRADES, Failure(httpx.ConnectError("down")))
    await trades.refresh()
    assert trades.payload is shown  # the last fills stay; same object, so not sent again
    await trades.refresh()
    assert trades.payload is shown  # unchanged


async def test_a_requested_symbol_is_read_after_a_delay() -> None:
    exchange = FakeExchange()
    delays: list[float] = []

    async def sleep(seconds: float) -> None:
        delays.append(seconds)

    trades = TradeHistory(
        exchange.client(),
        lambda: ["BTCUSDT", "ETHUSDT"],
        "USDT",
        clock=lambda: NOW_MS / 1000,
        interval=60,
        sleep=sleep,
    )
    task = asyncio.create_task(trades.run())
    try:
        for _ in range(1000):
            if len(exchange.calls(*TRADES)) == 2:
                break
            await asyncio.sleep(0.001)
        trades.request("BTCUSDT")
        trades.request("BTCUSDT")  # one read for requests close together
        for _ in range(1000):
            if len(exchange.calls(*TRADES)) == 3:
                break
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.01)
    finally:
        task.cancel()
    assert [c["symbol"] for c in exchange.calls(*TRADES)] == ["BTCUSDT", "ETHUSDT", "BTCUSDT"]
    assert delays == [3.0]


async def test_a_bug_never_stops_the_reads() -> None:
    exchange = FakeExchange()
    exchange.user_trades = [{"symbol": "BTCUSDT", "id": 1, "time": NOW_MS}]  # fields missing
    trades = TradeHistory(
        exchange.client(), lambda: ["BTCUSDT"], "USDT", clock=lambda: NOW_MS / 1000, interval=0
    )
    task = asyncio.create_task(trades.run())
    try:
        for _ in range(1000):
            if len(exchange.calls(*TRADES)) >= 2:
                break
            await asyncio.sleep(0.001)
    finally:
        task.cancel()
    assert len(exchange.calls(*TRADES)) >= 2
