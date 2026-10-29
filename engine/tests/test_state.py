from decimal import Decimal
from typing import Any

import httpx
from fake_exchange import SETTINGS, FakeExchange

from quantdeck_engine.binance.rest import BinanceRestClient
from quantdeck_engine.binance.user_stream import (
    AlgoUpdate,
    OrderUpdate,
    parse_account_update,
    parse_algo_update,
    parse_order_update,
    parse_user_event,
)
from quantdeck_engine.trading.state import AccountState, PositionClosed, PositionOpened, resync

D = Decimal

# ALGO_UPDATE example from the User Data Streams docs.
ALGO_SAMPLE: dict[str, Any] = {
    "e": "ALGO_UPDATE",
    "T": 1750515742297,
    "E": 1750515742303,
    "o": {
        "caid": "Q5xaq5EGKgXXa0fD7fs0Ip",
        "aid": 2148719,
        "at": "CONDITIONAL",
        "o": "TAKE_PROFIT",
        "s": "BNBUSDT",
        "S": "SELL",
        "ps": "BOTH",
        "f": "GTC",
        "q": "0.01",
        "X": "CANCELED",
        "ai": "",
        "ap": "0.00000",
        "aq": "0.00000",
        "act": "0",
        "tp": "750",
        "p": "750",
        "V": "EXPIRE_MAKER",
        "wt": "CONTRACT_PRICE",
        "pm": "NONE",
        "cp": False,
        "pP": False,
        "R": False,
        "tt": 0,
        "gtd": 0,
        "rm": "Reduce Only reject",
        "ia": False,
    },
}


def algo_event(status: str, client_algo_id: str = "qd_s_BTCUSDT_1_00") -> AlgoUpdate:
    o = {
        **ALGO_SAMPLE["o"],
        "s": "BTCUSDT",
        "o": "STOP_MARKET",
        "X": status,
        "caid": client_algo_id,
        "cp": True,
        "tp": "59400",
    }
    return parse_algo_update({**ALGO_SAMPLE, "o": o})


def order_event(status: str, client_id: str = "qd_e_BTCUSDT_1_00") -> OrderUpdate:
    o = {
        "s": "BTCUSDT",
        "c": client_id,
        "S": "BUY",
        "o": "MARKET",
        "q": "0.083",
        "p": "0",
        "ap": "0",
        "sp": "0",
        "x": "NEW",
        "X": status,
        "i": 1,
        "l": "0",
        "z": "0",
        "L": "0",
        "T": 1,
        "t": 0,
        "R": False,
        "ps": "BOTH",
        "rp": "0",
    }
    return parse_order_update({"e": "ORDER_TRADE_UPDATE", "E": 1, "o": o})


def account_event(amount: str, symbol: str = "BTCUSDT"):
    position = {
        "s": symbol,
        "pa": amount,
        "ep": "60000",
        "up": "0",
        "mt": "isolated",
        "ps": "BOTH",
    }
    balance = {"a": "USDT", "wb": "4950", "cw": "0", "bc": "0"}
    return parse_account_update(
        {"e": "ACCOUNT_UPDATE", "E": 1, "a": {"m": "ORDER", "B": [balance], "P": [position]}}
    )


def test_parse_algo_update_sample() -> None:
    event = parse_user_event(ALGO_SAMPLE)
    assert isinstance(event, AlgoUpdate)
    assert event.client_algo_id == "Q5xaq5EGKgXXa0fD7fs0Ip"
    assert event.order_type == "TAKE_PROFIT"
    assert event.status == "CANCELED"
    assert event.trigger_price == D(750)
    assert event.close_position is False
    assert event.reject_reason == "Reduce Only reject"


def test_algo_orders_are_open_only_while_new() -> None:
    state = AccountState(["BTCUSDT"])
    state.apply(algo_event("NEW"))
    assert list(state.symbols["BTCUSDT"].algo_orders) == ["qd_s_BTCUSDT_1_00"]
    state.apply(algo_event("TRIGGERING"))
    assert state.symbols["BTCUSDT"].algo_orders == {}


def test_orders_are_tracked_until_final() -> None:
    state = AccountState(["BTCUSDT"])
    state.apply(order_event("NEW"))
    assert "qd_e_BTCUSDT_1_00" in state.symbols["BTCUSDT"].orders
    state.apply(order_event("FILLED"))
    assert state.symbols["BTCUSDT"].orders == {}


def test_account_update_reports_position_changes() -> None:
    state = AccountState(["BTCUSDT"])
    assert state.apply(account_event("0.083")) == [PositionOpened("BTCUSDT", D("0.083"))]
    assert state.symbols["BTCUSDT"].entry_price == D(60000)
    assert state.wallet_balances["USDT"] == D(4950)
    assert state.apply(account_event("0.050")) == []
    assert state.apply(account_event("0")) == [PositionClosed("BTCUSDT")]


def test_untracked_symbols_are_ignored() -> None:
    state = AccountState(["BTCUSDT"])
    assert state.apply(account_event("1", symbol="ETHUSDT")) == []
    assert "ETHUSDT" not in state.symbols


def test_replaying_queued_events_after_a_snapshot_ends_in_the_final_state() -> None:
    # The stream connects, the snapshot shows the order already filled, then the events
    # queued since the connect arrive (D-13). The order's last event decides.
    state = AccountState(["BTCUSDT"])
    state.load_snapshot(balances=[], positions=[], open_orders=[], open_algo_orders=[])
    state.apply(order_event("NEW"))
    state.apply(order_event("FILLED"))
    assert state.symbols["BTCUSDT"].orders == {}


async def test_resync_loads_a_rest_snapshot() -> None:
    exchange = FakeExchange()
    exchange.position_amount = D("0.083")
    exchange.entry_price = D(60000)
    exchange.add_stop("SELL", "59400")
    state = AccountState(["BTCUSDT"])

    changes = await resync(exchange.client(), state)

    assert changes == [PositionOpened("BTCUSDT", D("0.083"))]
    btc = state.symbols["BTCUSDT"]
    assert btc.position_amount == D("0.083")
    assert btc.algo_orders["qd_s_BTCUSDT_1_00"].trigger_price == D(59400)
    assert state.wallet_balances["USDT"] == D(5000)


async def test_resync_reports_a_position_closed_while_disconnected() -> None:
    exchange = FakeExchange()
    state = AccountState(["BTCUSDT"])
    state.apply(account_event("0.083"))
    assert await resync(exchange.client(), state) == [PositionClosed("BTCUSDT")]


async def test_leverage_brackets_accepts_object_or_array() -> None:
    brackets = [{"bracket": 1}]
    for body in (
        {"symbol": "BTCUSDT", "brackets": brackets},
        [{"symbol": "ETHUSDT", "brackets": []}, {"symbol": "BTCUSDT", "brackets": brackets}],
    ):
        transport = httpx.MockTransport(lambda _request, body=body: httpx.Response(200, json=body))
        async with BinanceRestClient(SETTINGS, transport=transport) as client:
            assert await client.leverage_brackets("BTCUSDT") == brackets
