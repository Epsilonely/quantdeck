"""Fade of large 1h/2h moves: a 15m candidate (GitHub issue #15).

Candles are grouped into bars of `bar_minutes` on UTC boundaries. When a bar closes, its
return r (close over open, minus 1) is compared with σ, the standard deviation of the
previous `lookback` bars' returns. A drop of at least k·σ enters long and a rise of at
least k·σ short, expecting part of the move to reverse. The stop allows the same move
again (a long's stop sits |r| below the close), and the position is closed when the next
bar closes. Only complete bars count; a bar with a missing candle neither signals nor
enters σ.

The exit depends only on the candle's clock time, so an adopted position is closed at the
next bar boundary too (D-36).
"""

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from quantdeck_engine.binance.streams import INTERVAL_MS, Kline
from quantdeck_engine.strategy.base import Action, ChartLine, Enter, Exit, Position, parameter
from quantdeck_engine.trading.rules import Side

DAY_MS = 24 * 60 * 60 * 1000


@dataclass
class MoveFade:
    interval: str = "15m"
    bar_minutes: int = parameter(
        120,
        "움직임을 재는 묶음 봉의 길이(분). 봉들을 UTC 기준으로 이 길이씩 묶습니다. 봉 간격의 "
        "배수이면서 하루(1440분)를 나누어떨어지게 해야 합니다.",
    )
    k: Decimal = parameter(
        Decimal(3),
        "진입 기준(표준편차의 배수). 묶음 봉의 수익률이 −k×σ 이하면 롱, +k×σ 이상이면 숏으로 "
        "되돌림을 노립니다. 스톱은 같은 크기의 움직임을 한 번 더 버티는 곳에 두고, 다음 묶음 "
        "봉이 마감하면 청산합니다.",
    )
    lookback: int = parameter(120, "σ(묶음 봉 수익률의 표준편차)를 계산하는 직전 묶음 봉의 개수.")
    _step: int = field(init=False, repr=False)
    _bar_ms: int = field(init=False, repr=False)
    _returns: deque[Decimal] = field(init=False, repr=False)
    _bar_start: int | None = field(init=False, repr=False, default=None)
    _bar_open: Decimal | None = field(init=False, repr=False, default=None)
    _bar_count: int = field(init=False, repr=False, default=0)

    def __post_init__(self) -> None:
        step = INTERVAL_MS.get(self.interval)
        bar_ms = self.bar_minutes * 60_000
        if step is None or bar_ms < step or bar_ms % step or DAY_MS % bar_ms:
            raise ValueError(
                "the move fade needs bars that are a whole number of candles and divide a day"
            )
        if self.k <= 0 or self.lookback < 2:
            raise ValueError("the move fade needs k > 0 and lookback >= 2")
        self._step = step
        self._bar_ms = bar_ms
        self._returns = deque(maxlen=self.lookback)

    @property
    def warmup(self) -> int:
        """The current bar and the `lookback` before it."""
        return (self.lookback + 1) * self.bar_minutes * 60_000 // INTERVAL_MS[self.interval]

    @property
    def limit_entries(self) -> bool:
        return False

    @property
    def chart_lines(self) -> tuple[ChartLine, ...]:
        """None: it decides by a bar's return against σ, not by a price on the chart."""
        return ()

    def levels(self, previous: Sequence[Kline], candle: Kline | None) -> dict[str, Decimal]:
        return {}

    def on_candle(self, candle: Kline, position: Position | None) -> Action | None:
        bar_start = candle.open_time - candle.open_time % self._bar_ms
        if bar_start != self._bar_start:
            self._bar_start = bar_start
            # A bar whose first candle is missing has no open and never counts.
            self._bar_open = candle.open if candle.open_time == bar_start else None
            self._bar_count = 0
        self._bar_count += 1
        if (candle.open_time + self._step) % self._bar_ms:
            return None  # the bar is still open
        r = None
        if self._bar_open is not None and self._bar_count == self._bar_ms // self._step:
            r = candle.close / self._bar_open - 1
        history = list(self._returns)
        if r is not None:
            self._returns.append(r)
        if position is not None:
            return Exit()
        if r is None or len(history) < self.lookback:
            return None
        mean = sum(history, Decimal(0)) / len(history)
        sigma = (sum(((x - mean) ** 2 for x in history), Decimal(0)) / len(history)).sqrt()
        if sigma == 0:
            return None
        if r <= -self.k * sigma:
            return Enter(Side.LONG, candle.close * (1 + r))
        if r >= self.k * sigma:
            return Enter(Side.SHORT, candle.close * (1 + r))
        return None
