import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing, asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from websockets.asyncio.server import Server, ServerConnection, serve

from quantdeck_engine.binance.rest import BinanceRestClient
from quantdeck_engine.binance.user_stream import (
    AccountUpdate,
    OrderUpdate,
    RawEvent,
    UserEvent,
    UserStreamConnected,
    parse_user_event,
    user_data_stream,
)
from quantdeck_engine.config import parse_settings
from quantdeck_engine.logs import setup_logging

SETTINGS = parse_settings({"BINANCE_API_KEY": "test-key", "BINANCE_API_SECRET": "test-secret"})

# Payload examples from the USDⓈ-M Futures User Data Streams docs.
ORDER_SAMPLE: dict[str, Any] = {
    "e": "ORDER_TRADE_UPDATE",
    "E": 1568879465651,
    "T": 1568879465650,
    "o": {
        "s": "BTCUSDT",
        "c": "TEST",
        "S": "SELL",
        "o": "TRAILING_STOP_MARKET",
        "f": "GTC",
        "q": "0.001",
        "p": "0",
        "ap": "0",
        "sp": "7103.04",
        "x": "NEW",
        "X": "NEW",
        "i": 8886774,
        "l": "0",
        "z": "0",
        "L": "0",
        "N": "USDT",
        "n": "0",
        "T": 1568879465650,
        "t": 0,
        "b": "0",
        "a": "9.91",
        "m": False,
        "R": False,
        "wt": "CONTRACT_PRICE",
        "ot": "TRAILING_STOP_MARKET",
        "ps": "LONG",
        "cp": False,
        "AP": "7476.89",
        "cr": "5.0",
        "pP": False,
        "si": 0,
        "ss": 0,
        "rp": "0",
        "V": "EXPIRE_TAKER",
        "pm": "OPPONENT",
        "gtd": 0,
        "er": "0",
    },
}

ACCOUNT_SAMPLE: dict[str, Any] = {
    "e": "ACCOUNT_UPDATE",
    "E": 1564745798939,
    "T": 1564745798938,
    "a": {
        "m": "ORDER",
        "B": [{"a": "USDT", "wb": "122624.12345678", "cw": "100.12345678", "bc": "50.12345678"}],
        "P": [
            {
                "s": "BTCUSDT",
                "pa": "0",
                "ep": "0.00000",
                "bep": "0",
                "cr": "200",
                "up": "0",
                "mt": "isolated",
                "iw": "0.00000000",
                "ps": "BOTH",
            }
        ],
    },
}


def expired(key: str) -> dict[str, Any]:
    return {"e": "listenKeyExpired", "E": 1736996475556, "listenKey": key}


# Parsing


def test_parse_order_update() -> None:
    event = parse_user_event(ORDER_SAMPLE)
    assert isinstance(event, OrderUpdate)
    assert event.event_time == 1568879465651
    assert (event.symbol, event.order_id, event.client_order_id) == ("BTCUSDT", 8886774, "TEST")
    assert (event.side, event.order_type, event.position_side) == (
        "SELL",
        "TRAILING_STOP_MARKET",
        "LONG",
    )
    assert (event.execution_type, event.status) == ("NEW", "NEW")
    assert event.quantity == Decimal("0.001")
    assert event.stop_price == Decimal("7103.04")
    assert event.filled_quantity == Decimal("0")
    assert (event.commission, event.commission_asset) == (Decimal("0"), "USDT")
    assert event.realized_profit == Decimal("0")
    assert (event.reduce_only, event.close_position) == (False, False)
    assert (event.trade_id, event.trade_time) == (0, 1568879465650)


def test_parse_order_update_without_commission_fields() -> None:
    order = {k: v for k, v in ORDER_SAMPLE["o"].items() if k not in ("N", "n")}
    event = parse_user_event({**ORDER_SAMPLE, "o": order})
    assert isinstance(event, OrderUpdate)
    assert (event.commission, event.commission_asset) == (Decimal("0"), None)


def test_parse_account_update() -> None:
    event = parse_user_event(ACCOUNT_SAMPLE)
    assert isinstance(event, AccountUpdate)
    assert (event.event_time, event.reason) == (1564745798939, "ORDER")
    (balance,) = event.balances
    assert balance.asset == "USDT"
    assert balance.wallet_balance == Decimal("122624.12345678")
    assert balance.cross_wallet_balance == Decimal("100.12345678")
    assert balance.balance_change == Decimal("50.12345678")
    (position,) = event.positions
    assert (position.symbol, position.margin_type, position.position_side) == (
        "BTCUSDT",
        "isolated",
        "BOTH",
    )
    assert position.amount == Decimal("0")
    assert position.entry_price == Decimal("0.00000")


def test_parse_funding_fee_update_without_positions() -> None:
    data = {
        **ACCOUNT_SAMPLE,
        "a": {"m": "FUNDING_FEE", "B": ACCOUNT_SAMPLE["a"]["B"], "S": "BTCUSDT"},
    }
    event = parse_user_event(data)
    assert isinstance(event, AccountUpdate)
    assert event.reason == "FUNDING_FEE"
    assert event.positions == ()


def test_unhandled_event_type_is_returned_raw() -> None:
    data = {"e": "ACCOUNT_CONFIG_UPDATE", "E": 1, "ac": {"s": "BTCUSDT", "l": 25}}
    assert parse_user_event(data) == RawEvent("ACCOUNT_CONFIG_UPDATE", data)


# Stream


class FakeListenKeyApi:
    """httpx MockTransport handler for POST/PUT /fapi/v1/listenKey."""

    def __init__(self, keys: list[str], post_failures: int = 0) -> None:
        self.keys = keys
        self.post_failures = post_failures
        self.posts = 0
        self.puts = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/fapi/v1/listenKey"
        if request.method == "PUT":
            self.puts += 1
            return httpx.Response(200, json={"listenKey": self.keys[0]})
        self.posts += 1
        if self.posts <= self.post_failures:
            return httpx.Response(503, text="Service Unavailable")
        index = min(self.posts - self.post_failures, len(self.keys)) - 1
        return httpx.Response(200, json={"listenKey": self.keys[index]})


@asynccontextmanager
async def fake_binance(
    api: FakeListenKeyApi, handler: Any
) -> AsyncIterator[tuple[BinanceRestClient, str]]:
    """A REST client backed by `api` and the ws root of a local server running `handler`."""
    server: Server
    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        transport = httpx.MockTransport(api)
        async with BinanceRestClient(SETTINGS, transport=transport) as client:
            yield client, f"ws://127.0.0.1:{port}"


async def collect(client: BinanceRestClient, ws_root: str, count: int) -> list[UserEvent]:
    events: list[UserEvent] = []
    stream = user_data_stream(client, ws_root, retry_delays=(0,))
    async with asyncio.timeout(5), aclosing(stream):
        async for event in stream:
            events.append(event)
            if len(events) == count:
                break
    return events


async def test_connects_on_private_route_and_yields_parsed_events() -> None:
    paths: list[str] = []

    async def handler(ws: ServerConnection) -> None:
        paths.append(ws.request.path)
        await ws.send(json.dumps(ORDER_SAMPLE))
        await ws.send(json.dumps(ACCOUNT_SAMPLE))
        await ws.wait_closed()

    api = FakeListenKeyApi(["lk-1"])
    async with fake_binance(api, handler) as (client, ws_root):
        events = await collect(client, ws_root, 3)

    assert [type(e) for e in events] == [UserStreamConnected, OrderUpdate, AccountUpdate]
    assert paths == ["/private/ws/lk-1"]


async def test_listen_key_expired_reconnects_with_a_new_key() -> None:
    paths: list[str] = []

    async def handler(ws: ServerConnection) -> None:
        paths.append(ws.request.path)
        if ws.request.path.endswith("lk-1"):
            await ws.send(json.dumps(expired("lk-1")))
        else:
            await ws.send(json.dumps(ORDER_SAMPLE))
        await ws.wait_closed()

    api = FakeListenKeyApi(["lk-1", "lk-2"])
    async with fake_binance(api, handler) as (client, ws_root):
        events = await collect(client, ws_root, 3)

    # The expiry itself is not passed on; the consumer sees a reconnect instead.
    assert [type(e) for e in events] == [UserStreamConnected, UserStreamConnected, OrderUpdate]
    assert paths == ["/private/ws/lk-1", "/private/ws/lk-2"]
    assert api.posts == 2


async def test_reconnects_after_the_server_closes() -> None:
    connections = 0

    async def handler(ws: ServerConnection) -> None:
        nonlocal connections
        connections += 1
        if connections == 1:
            await ws.close()  # like Binance's 24-hour disconnect
            return
        await ws.send(json.dumps(ORDER_SAMPLE))
        await ws.wait_closed()

    api = FakeListenKeyApi(["lk-1"])
    async with fake_binance(api, handler) as (client, ws_root):
        events = await collect(client, ws_root, 3)

    assert [type(e) for e in events] == [UserStreamConnected, UserStreamConnected, OrderUpdate]
    assert api.posts == 2  # a fresh key before every connection


async def test_retries_when_the_listen_key_request_fails(caplog: pytest.LogCaptureFixture) -> None:
    async def handler(ws: ServerConnection) -> None:
        await ws.wait_closed()

    api = FakeListenKeyApi(["lk-1"], post_failures=2)
    with caplog.at_level(logging.WARNING):
        async with fake_binance(api, handler) as (client, ws_root):
            events = await collect(client, ws_root, 1)

    assert events == [UserStreamConnected()]
    assert api.posts == 3
    assert "retrying" in caplog.text


async def test_keepalive_runs_while_connected() -> None:
    async def handler(ws: ServerConnection) -> None:
        await ws.wait_closed()

    api = FakeListenKeyApi(["lk-1"])
    async with fake_binance(api, handler) as (client, ws_root):
        stream = user_data_stream(client, ws_root, keepalive_seconds=0.02)
        async with aclosing(stream):
            assert isinstance(await anext(stream), UserStreamConnected)
            await asyncio.sleep(0.2)

    assert api.puts >= 2


async def test_unparsed_events_are_passed_on_raw(caplog: pytest.LogCaptureFixture) -> None:
    broken = {"e": "ORDER_TRADE_UPDATE", "E": 1, "o": {"s": "BTCUSDT"}}
    margin_call = {"e": "MARGIN_CALL", "E": 1, "cw": "3.16", "p": []}

    async def handler(ws: ServerConnection) -> None:
        await ws.send(json.dumps(broken))
        await ws.send(json.dumps(margin_call))
        await ws.wait_closed()

    api = FakeListenKeyApi(["lk-1"])
    with caplog.at_level(logging.WARNING):
        async with fake_binance(api, handler) as (client, ws_root):
            events = await collect(client, ws_root, 3)

    assert events[1:] == [
        RawEvent("ORDER_TRADE_UPDATE", broken),
        RawEvent("MARGIN_CALL", margin_call),
    ]
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "Could not parse ORDER_TRADE_UPDATE" in errors[0].getMessage()
    assert "MARGIN_CALL" in caplog.text


@pytest.mark.usefixtures("isolated_logging")
async def test_listen_key_never_reaches_the_log(tmp_path: Path) -> None:
    async def handler(ws: ServerConnection) -> None:
        if ws.request.path.endswith("lk-secret-1"):
            await ws.send(json.dumps(expired("lk-secret-1")))
        await ws.wait_closed()

    log_file = setup_logging(tmp_path)
    logging.getLogger().setLevel(logging.DEBUG)
    api = FakeListenKeyApi(["lk-secret-1", "lk-secret-2"])
    async with fake_binance(api, handler) as (client, ws_root):
        stream = user_data_stream(client, ws_root, keepalive_seconds=0.02)
        async with aclosing(stream):
            for _ in range(2):
                assert isinstance(await anext(stream), UserStreamConnected)
            await asyncio.sleep(0.1)

    text = log_file.read_text(encoding="utf-8")
    assert "User data stream connected" in text
    assert "PUT /fapi/v1/listenKey -> HTTP 200" in text
    assert "lk-secret" not in text
