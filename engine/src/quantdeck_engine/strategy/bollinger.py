"""Bollinger Band mean reversion: the counter-trend candidate for the first strategy (D-39).

Enters against a candle that closes outside the bands (the mean of the last `period`
closes, the current one included, ± `width` standard deviations), expecting a return to
the middle. The stop sits `stop_width` standard deviations beyond that close. Exits when a
candle closes back at or across the middle band.
"""

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal

from quantdeck_engine.binance.streams import Kline
from quantdeck_engine.strategy.base import Action, Enter, Exit, Position
from quantdeck_engine.trading.rules import Side


@dataclass
class BollingerReversion:
    interval: str = "1h"
    period: int = 20
    width: Decimal = Decimal(2)
    stop_width: Decimal = Decimal(1)
    _closes: deque[Decimal] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.period < 2 or self.width <= 0 or self.stop_width <= 0:
            raise ValueError("Bollinger needs period >= 2, width > 0, and stop_width > 0")
        self._closes = deque(maxlen=self.period)

    @property
    def warmup(self) -> int:
        return self.period

    @property
    def limit_entries(self) -> bool:
        return False

    def on_candle(self, candle: Kline, position: Position | None) -> Action | None:
        self._closes.append(candle.close)
        if len(self._closes) < self.period:
            return None
        mean = sum(self._closes, Decimal(0)) / self.period
        deviation = (sum(((c - mean) ** 2 for c in self._closes), Decimal(0)) / self.period).sqrt()
        close = candle.close
        if position is not None:
            if position.side is Side.LONG and close >= mean:
                return Exit()
            if position.side is Side.SHORT and close <= mean:
                return Exit()
            return None
        if deviation == 0:
            return None
        if close < mean - self.width * deviation:
            return Enter(Side.LONG, close - self.stop_width * deviation)
        if close > mean + self.width * deviation:
            return Enter(Side.SHORT, close + self.stop_width * deviation)
        return None
