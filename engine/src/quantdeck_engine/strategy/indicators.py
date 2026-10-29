"""What several strategies compute alike, written once so each computes it the same way.

Each reads nothing but its arguments, except `BarBuilder`, which a strategy feeds its
candles in order, so a strategy's `levels` can call the functions for any candle (D-56).
"""

from collections.abc import Sequence
from decimal import Decimal
from itertools import pairwise
from typing import Protocol

from quantdeck_engine.binance.streams import INTERVAL_MS, Kline
from quantdeck_engine.times import DAY_MS


class PriceRange(Protocol):
    """A candle or a bar built from candles."""

    @property
    def high(self) -> Decimal: ...

    @property
    def low(self) -> Decimal: ...

    @property
    def close(self) -> Decimal: ...


def average_true_range(bars: Sequence[PriceRange]) -> Decimal:
    """The mean true range of `bars` after the first, which only lends its close."""
    ranges = [
        max(bar.high, before.close) - min(bar.low, before.close) for before, bar in pairwise(bars)
    ]
    return sum(ranges, Decimal(0)) / len(ranges)


def mean_and_deviation(values: Sequence[Decimal]) -> tuple[Decimal, Decimal]:
    """The mean of `values` and their population standard deviation."""
    mean = sum(values, Decimal(0)) / len(values)
    deviation = (sum(((v - mean) ** 2 for v in values), Decimal(0)) / len(values)).sqrt()
    return mean, deviation


class BarBuilder:
    """Groups closed candles into bars of `bar_minutes` on UTC boundaries."""

    def __init__(self, interval: str, bar_minutes: int) -> None:
        step = INTERVAL_MS.get(interval)
        bar_ms = bar_minutes * 60_000
        if step is None or bar_ms < step or bar_ms % step or DAY_MS % bar_ms:
            raise ValueError("bars must be a whole number of candles and divide a day")
        self.step = step
        self.bar_ms = bar_ms
        self.size = bar_ms // step  # candles per bar
        self._start: int | None = None
        self._candles: list[Kline] = []

    def add(self, candle: Kline) -> list[Kline] | None:
        """Add the next closed candle. Once it closes its bar, return the bar's candles, or
        an empty list if one is missing, since such a bar never counts; None while the bar
        is still open."""
        start = candle.open_time - candle.open_time % self.bar_ms
        if start != self._start:
            self._start = start
            self._candles = []
        self._candles.append(candle)
        if (candle.open_time + self.step) % self.bar_ms:
            return None
        candles = self._candles
        if len(candles) != self.size or candles[0].open_time != start:
            return []
        return candles
