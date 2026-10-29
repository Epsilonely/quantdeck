"""Donchian channel breakout: trend following, chosen as the first strategy (D-39, D-41).

Enters when a candle closes beyond the extreme of the previous `entry_period` candles, with
the stop at the opposite extreme of the previous `exit_period` candles. Exits when a candle
closes beyond that shorter channel against the position, so the exit follows the trend
while the exchange-side stop stays where it was placed (D-36).
"""

from collections import deque
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal

from quantdeck_engine.binance.streams import Kline
from quantdeck_engine.strategy.base import Action, ChartLine, Enter, Exit, Position, parameter
from quantdeck_engine.trading.rules import Side


@dataclass(frozen=True)
class Channels:
    """The extremes of the candles before the current one, never including it: a close can
    only break out of a channel it isn't part of."""

    entry_high: Decimal
    entry_low: Decimal
    exit_high: Decimal
    exit_low: Decimal


def channels(previous: Sequence[Kline], entry_period: int, exit_period: int) -> Channels | None:
    """The channels over the last `entry_period` and `exit_period` of `previous`; None with
    fewer than `entry_period` of them."""
    if len(previous) < entry_period:
        return None
    entry = previous[-entry_period:]
    exit_ = previous[-exit_period:]
    return Channels(
        entry_high=max(k.high for k in entry),
        entry_low=min(k.low for k in entry),
        exit_high=max(k.high for k in exit_),
        exit_low=min(k.low for k in exit_),
    )


def channel_lines(entry_period: int, exit_period: int) -> tuple[ChartLine, ...]:
    """The channels on a chart (D-56): the entry channel while flat, and while holding only
    the exit channel's side that closes the position."""
    return (
        ChartLine("entry_high", f"진입 채널 위 (직전 {entry_period}봉 최고가)", ("flat",)),
        ChartLine("entry_low", f"진입 채널 아래 (직전 {entry_period}봉 최저가)", ("flat",)),
        ChartLine("exit_low", f"청산선 (직전 {exit_period}봉 최저가)", ("long",)),
        ChartLine("exit_high", f"청산선 (직전 {exit_period}봉 최고가)", ("short",)),
    )


@dataclass
class DonchianBreakout:
    interval: str = "1h"
    entry_period: int = parameter(
        20,
        "진입 채널의 길이(봉 수). 종가가 직전 이 개수 봉의 최고가 위에서 마감하면 롱, "
        "최저가 아래에서 마감하면 숏으로 진입합니다.",
    )
    exit_period: int = parameter(
        10,
        "청산 채널의 길이(봉 수)로, 진입 채널 이하여야 합니다. 스톱은 진입할 때 직전 이 개수 "
        "봉의 반대쪽 끝(롱이면 최저가)에 두고 옮기지 않으며, 종가가 이 채널을 포지션 반대쪽으로 "
        "벗어나면 청산합니다.",
    )
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

    @property
    def chart_lines(self) -> tuple[ChartLine, ...]:
        return channel_lines(self.entry_period, self.exit_period)

    def levels(self, previous: Sequence[Kline], candle: Kline | None) -> dict[str, Decimal]:
        found = channels(previous, self.entry_period, self.exit_period)
        return {} if found is None else asdict(found)

    def on_candle(self, candle: Kline, position: Position | None) -> Action | None:
        try:
            return self._decide(candle, position)
        finally:
            self._previous.append(candle)

    def _decide(self, candle: Kline, position: Position | None) -> Action | None:
        found = channels(list(self._previous), self.entry_period, self.exit_period)
        if found is None:
            return None
        if position is not None:
            if position.side is Side.LONG and candle.close < found.exit_low:
                return Exit()
            if position.side is Side.SHORT and candle.close > found.exit_high:
                return Exit()
            return None
        if candle.close > found.entry_high:
            return Enter(Side.LONG, found.exit_low)
        if candle.close < found.entry_low:
            return Enter(Side.SHORT, found.exit_high)
        return None
