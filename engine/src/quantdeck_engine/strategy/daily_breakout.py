"""Daily-open volatility breakout: a 15m candidate (GitHub issue #15).

Yesterday sets the day's range: R is the previous UTC day's high minus its low, and O the
day's 00:00 UTC open. The first candle of the day to close at or beyond O ± k·R enters in
that direction, with the stop at O, and the position is closed on the day's last candle.
A day takes at most one entry: once any candle of the day has closed beyond a level, later
ones don't enter, whether that entry was taken, stopped out, or skipped. Everything comes
from the last two days' candles, so a restart decides the same (D-36).

The exit depends only on the candle's clock time, not on when the position was entered,
so an adopted position is also closed at the end of its day.
"""

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from quantdeck_engine.binance.streams import INTERVAL_MS, Kline
from quantdeck_engine.strategy.base import Action, ChartLine, Enter, Exit, Position, parameter
from quantdeck_engine.trading.rules import Side

DAY_MS = 24 * 60 * 60 * 1000


@dataclass
class DailyOpenBreakout:
    interval: str = "15m"
    k: Decimal = parameter(
        Decimal("0.5"),
        "돌파 기준 배수. 당일 00:00 UTC 시가에서 전날(UTC) 고가−저가 폭 × k 이상 벗어나 처음 "
        "마감한 봉의 방향으로 진입합니다. 스톱은 당일 시가, 청산은 그날 마지막 봉이며 하루에 "
        "한 번만 진입합니다.",
    )
    long_only: bool = parameter(
        False, "켜면 위쪽 돌파(롱)만 진입하고, 아래쪽 돌파(숏)는 건너뜁니다."
    )
    _step: int = field(init=False, repr=False)
    _previous: deque[Kline] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        step = INTERVAL_MS.get(self.interval)
        if step is None or step >= DAY_MS or DAY_MS % step:
            raise ValueError("the daily breakout needs an interval that divides a day")
        if self.k <= 0:
            raise ValueError("the daily breakout needs k > 0")
        self._step = step
        self._previous = deque(maxlen=self.warmup)

    @property
    def warmup(self) -> int:
        """Two days: all of yesterday, and today up to the current candle."""
        return 2 * DAY_MS // INTERVAL_MS[self.interval]

    @property
    def limit_entries(self) -> bool:
        return False

    @property
    def chart_lines(self) -> tuple[ChartLine, ...]:
        """The day's entry levels while flat. A position closes at the day's end, not at a
        price, and its stop at the day's open shows as the stop line."""
        k = format(self.k, "f")
        lines = (ChartLine("upper", f"롱 진입선 (시가 + {k} × 전날 폭)", ("flat",)),)
        if not self.long_only:
            lines += (ChartLine("lower", f"숏 진입선 (시가 − {k} × 전날 폭)", ("flat",)),)
        return lines

    def levels(self, previous: Sequence[Kline], candle: Kline | None) -> dict[str, Decimal]:
        if candle is not None:
            found = self._levels(previous, candle.open_time, candle.open)
        elif previous:
            # The next candle; if it opens the day, its open isn't known yet.
            found = self._levels(previous, previous[-1].open_time + self._step, None)
        else:
            found = None
        if found is None:
            return {}
        _, upper, lower = found
        return {"upper": upper} if self.long_only else {"upper": upper, "lower": lower}

    def on_candle(self, candle: Kline, position: Position | None) -> Action | None:
        try:
            return self._decide(candle, position)
        finally:
            self._previous.append(candle)

    def _decide(self, candle: Kline, position: Position | None) -> Action | None:
        if position is not None:
            return Exit() if self._last_of_day(candle.open_time) else None
        found = self._levels(self._previous, candle.open_time, candle.open)
        if found is None:
            return None
        day_open, upper, lower = found
        if candle.close >= upper:
            return Enter(Side.LONG, day_open)
        if not self.long_only and candle.close <= lower:
            return Enter(Side.SHORT, day_open)
        return None

    def _levels(
        self, previous: Iterable[Kline], open_time: int, open_: Decimal | None
    ) -> tuple[Decimal, Decimal, Decimal] | None:
        """The day's open and its upper and lower entry levels for the candle opening at
        `open_time` at `open_` (None if not known yet), from the candles before it. None when
        that candle can't enter: the day's last one, a day without all of yesterday or its
        own first candle, or a day whose entry has come."""
        if self._last_of_day(open_time):
            return None  # its entry would be closed at the next open
        day = open_time - open_time % DAY_MS
        yesterday = [k for k in previous if day - DAY_MS <= k.open_time < day]
        today = [k for k in previous if k.open_time >= day]
        if len(yesterday) < DAY_MS // self._step:
            return None  # yesterday's range is incomplete
        opening_time, day_open = (
            (today[0].open_time, today[0].open) if today else (open_time, open_)
        )
        if opening_time != day or day_open is None:
            return None  # the day's first candle is missing, or still to open
        reach = self.k * (max(k.high for k in yesterday) - min(k.low for k in yesterday))
        if reach <= 0:
            return None
        upper, lower = day_open + reach, day_open - reach
        if any(self._beyond(k.close, upper, lower) for k in today):
            return None  # the day's entry has come
        return day_open, upper, lower

    def _last_of_day(self, open_time: int) -> bool:
        return (open_time + self._step) % DAY_MS == 0

    def _beyond(self, close: Decimal, upper: Decimal, lower: Decimal) -> bool:
        return close >= upper or (not self.long_only and close <= lower)
