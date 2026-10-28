"""Candles for clients' charts (D-54), display only.

A symbol with a strategy keeps its last closed candles on the strategy's interval, the same
candles the strategy decided on, and the candle still forming. The closed-candle feed fills
both (D-42); nothing here feeds a decision.

The closed candles go to clients as a whole list whenever it changes, as the trades do
(D-51); the forming candle rides in the state, which goes out about once a second (D-33).
"""

import math
import time
from collections import deque
from collections.abc import Callable, Iterable
from decimal import Decimal
from typing import Any

from quantdeck_engine.binance.streams import Kline
from quantdeck_engine.times import iso_utc

CHART_CANDLES = 300  # closed candles kept per symbol
# The stream updates a forming candle several times a second; the state needs it at most
# this often (seconds).
FORMING_INTERVAL = 1.0


def candle_values(candle: Kline) -> dict[str, str]:
    """A candle as clients get it; prices are fixed-point strings (D-33)."""
    return {
        "open_time": iso_utc(candle.open_time / 1000),
        "open": _text(candle.open),
        "high": _text(candle.high),
        "low": _text(candle.low),
        "close": _text(candle.close),
    }


class CandleChart:
    def __init__(
        self,
        symbol: str,
        interval: str,
        *,
        size: int = CHART_CANDLES,
        clock: Callable[[], float] = time.monotonic,
        forming_interval: float = FORMING_INTERVAL,
    ) -> None:
        self.symbol = symbol
        self.interval = interval
        self.size = size
        self._clock = clock
        self._forming_interval = forming_interval
        self._closed: deque[Kline] = deque(maxlen=size)
        self._payload: dict[str, Any] | None = None  # built once per list
        self._forming: Kline | None = None  # the stream's latest update
        self._shown: Kline | None = None  # the forming candle clients last got
        self._shown_at = -math.inf

    def reset(self, candles: Iterable[Kline]) -> None:
        """Start over with these closed candles, oldest first: the warm-up's."""
        self._closed = deque(candles, maxlen=self.size)
        self._payload = None

    def add_closed(self, candle: Kline) -> None:
        if self._closed and candle.open_time <= self._closed[-1].open_time:
            return
        self._closed.append(candle)
        self._payload = None

    def set_forming(self, candle: Kline | None) -> None:
        """The stream's latest update of the forming candle; None once the stream stops."""
        self._forming = candle

    def forming(self) -> Kline | None:
        """The forming candle for the state: a newer update shows at most once per
        FORMING_INTERVAL, and none once its candle is among the closed ones."""
        now = self._clock()
        if self._forming is not self._shown and now - self._shown_at >= self._forming_interval:
            self._shown, self._shown_at = self._forming, now
        shown = self._shown
        if shown is None or (self._closed and shown.open_time <= self._closed[-1].open_time):
            return None
        return shown

    @property
    def payload(self) -> dict[str, Any] | None:
        """The closed candles as clients get them, oldest first; the same object until the
        list changes, so the server encodes it once. None before the warm-up."""
        if self._payload is None and self._closed:
            self._payload = {
                "symbol": self.symbol,
                "interval": self.interval,
                "candles": [candle_values(c) for c in self._closed],
            }
        return self._payload


def _text(amount: Decimal) -> str:
    """Fixed-point, never exponent notation."""
    return format(amount, "f")
