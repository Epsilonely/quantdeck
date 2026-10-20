"""Market data streams for Binance USDⓈ-M Futures.

Streams use the routed URLs recorded in docs/memory-bank/binanceNotes.md; klines and mark
prices are on the /market route. Binance drops every connection after 24 hours at most, so
streams reconnect forever instead of ending.
"""

import json
import logging
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from quantdeck_engine.times import DAY_MS

logger = logging.getLogger(__name__)

# Candle lengths. 1M is left out: months differ in length.
INTERVAL_MS = {
    "1m": 60_000,
    "3m": 3 * 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "2h": 2 * 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "6h": 6 * 60 * 60_000,
    "8h": 8 * 60 * 60_000,
    "12h": 12 * 60 * 60_000,
    "1d": DAY_MS,
    "3d": 3 * DAY_MS,
    "1w": 7 * DAY_MS,
}
# How long after its close REST's copy of a candle is trusted. The stream's closed message
# is final at once; REST lagged it by more than 10 s on testnet (2026-10-19, D-42).
SETTLE_MS = 60_000
# Seconds a closing stream waits for Binance to drop the connection after the close
# handshake; then websockets aborts it. Its default of 10 s held up symbol changes and
# shutdown, since testnet took 5-9.5 s to drop it (binanceNotes.md). The engine is done
# with the stream by then, so aborting loses nothing.
CLOSE_TIMEOUT = 2.0


# Slots: a backtest holds every candle of its period in memory.
@dataclass(frozen=True, slots=True)
class Kline:
    symbol: str
    interval: str
    open_time: int  # ms
    close_time: int  # ms
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal  # base asset
    closed: bool  # False while the candle is still forming


def kline_stream_url(ws_root: str, symbol: str, interval: str) -> str:
    return f"{ws_root}/market/ws/{symbol.lower()}@kline_{interval}"


def parse_kline(message: str | bytes) -> Kline:
    k = json.loads(message)["k"]
    return Kline(
        symbol=k["s"],
        interval=k["i"],
        open_time=k["t"],
        close_time=k["T"],
        open=Decimal(k["o"]),
        high=Decimal(k["h"]),
        low=Decimal(k["l"]),
        close=Decimal(k["c"]),
        volume=Decimal(k["v"]),
        closed=k["x"],
    )


def kline_from_row(row: Sequence[Any], symbol: str, interval: str) -> Kline:
    """A closed candle from a `GET /fapi/v1/klines` row or an archive CSV row.

    Both share the column order open time, open, high, low, close, volume, close time, ...
    REST sends times as numbers and prices as strings; the archive CSV has only strings.
    The caller drops candles that are still open.
    """
    return Kline(
        symbol=symbol,
        interval=interval,
        open_time=int(row[0]),
        close_time=int(row[6]),
        open=Decimal(row[1]),
        high=Decimal(row[2]),
        low=Decimal(row[3]),
        close=Decimal(row[4]),
        volume=Decimal(row[5]),
        closed=True,
    )


def settled_klines(
    rows: Sequence[Sequence[Any]], symbol: str, interval: str, server_ms: int
) -> list[Kline]:
    """The candles in `GET /fapi/v1/klines` rows that closed `SETTLE_MS` or more before
    `server_ms`, oldest first.

    The rows stop at the first candle that is still open or closed only just now, so a
    result shorter than `rows` means the end of the settled history was reached.
    """
    candles = []
    for row in rows:
        candle = kline_from_row(row, symbol, interval)
        if candle.close_time >= server_ms - SETTLE_MS:
            break
        candles.append(candle)
    return candles


def connect_stream(url: str) -> connect:
    """`websockets` connect for every Binance stream, so a close takes `CLOSE_TIMEOUT` at
    most: `async with` for one connection, `async for` to reconnect forever."""
    return connect(url, close_timeout=CLOSE_TIMEOUT)


async def _messages(
    url: str, name: str, on_connect: Callable[[], None] | None = None
) -> AsyncIterator[str | bytes]:
    """Raw messages from `url` forever, reconnecting whenever the connection drops.

    Messages sent while reconnecting are lost; `on_connect` runs on every (re)connect.
    """
    async for ws in connect_stream(url):
        async with ws:
            logger.info("%s stream %s connected", name, url)
            if on_connect is not None:
                on_connect()
            try:
                async for message in ws:
                    yield message
            except ConnectionClosed:
                pass
        logger.warning("%s stream %s closed; reconnecting", name, url)


async def kline_stream(
    ws_root: str,
    symbol: str,
    interval: str,
    *,
    on_connect: Callable[[], None] | None = None,
) -> AsyncIterator[Kline]:
    """Yield kline updates forever, reconnecting whenever the connection drops.

    Updates sent while reconnecting are lost; `on_connect` runs on every (re)connect so the
    caller can fill the gap from REST (`candle_feed.py`). Wrap the call in
    `contextlib.aclosing` so the connection closes as soon as you stop iterating.
    """
    url = kline_stream_url(ws_root, symbol, interval)
    async with aclosing(_messages(url, "Kline", on_connect)) as messages:
        async for message in messages:
            yield parse_kline(message)


@dataclass(frozen=True)
class MarkPrice:
    symbol: str
    price: Decimal
    event_time: int  # ms


def mark_price_stream_url(ws_root: str, symbols: Iterable[str]) -> str:
    streams = "/".join(f"{s.lower()}@markPrice@1s" for s in symbols)
    return f"{ws_root}/market/stream?streams={streams}"


def parse_mark_price(message: str | bytes) -> MarkPrice:
    """One `markPriceUpdate` from a combined stream (`{"stream": ..., "data": ...}`)."""
    data = json.loads(message)["data"]
    return MarkPrice(symbol=data["s"], price=Decimal(data["p"]), event_time=data["E"])


async def mark_price_stream(ws_root: str, symbols: Iterable[str]) -> AsyncIterator[MarkPrice]:
    """Yield each symbol's mark price once a second, forever, reconnecting as needed.

    For display only: risk checks don't use the mark price (D-24). A message that can't be
    parsed is logged and skipped. Wrap the call in `contextlib.aclosing`.
    """
    url = mark_price_stream_url(ws_root, symbols)
    async with aclosing(_messages(url, "Mark price")) as messages:
        async for message in messages:
            try:
                update = parse_mark_price(message)
            except (KeyError, TypeError, ValueError, InvalidOperation):
                logger.warning("Could not parse a mark price message: %.200s", message)
                continue
            yield update
