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


class _Extreme:
    """The highest or the lowest of the last `period` values added. A value that can no
    longer be the extreme is dropped when a newer one arrives, so each costs O(1) on average
    instead of a pass over the window. Of equal extremes the oldest is kept, as `max` and
    `min` return the first."""

    def __init__(self, period: int, *, highest: bool) -> None:
        self._period = period
        self._highest = highest
        self._kept: deque[tuple[int, Decimal]] = deque()  # (index, value), the extreme first
        self._added = 0

    def add(self, value: Decimal) -> None:
        kept = self._kept
        if self._highest:
            while kept and kept[-1][1] < value:
                kept.pop()
        else:
            while kept and kept[-1][1] > value:
                kept.pop()
        kept.append((self._added, value))
        self._added += 1
        if kept[0][0] <= self._added - 1 - self._period:
            kept.popleft()  # out of the window

    @property
    def value(self) -> Decimal:
        return self._kept[0][1]


class ChannelWindow:
    """The channels of the candles added so far, kept up to date as each one arrives."""

    def __init__(self, entry_period: int, exit_period: int) -> None:
        self._entry_period = entry_period
        self._added = 0
        self._entry_high = _Extreme(entry_period, highest=True)
        self._entry_low = _Extreme(entry_period, highest=False)
        self._exit_high = _Extreme(exit_period, highest=True)
        self._exit_low = _Extreme(exit_period, highest=False)

    def add(self, candle: Kline) -> None:
        self._added += 1
        self._entry_high.add(candle.high)
        self._entry_low.add(candle.low)
        self._exit_high.add(candle.high)
        self._exit_low.add(candle.low)

    def channels(self) -> Channels | None:
        """The channels over the last `entry_period` and `exit_period` candles added; None
        with fewer than `entry_period` of them."""
        if self._added < self._entry_period:
            return None
        return Channels(
            entry_high=self._entry_high.value,
            entry_low=self._entry_low.value,
            exit_high=self._exit_high.value,
            exit_low=self._exit_low.value,
        )


def channels(previous: Sequence[Kline], entry_period: int, exit_period: int) -> Channels | None:
    """The channels over the last `entry_period` and `exit_period` of `previous`; None with
    fewer than `entry_period` of them. Computed by the `ChannelWindow` a strategy decides
    with, so a chart's levels are the ones decided by (D-56)."""
    window = ChannelWindow(entry_period, exit_period)
    for candle in previous[-entry_period:]:
        window.add(candle)
    return window.channels()


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
    _window: ChannelWindow = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not 1 <= self.exit_period <= self.entry_period:
            raise ValueError("Donchian periods need 1 <= exit_period <= entry_period")
        self._window = ChannelWindow(self.entry_period, self.exit_period)

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
            self._window.add(candle)

    def _decide(self, candle: Kline, position: Position | None) -> Action | None:
        found = self._window.channels()
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
