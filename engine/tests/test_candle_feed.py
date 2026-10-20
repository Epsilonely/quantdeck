import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from decimal import Decimal

import httpx
import pytest
from fake_stream_server import lingering_server, time_to_cancel

from quantdeck_engine.binance import streams
from quantdeck_engine.binance.candle_feed import ClosedCandleFeed, FeedStopped
from quantdeck_engine.binance.rest import BinanceRestClient
from quantdeck_engine.binance.streams import Kline, kline_stream
from quantdeck_engine.config import parse_settings

SETTINGS = parse_settings({"BINANCE_API_KEY": "test-key", "BINANCE_API_SECRET": "test-secret"})
MINUTE = 60_000
T0 = 1_788_220_800_000  # a whole minute
WARM = T0 + 10 * MINUTE + 30_000  # when the tests warm up: candle 8 closed 90 s before


def row(i: int) -> list[object]:
    open_time = T0 + i * MINUTE
    return [
        open_time,
        "100",
        "101",
        "99",
        "100",
        "1",
        open_time + MINUTE - 1,
        "100",
        1,
        "0",
        "0",
        "0",
    ]


def kline(i: int, *, closed: bool = True) -> Kline:
    return Kline(
        symbol="BTCUSDT",
        interval="1m",
        open_time=T0 + i * MINUTE,
        close_time=T0 + (i + 1) * MINUTE - 1,
        open=Decimal(100),
        high=Decimal(101),
        low=Decimal(99),
        close=Decimal(100),
        volume=Decimal(1),
        closed=closed,
    )


class Exchange:
    """REST klines and a scripted kline stream. One clock serves the feed and the server."""

    def __init__(self, candles: list[int], now_ms: int) -> None:
        self.rows = [row(i) for i in candles]
        self.now_ms = now_ms
        self.failures = 0  # klines requests to fail with HTTP 503
        self.events: asyncio.Queue[object] = asyncio.Queue()
        self.sleeps: list[float] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fapi/v1/time":
            return httpx.Response(200, json={"serverTime": self.now_ms})
        assert request.url.path == "/fapi/v1/klines"
        if self.failures:
            self.failures -= 1
            return httpx.Response(503, json={"code": -1001, "msg": "Service Unavailable."})
        params = request.url.params
        limit = int(params["limit"])
        existing = [r for r in self.rows if int(str(r[0])) <= self.now_ms]
        if "startTime" not in params:
            return httpx.Response(200, json=existing[-limit:])
        start = int(params["startTime"])
        end = int(params.get("endTime", self.now_ms))
        return httpx.Response(
            200, json=[r for r in existing if start <= int(str(r[0])) <= end][:limit]
        )

    async def stream(
        self, ws_root: str, symbol: str, interval: str, *, on_connect
    ) -> AsyncIterator[Kline]:
        while True:
            event = await self.events.get()
            if event == "connect":
                on_connect()
            elif event == "end":
                return
            else:
                assert isinstance(event, Kline)
                yield event

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def feed(self, *, grace: float = 3600.0) -> ClosedCandleFeed:
        client = BinanceRestClient(SETTINGS, transport=httpx.MockTransport(self.handler))
        return ClosedCandleFeed(
            client,
            "wss://example",
            "BTCUSDT",
            "1m",
            stream=self.stream,
            clock=lambda: self.now_ms / 1000,
            sleep=self.sleep,
            grace=grace,
        )


async def take(feed: ClosedCandleFeed, n: int) -> list[int]:
    """The candle indexes of the next `n` candles the feed hands out."""
    async with asyncio.timeout(2), aclosing(feed.candles()) as candles:
        return [(await anext(candles)).open_time // MINUTE - T0 // MINUTE for _ in range(n)]


def queue_reads() -> set[asyncio.Task[object]]:
    return {
        task
        for task in asyncio.all_tasks()
        if getattr(task.get_coro(), "__qualname__", None) == "Queue.get"
    }


async def test_warm_up_returns_the_last_closed_candles() -> None:
    # Candle 10 is open, 9 closed 30 s ago (REST's copy not trusted yet), 8 closed 90 s ago.
    exchange = Exchange(list(range(11)), now_ms=WARM)
    feed = exchange.feed()

    candles = await feed.warm_up(3)

    assert [c.open_time for c in candles] == [T0 + i * MINUTE for i in (6, 7, 8)]
    assert all(c.closed for c in candles)
    assert feed.last == candles[-1]


async def test_warm_up_skips_a_candle_closed_less_than_a_minute_ago() -> None:
    exchange = Exchange(list(range(10)), now_ms=T0 + 9 * MINUTE + 59_000)
    candles = await exchange.feed().warm_up(3)
    assert [c.open_time for c in candles] == [T0 + i * MINUTE for i in (5, 6, 7)]


async def test_a_connect_fills_from_rest_then_the_stream_continues_without_duplicates() -> None:
    exchange = Exchange(list(range(14)), now_ms=WARM)
    feed = exchange.feed()
    await feed.warm_up(3)  # through candle 8
    exchange.now_ms = T0 + 13 * MINUTE + 30_000  # 9 to 11 closed over a minute ago
    for event in ("connect", kline(11), kline(12)):
        exchange.events.put_nowait(event)

    assert await take(feed, 4) == [9, 10, 11, 12]


async def test_a_gap_before_a_closed_candle_is_filled_from_rest_first() -> None:
    exchange = Exchange(list(range(15)), now_ms=WARM)
    feed = exchange.feed()
    await feed.warm_up(3)
    exchange.now_ms = T0 + 14 * MINUTE + 30_000
    for event in (kline(9), kline(12)):  # 10 and 11 lost
        exchange.events.put_nowait(event)

    assert await take(feed, 4) == [9, 10, 11, 12]


async def test_a_gap_rest_cannot_fill_is_passed_over_once_a_later_candle_exists(
    caplog: pytest.LogCaptureFixture,
) -> None:
    exchange = Exchange([*range(10), 12], now_ms=WARM)  # no candles 10 and 11
    feed = exchange.feed()
    await feed.warm_up(3)
    exchange.now_ms = T0 + 14 * MINUTE + 30_000
    exchange.events.put_nowait(kline(12))

    with caplog.at_level(logging.WARNING):
        assert await take(feed, 2) == [9, 12]
    assert "no candles between" in caplog.text


async def test_the_feed_waits_and_retries_while_rest_fails() -> None:
    exchange = Exchange(list(range(14)), now_ms=WARM)
    feed = exchange.feed()
    await feed.warm_up(3)
    exchange.now_ms = T0 + 13 * MINUTE + 30_000
    exchange.failures = 2
    exchange.events.put_nowait("connect")

    assert await take(feed, 3) == [9, 10, 11]
    assert exchange.sleeps == [5.0, 5.0]


async def test_a_close_the_stream_never_sent_is_read_from_rest_later() -> None:
    exchange = Exchange(list(range(12)), now_ms=WARM)
    feed = exchange.feed(grace=10.0)
    await feed.warm_up(3)
    # Candle 9 closed 75 s ago, past REST's minute plus the 10 s grace; the stream sent
    # nothing.
    exchange.now_ms = T0 + 11 * MINUTE + 15_000

    assert await take(feed, 1) == [9]


async def test_updates_of_a_forming_candle_are_ignored() -> None:
    exchange = Exchange(list(range(11)), now_ms=WARM)
    feed = exchange.feed()
    await feed.warm_up(3)
    for event in (kline(9, closed=False), kline(9, closed=False), kline(9), kline(10)):
        exchange.events.put_nowait(event)

    assert await take(feed, 2) == [9, 10]


async def test_a_stream_that_ends_stops_the_feed() -> None:
    exchange = Exchange(list(range(11)), now_ms=WARM)
    feed = exchange.feed()
    await feed.warm_up(3)
    exchange.events.put_nowait("end")

    with pytest.raises(FeedStopped):
        await take(feed, 1)


async def test_cancelling_a_reader_closes_the_stream_within_the_close_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A symbol change waits for this while it holds the symbol's order lock (D-47); the
    # server holds on to the connection after the close handshake, as Binance does.
    monkeypatch.setattr(streams, "CLOSE_TIMEOUT", 0.2)
    connected = asyncio.Event()

    def stream(
        ws_root: str, symbol: str, interval: str, *, on_connect: Callable[[], None]
    ) -> AsyncIterator[Kline]:
        def connect() -> None:
            connected.set()
            on_connect()

        return kline_stream(ws_root, symbol, interval, on_connect=connect)

    async def read(feed: ClosedCandleFeed) -> None:
        async with aclosing(feed.candles()) as candles:
            async for _ in candles:
                pass

    exchange = Exchange([], now_ms=WARM)
    client = BinanceRestClient(SETTINGS, transport=httpx.MockTransport(exchange.handler))
    async with lingering_server() as server:
        feed = ClosedCandleFeed(client, server.url, "BTCUSDT", "1m", stream=stream)
        task = asyncio.create_task(read(feed))
        await connected.wait()
        elapsed = await time_to_cancel(task)

    assert 0.1 < elapsed < 1  # waited out the timeout, not the server


async def test_cancelling_a_reader_leaves_no_queue_read_pending() -> None:
    # A symbol change or removal cancels the reader while it waits for the stream (D-47).
    # A queue read left pending is destroyed minutes later, and asyncio logs an ERROR.
    exchange = Exchange(list(range(11)), now_ms=WARM)
    feed = exchange.feed()
    await feed.warm_up(3)
    exchange.events.put_nowait(kline(9))
    received = asyncio.Event()

    async def read() -> None:
        async with aclosing(feed.candles()) as candles:
            async for _ in candles:
                received.set()  # and on to wait for the next candle

    task = asyncio.create_task(read())
    async with asyncio.timeout(2):
        await received.wait()
    waiting = queue_reads()  # held, so garbage collection can't hide one left pending
    assert waiting

    task.cancel()
    await asyncio.wait({task})

    assert [get for get in waiting if not get.done()] == []


def test_warm_up_count_is_bounded() -> None:
    exchange = Exchange([], now_ms=T0)
    with pytest.raises(ValueError):
        asyncio.run(exchange.feed().warm_up(1500))
