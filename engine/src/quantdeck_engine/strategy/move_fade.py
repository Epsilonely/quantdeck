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

from quantdeck_engine.binance.streams import Kline
from quantdeck_engine.strategy.base import Action, ChartLine, Enter, Exit, Position, parameter
from quantdeck_engine.strategy.indicators import BarBuilder, mean_and_deviation
from quantdeck_engine.trading.rules import Side


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
    _builder: BarBuilder = field(init=False, repr=False)
    _returns: deque[Decimal] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._builder = BarBuilder(self.interval, self.bar_minutes)
        if self.k <= 0 or self.lookback < 2:
            raise ValueError("the move fade needs k > 0 and lookback >= 2")
        self._returns = deque(maxlen=self.lookback)

    @property
    def warmup(self) -> int:
        """The current bar and the `lookback` before it."""
        return (self.lookback + 1) * self._builder.size

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
        bar = self._builder.add(candle)
        if bar is None:
            return None  # the bar is still open
        r = candle.close / bar[0].open - 1 if bar else None
        history = list(self._returns)
        if r is not None:
            self._returns.append(r)
        if position is not None:
            return Exit()
        if r is None or len(history) < self.lookback:
            return None
        _, sigma = mean_and_deviation(history)
        if sigma == 0:
            return None
        if r <= -self.k * sigma:
            return Enter(Side.LONG, candle.close * (1 + r))
        if r >= self.k * sigma:
            return Enter(Side.SHORT, candle.close * (1 + r))
        return None
