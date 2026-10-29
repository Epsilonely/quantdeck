"""Bollinger Band mean reversion: counter-trend, dropped as the first strategy (D-39, D-41).

Enters against a candle that closes outside the bands (the mean of the last `period`
closes, the current one included, ± `width` standard deviations), expecting a return to
the middle. The stop sits `stop_width` standard deviations beyond that close. Exits when a
candle closes back at or across the middle band.

With a `limit_offset`, the entry is a limit order that many standard deviations farther
out than the close (a long buys below it), and the stop sits `stop_width` beyond the limit
(GitHub issue #17, D-45). Only the backtest fills limit entries.
"""

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from quantdeck_engine.binance.streams import Kline
from quantdeck_engine.strategy.base import Action, ChartLine, Enter, Exit, Position, parameter
from quantdeck_engine.strategy.indicators import mean_and_deviation
from quantdeck_engine.trading.rules import Side


@dataclass(frozen=True)
class Bands:
    middle: Decimal  # the mean
    upper: Decimal
    lower: Decimal
    deviation: Decimal


@dataclass
class BollingerReversion:
    interval: str = "1h"
    period: int = parameter(
        20,
        "밴드를 계산하는 봉 수. 현재 봉을 포함한 최근 이 개수 종가의 평균(가운데 선)과 "
        "표준편차로 밴드를 그립니다.",
    )
    width: Decimal = parameter(
        Decimal(2),
        "밴드 폭(표준편차의 배수). 종가가 평균 ± 이 배수 × 표준편차 밖에서 마감하면 반대 "
        "방향으로 진입하고, 종가가 가운데 선에 닿거나 넘어가면 청산합니다.",
    )
    stop_width: Decimal = parameter(
        Decimal(1),
        "스톱 거리(표준편차의 배수). 진입 신호가 난 종가(지정가 진입이면 지정가)에서 이만큼 더 "
        "바깥에 스톱을 둡니다.",
    )
    limit_offset: Decimal = parameter(
        Decimal(0),
        "지정가 진입 거리(표준편차의 배수). 0이면 시장가로 진입합니다. 0보다 크면 종가보다 "
        "이만큼 바깥에 지정가를 거는데, 지정가 진입은 백테스트 전용이라 엔진이 거부합니다.",
    )
    _closes: deque[Decimal] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.period < 2 or self.width <= 0 or self.stop_width <= 0:
            raise ValueError("Bollinger needs period >= 2, width > 0, and stop_width > 0")
        if self.limit_offset < 0:
            raise ValueError("Bollinger needs limit_offset >= 0")
        self._closes = deque(maxlen=self.period)

    @property
    def warmup(self) -> int:
        return self.period

    @property
    def limit_entries(self) -> bool:
        return self.limit_offset > 0

    @property
    def chart_lines(self) -> tuple[ChartLine, ...]:
        """The bands while flat; while holding, the middle band that closes the position."""
        period, width = self.period, format(self.width, "f")
        return (
            ChartLine("upper", f"위 밴드 ({period}봉 평균 + {width}σ)", ("flat",)),
            ChartLine("lower", f"아래 밴드 ({period}봉 평균 − {width}σ)", ("flat",)),
            ChartLine("middle", f"청산선 ({period}봉 평균)", ("long", "short")),
        )

    def levels(self, previous: Sequence[Kline], candle: Kline | None) -> dict[str, Decimal]:
        if candle is None:
            return {}  # the bands take the candle's own close
        closes = [k.close for k in previous[-(self.period - 1) :]]
        bands = self._bands([*closes, candle.close])
        if bands is None:
            return {}
        return {"upper": bands.upper, "lower": bands.lower, "middle": bands.middle}

    def on_candle(self, candle: Kline, position: Position | None) -> Action | None:
        self._closes.append(candle.close)
        bands = self._bands(list(self._closes))
        if bands is None:
            return None
        close = candle.close
        if position is not None:
            if position.side is Side.LONG and close >= bands.middle:
                return Exit()
            if position.side is Side.SHORT and close <= bands.middle:
                return Exit()
            return None
        if bands.deviation == 0:
            return None
        if close < bands.lower:
            return self._enter(Side.LONG, close, bands.deviation)
        if close > bands.upper:
            return self._enter(Side.SHORT, close, bands.deviation)
        return None

    def _bands(self, closes: Sequence[Decimal]) -> Bands | None:
        """The bands over the last `period` of `closes`, the current one last; None with
        fewer."""
        if len(closes) < self.period:
            return None
        mean, deviation = mean_and_deviation(closes[-self.period :])
        spread = self.width * deviation
        return Bands(mean, mean + spread, mean - spread, deviation)

    def _enter(self, side: Side, close: Decimal, deviation: Decimal) -> Enter:
        if not self.limit_offset:
            return Enter(side, close - side.sign * self.stop_width * deviation)
        limit = close - side.sign * self.limit_offset * deviation
        return Enter(side, limit - side.sign * self.stop_width * deviation, limit)
