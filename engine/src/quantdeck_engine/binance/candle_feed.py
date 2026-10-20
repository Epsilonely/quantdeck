"""Closed candles for a strategy: in order, each once, and without a gap REST can fill (D-36).

The kline stream loses whatever Binance sends while it reconnects, and a strategy that
misses a closed candle decides differently from the backtest. So the feed

- loads the strategy's warm-up candles from REST at startup;
- fills from REST after every (re)connect, when a closed candle arrives after a gap, and
  when the next candle hasn't closed on the stream some time after it should have;
- waits and retries while REST fails, so the strategy never decides on a series with a
  hole in it. Open positions keep their exchange-side stops meanwhile (D-19).

REST takes a candle only once it closed a minute ago: on testnet, REST still showed a
smaller volume (once a different close) 5 to 10 s after a close that the stream had already
sent in full (binanceNotes.md). So after a reconnect near a close, the strategy can get the
candle up to about a minute late.

A candle missing even from REST counts as a real gap (the exchange produces no candles
while trading is down) once a later closed candle exists; it is logged and passed over,
as the backtest's data loader does (D-38).
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing, suppress
from itertools import pairwise

from quantdeck_engine.binance.rest import REQUEST_ERRORS, BinanceRestClient
from quantdeck_engine.binance.streams import (
    INTERVAL_MS,
    SETTLE_MS,
    Kline,
    kline_stream,
    settled_klines,
)
from quantdeck_engine.times import utc_text

logger = logging.getLogger(__name__)

REST_LIMIT = 1500  # candles per request, the maximum
GRACE = 5.0  # seconds past SETTLE_MS before asking REST for a close the stream didn't send
RETRY_DELAY = 5.0  # seconds between REST attempts

_CONNECTED = object()

StreamFactory = Callable[..., AsyncIterator[Kline]]


class FeedStopped(Exception):
    """The kline stream ended, which it never should."""


class ClosedCandleFeed:
    def __init__(
        self,
        client: BinanceRestClient,
        ws_root: str,
        symbol: str,
        interval: str,
        *,
        stream: StreamFactory = kline_stream,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        grace: float = GRACE,
        retry_delay: float = RETRY_DELAY,
    ) -> None:
        if interval not in INTERVAL_MS:
            raise ValueError(f"Unsupported interval {interval!r}")
        self._client = client
        self._ws_root = ws_root
        self.symbol = symbol
        self.interval = interval
        self._step = INTERVAL_MS[interval]
        self._stream = stream
        self._clock = clock
        self._sleep = sleep
        self._grace = grace
        self._retry_delay = retry_delay
        self.last: Kline | None = None  # the last candle handed out

    async def warm_up(self, count: int) -> list[Kline]:
        """The last `count` closed candles, oldest first; `candles()` continues after them.

        Request errors propagate: this runs at startup, which fails as a whole (D-31).
        """
        if not 1 <= count < REST_LIMIT:
            raise ValueError(f"Warm-up of {count} candles is outside 1 to {REST_LIMIT - 1}")
        rows = await self._client.klines(self.symbol, self.interval, limit=count + 2)
        server_ms = await self._client.server_time()
        candles = settled_klines(rows, self.symbol, self.interval, server_ms)[-count:]
        self._log_gaps(candles)
        if len(candles) < count:
            logger.warning(
                "%s %s: only %d of %d warm-up candles",
                self.symbol,
                self.interval,
                len(candles),
                count,
            )
        if candles:
            self.last = candles[-1]
        return candles

    async def candles(self) -> AsyncIterator[Kline]:
        """Closed candles, oldest first, forever. Wrap the call in `contextlib.aclosing`."""
        queue: asyncio.Queue[object] = asyncio.Queue()
        reader = asyncio.create_task(self._read(queue))
        try:
            # Connecting triggers a fill, which covers whatever closed since the warm-up.
            check_at = self._due()
            while True:
                if self._clock() >= check_at:
                    for candle in await self._fill(None):
                        yield candle
                    # Still overdue (REST doesn't have it yet): ask again after a pause.
                    check_at = max(self._due(), self._clock() + self._retry_delay)
                event = await self._next_event(queue, reader, until=check_at)
                if event is None:
                    continue  # time to check REST
                if event is _CONNECTED:
                    check_at = self._clock()
                    continue
                assert isinstance(event, Kline)
                if self.last is not None and event.open_time <= self.last.open_time:
                    continue  # already handed out, from REST
                if self.last is not None and event.open_time > self._expected():
                    for candle in await self._fill(event.open_time):
                        yield candle
                self._take(event)
                yield event
                check_at = self._due()
        finally:
            reader.cancel()
            with suppress(asyncio.CancelledError):
                await reader

    # Stream

    async def _read(self, queue: asyncio.Queue[object]) -> None:
        def connected() -> None:
            queue.put_nowait(_CONNECTED)

        stream = self._stream(self._ws_root, self.symbol, self.interval, on_connect=connected)
        async with aclosing(stream) as updates:
            async for update in updates:
                if update.closed:
                    queue.put_nowait(update)

    async def _next_event(
        self, queue: asyncio.Queue[object], reader: asyncio.Task[None], *, until: float
    ) -> object | None:
        """The next stream event, or None once the clock reaches `until`."""
        get = asyncio.ensure_future(queue.get())
        try:
            done, _ = await asyncio.wait(
                {get, reader},
                timeout=max(until - self._clock(), 0),
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            # On every way out, cancellation included (a symbol change stops the strategy,
            # D-47): a read left pending is destroyed later, and asyncio logs an ERROR.
            # A read that already returned ignores this; one that hadn't takes no item.
            get.cancel()
        if get in done:
            return get.result()
        if reader in done:
            reader.result()  # raises the stream's error, if it had one
            raise FeedStopped(f"{self.symbol} {self.interval} kline stream ended")
        return None

    # REST

    async def _fill(self, before: int | None) -> list[Kline]:
        """Closed candles from REST after the last one handed out, up to `before` (an open
        time, exclusive) or now. Retries until REST answers."""
        if self.last is None:
            return []
        start = self._expected()
        attempt = 0
        while True:
            try:
                fetched = await self._fetch(start, before)
                break
            except REQUEST_ERRORS as e:
                attempt += 1
                logger.warning(
                    "%s %s: candles since %s not read from REST (attempt %d: %s); the strategy "
                    "waits, retrying in %.0f s",
                    self.symbol,
                    self.interval,
                    utc_text(start),
                    attempt,
                    e,
                    self._retry_delay,
                )
                await self._sleep(self._retry_delay)
        if attempt:
            logger.info(
                "%s %s: REST answered after %d failed attempt(s)",
                self.symbol,
                self.interval,
                attempt,
            )
        taken = []
        for candle in fetched:
            if candle.open_time >= start:
                self._take(candle)
                taken.append(candle)
        if taken:
            logger.info(
                "%s %s: read %d closed candle(s) from REST, %s to %s",
                self.symbol,
                self.interval,
                len(taken),
                utc_text(taken[0].open_time),
                utc_text(taken[-1].open_time),
            )
        return taken

    async def _fetch(self, start: int, before: int | None) -> list[Kline]:
        candles: list[Kline] = []
        cursor = start
        while before is None or cursor < before:
            rows = await self._client.klines(
                self.symbol,
                self.interval,
                start_time=cursor,
                end_time=None if before is None else before - 1,
                limit=REST_LIMIT,
            )
            server_ms = await self._client.server_time()
            settled = settled_klines(rows, self.symbol, self.interval, server_ms)
            candles += settled
            if len(rows) < REST_LIMIT or len(settled) < len(rows):
                break
            cursor = int(rows[-1][0]) + self._step
        return candles

    # Bookkeeping

    def _take(self, candle: Kline) -> None:
        if self.last is not None and candle.open_time > self._expected():
            logger.warning(
                "%s %s: no candles between %s and %s UTC, even from REST",
                self.symbol,
                self.interval,
                utc_text(self.last.open_time),
                utc_text(candle.open_time),
            )
        self.last = candle

    def _expected(self) -> int:
        assert self.last is not None
        return self.last.open_time + self._step

    def _due(self) -> float:
        """When to ask REST if the stream hasn't sent the next candle: once REST's copy can
        be trusted, plus the grace. Without a candle to follow yet, the first closed one on
        the stream sets it."""
        if self.last is None:
            return self._clock() + self._retry_delay
        return (self._expected() + self._step + SETTLE_MS) / 1000 + self._grace

    def _log_gaps(self, candles: list[Kline]) -> None:
        for a, b in pairwise(candles):
            if b.open_time - a.open_time != self._step:
                logger.warning(
                    "%s %s: no candles between %s and %s UTC",
                    self.symbol,
                    self.interval,
                    utc_text(a.open_time),
                    utc_text(b.open_time),
                )
