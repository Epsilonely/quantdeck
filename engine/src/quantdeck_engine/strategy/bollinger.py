"""Bollinger Band mean reversion: the counter-trend candidate for the first strategy (D-39).

Enters against a candle that closes outside the bands (the mean of the last `period`
closes, the current one included, ± `width` standard deviations), expecting a return to
the middle. The stop sits `stop_width` standard deviations beyond that close. Exits when a
candle closes back at or across the middle band.

With a `limit_offset`, the entry is a limit order that many standard deviations farther
out than the close (a long buys below it), and the stop sits `stop_width` beyond the limit
(GitHub issue #17, D-45). Only the backtest fills limit entries.
"""

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal

from quantdeck_engine.binance.streams import Kline
from quantdeck_engine.strategy.base import Action, Enter, Exit, Position, parameter
from quantdeck_engine.trading.rules import Side


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
            return self._enter(Side.LONG, close, deviation)
        if close > mean + self.width * deviation:
            return self._enter(Side.SHORT, close, deviation)
        return None

    def _enter(self, side: Side, close: Decimal, deviation: Decimal) -> Enter:
        if not self.limit_offset:
            return Enter(side, close - side.sign * self.stop_width * deviation)
        limit = close - side.sign * self.limit_offset * deviation
        return Enter(side, limit - side.sign * self.stop_width * deviation, limit)
