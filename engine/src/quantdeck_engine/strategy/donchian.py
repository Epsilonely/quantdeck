"""Donchian channel breakout: the trend-following candidate for the first strategy (D-39).

Enters when a candle closes beyond the extreme of the previous `entry_period` candles, with
the stop at the opposite extreme of the previous `exit_period` candles. Exits when a candle
closes beyond that shorter channel against the position, so the exit follows the trend
while the exchange-side stop stays where it was placed (D-36).
"""

from collections import deque
from dataclasses import dataclass, field

from quantdeck_engine.binance.streams import Kline
from quantdeck_engine.strategy.base import Action, Enter, Exit, Position
from quantdeck_engine.trading.rules import Side


@dataclass
class DonchianBreakout:
    interval: str = "1h"
    entry_period: int = 20
    exit_period: int = 10
    _previous: deque[Kline] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not 1 <= self.exit_period <= self.entry_period:
            raise ValueError("Donchian periods need 1 <= exit_period <= entry_period")
        self._previous = deque(maxlen=self.entry_period)

    @property
    def warmup(self) -> int:
        return self.entry_period + 1

    @property
    def limit_entries(self) -> bool:
        return False

    def on_candle(self, candle: Kline, position: Position | None) -> Action | None:
        try:
            return self._decide(candle, position)
        finally:
            self._previous.append(candle)

    def _decide(self, candle: Kline, position: Position | None) -> Action | None:
        if len(self._previous) < self.entry_period:
            return None
        recent = list(self._previous)[-self.exit_period :]
        exit_low = min(k.low for k in recent)
        exit_high = max(k.high for k in recent)
        if position is not None:
            if position.side is Side.LONG and candle.close < exit_low:
                return Exit()
            if position.side is Side.SHORT and candle.close > exit_high:
                return Exit()
            return None
        if candle.close > max(k.high for k in self._previous):
            return Enter(Side.LONG, exit_low)
        if candle.close < min(k.low for k in self._previous):
            return Enter(Side.SHORT, exit_high)
        return None
