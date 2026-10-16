import json
from contextlib import aclosing
from decimal import Decimal

from websockets.asyncio.server import ServerConnection, serve

from quantdeck_engine.binance.streams import (
    kline_stream,
    kline_stream_url,
    mark_price_stream_url,
    parse_kline,
    parse_mark_price,
)
from quantdeck_engine.config import MAINNET, TESTNET

SAMPLE = {
    "e": "kline",
    "E": 1638747660000,
    "s": "BTCUSDT",
    "k": {
        "t": 1638747660000,
        "T": 1638747719999,
        "s": "BTCUSDT",
        "i": "1m",
        "f": 100,
        "L": 200,
        "o": "0.0010",
        "c": "0.0020",
        "h": "0.0025",
        "l": "0.0015",
        "v": "1000",
        "n": 100,
        "x": False,
        "q": "1.0000",
        "V": "500",
        "Q": "0.500",
        "B": "123456",
    },
}


def test_kline_url_uses_market_route_and_lowercase_symbol() -> None:
    assert (
        kline_stream_url(MAINNET.ws, "BTCUSDT", "1m")
        == "wss://fstream.binance.com/market/ws/btcusdt@kline_1m"
    )
    assert (
        kline_stream_url(TESTNET.ws, "ETHUSDT", "15m")
        == "wss://demo-fstream.binance.com/market/ws/ethusdt@kline_15m"
    )


def test_parse_kline() -> None:
    k = parse_kline(json.dumps(SAMPLE))
    assert k.symbol == "BTCUSDT"
    assert k.interval == "1m"
    assert k.open_time == 1638747660000
    assert k.close_time == 1638747719999
    assert (k.open, k.high, k.low, k.close) == (
        Decimal("0.0010"),
        Decimal("0.0025"),
        Decimal("0.0015"),
        Decimal("0.0020"),
    )
    assert k.volume == Decimal("1000")
    assert k.closed is False


def test_parse_kline_ignores_unknown_fields() -> None:
    payload = {**SAMPLE, "st": 1}
    assert parse_kline(json.dumps(payload)).symbol == "BTCUSDT"


async def test_kline_stream_reconnects_after_server_closes() -> None:
    # Each connection sends one update and then closes, like Binance's 24-hour disconnect.
    connections = 0

    async def handler(ws: ServerConnection) -> None:
        nonlocal connections
        connections += 1
        await ws.send(json.dumps(SAMPLE))
        await ws.close()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        received = []
        async with aclosing(kline_stream(f"ws://127.0.0.1:{port}", "BTCUSDT", "1m")) as stream:
            async for k in stream:
                received.append(k)
                if len(received) == 3:
                    break

    assert len(received) == 3
    assert connections == 3


MARK_PRICE = {
    "stream": "btcusdt@markPrice@1s",
    "data": {
        "e": "markPriceUpdate",
        "E": 1562305380000,
        "s": "BTCUSDT",
        "p": "11794.15000000",
        "i": "11784.62659091",
        "P": "11784.25641265",
        "r": "0.00038167",
        "T": 1562306400000,
    },
}


def test_mark_price_url_combines_symbols_on_the_market_route() -> None:
    assert (
        mark_price_stream_url(TESTNET.ws, ["BTCUSDT", "ETHUSDT"])
        == "wss://demo-fstream.binance.com/market/stream?streams="
        "btcusdt@markPrice@1s/ethusdt@markPrice@1s"
    )


def test_parse_mark_price() -> None:
    update = parse_mark_price(json.dumps(MARK_PRICE))
    assert (update.symbol, update.price, update.event_time) == (
        "BTCUSDT",
        Decimal("11794.15000000"),
        1562305380000,
    )
