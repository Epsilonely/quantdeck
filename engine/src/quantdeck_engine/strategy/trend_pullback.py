"""Trend pullback: buys dips in an uptrend and sells rallies in a downtrend with limit
entries (GitHub issue #17, D-45).

Candles are grouped into bars of `bar_minutes` on UTC boundaries; only complete bars count.
The trend is the latest bar's close against the mean of the last `trend_period` bar closes,
and ATR is the mean true range of the last `atr_period` bars. While flat, every candle
enters with the trend: a limit `limit_atr` ATR beyond the candle's close (a long buys below
it), or at market when `limit_atr` is 0, with the stop `stop_atr` ATR beyond the entry
price. The position is closed when a bar closes on the other side of the mean. Only the
backtest fills limit entries.
"""

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import pairwise

from quantdeck_engine.binance.streams import INTERVAL_MS, Kline
from quantdeck_engine.strategy.base import Action, ChartLine, Enter, Exit, Position, parameter
from quantdeck_engine.trading.rules import Side

DAY_MS = 24 * 60 * 60 * 1000


@dataclass(frozen=True)
class _Bar:
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass
class TrendPullback:
    interval: str = "1h"
    bar_minutes: int = parameter(
        240,
        "추세와 ATR을 재는 묶음 봉의 길이(분, 240이면 4시간). 봉 간격의 배수이면서 "
        "하루(1440분)를 나누어떨어지게 해야 합니다.",
    )
    trend_period: int = parameter(
        55,
        "추세를 판단하는 묶음 봉 수. 마지막 묶음 봉 종가가 이 개수 종가의 평균보다 위면 상승, "
        "아래면 하락 추세로 보고 그 방향으로만 진입합니다. 묶음 봉이 평균 반대쪽에서 마감하면 "
        "청산합니다.",
    )
    atr_period: int = parameter(14, "ATR(묶음 봉의 평균 변동폭)을 계산하는 묶음 봉 수.")
    limit_atr: Decimal = parameter(
        Decimal("0.5"),
        "지정가 진입 거리(ATR 배수). 종가에서 이만큼 되돌린 가격에 지정가를 겁니다. 0이면 "
        "시장가로 진입합니다. 0보다 크면 지정가 진입이라 엔진이 거부하므로(백테스트 전용), "
        "엔진에서 쓰려면 0으로 두세요.",
    )
    stop_atr: Decimal = parameter(
        Decimal(2),
        "스톱 거리(ATR 배수). 진입가(지정가 진입이면 지정가)에서 이만큼 떨어진 곳에 스톱을 둡니다.",
    )
    _step: int = field(init=False, repr=False)
    _bar_ms: int = field(init=False, repr=False)
    _bars: deque[_Bar] = field(init=False, repr=False)
    _bar_start: int | None = field(init=False, repr=False, default=None)
    _bar_candles: list[Kline] = field(init=False, repr=False, default_factory=list)
    # As of the latest complete bar; None until there are enough bars.
    _trend: Side | None = field(init=False, repr=False, default=None)
    _atr: Decimal | None = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        step = INTERVAL_MS.get(self.interval)
        bar_ms = self.bar_minutes * 60_000
        if step is None or bar_ms < step or bar_ms % step or DAY_MS % bar_ms:
            raise ValueError(
                "the trend pullback needs bars that are a whole number of candles and divide a day"
            )
        if self.trend_period < 2 or self.atr_period < 1:
            raise ValueError("the trend pullback needs trend_period >= 2 and atr_period >= 1")
        if self.limit_atr < 0 or self.stop_atr <= 0:
            raise ValueError("the trend pullback needs limit_atr >= 0 and stop_atr > 0")
        self._step = step
        self._bar_ms = bar_ms
        # The ATR's first true range needs the close of the bar before it.
        self._bars = deque(maxlen=max(self.trend_period, self.atr_period + 1))

    @property
    def warmup(self) -> int:
        """Enough candles for the bars it needs, whichever candle of a bar they start at."""
        return (self._bars.maxlen or 0) * self._bar_ms // self._step + self._bar_ms // self._step

    @property
    def limit_entries(self) -> bool:
        return self.limit_atr > 0

    @property
    def chart_lines(self) -> tuple[ChartLine, ...]:
        """None: it decides on bars built from its candles, which the chart doesn't show, and
        the engine refuses its limit entries (D-45)."""
        return ()

    def levels(self, previous: Sequence[Kline], candle: Kline | None) -> dict[str, Decimal]:
        return {}

    def on_candle(self, candle: Kline, position: Position | None) -> Action | None:
        self._add(candle)
        trend, atr = self._trend, self._atr
        if position is not None:
            if trend is not None and trend is not position.side:
                return Exit()
            return None
        if trend is None or not atr:
            return None
        if not self.limit_atr:
            return Enter(trend, candle.close - trend.sign * self.stop_atr * atr)
        limit = candle.close - trend.sign * self.limit_atr * atr
        return Enter(trend, limit - trend.sign * self.stop_atr * atr, limit)

    def _add(self, candle: Kline) -> None:
        bar_start = candle.open_time - candle.open_time % self._bar_ms
        if bar_start != self._bar_start:
            self._bar_start = bar_start
            self._bar_candles = []
        self._bar_candles.append(candle)
        if (candle.open_time + self._step) % self._bar_ms:
            return  # the bar is still open
        candles = self._bar_candles
        if len(candles) != self._bar_ms // self._step or candles[0].open_time != bar_start:
            return  # a bar with a missing candle never counts
        high, low = max(k.high for k in candles), min(k.low for k in candles)
        self._bars.append(_Bar(high, low, candle.close))
        if len(self._bars) == self._bars.maxlen:
            self._update()

    def _update(self) -> None:
        bars = list(self._bars)
        closes = [b.close for b in bars[-self.trend_period :]]
        mean = sum(closes, Decimal(0)) / len(closes)
        self._trend = Side.LONG if closes[-1] > mean else Side.SHORT if closes[-1] < mean else None
        ranges = [
            max(bar.high, before.close) - min(bar.low, before.close)
            for before, bar in pairwise(bars[-(self.atr_period + 1) :])
        ]
        self._atr = sum(ranges, Decimal(0)) / len(ranges)
