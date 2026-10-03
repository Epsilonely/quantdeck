"""Market data streams for Binance USDⓈ-M Futures.

Streams use the routed URLs recorded in docs/memory-bank/binanceNotes.md; klines are on the
/market route. Binance drops every connection after 24 hours at most, so streams reconnect
forever instead of ending.
"""

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
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


async def kline_stream(ws_root: str, symbol: str, interval: str) -> AsyncIterator[Kline]:
    """Yield kline updates forever, reconnecting whenever the connection drops.

    Updates sent while reconnecting are lost. Wrap the call in `contextlib.aclosing` so the
    connection closes as soon as you stop iterating.
    """
    url = kline_stream_url(ws_root, symbol, interval)
    async for ws in connect(url):
        async with ws:
            logger.info("Kline stream %s connected", url)
            try:
                async for message in ws:
                    yield parse_kline(message)
            except ConnectionClosed:
                pass
        logger.warning("Kline stream %s closed; reconnecting", url)
