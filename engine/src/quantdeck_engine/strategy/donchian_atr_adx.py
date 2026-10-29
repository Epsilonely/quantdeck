"""Donchian breakout with an ATR stop or an ADX filter: turtle-style variants of the Donchian
breakout (GitHub issue #26). The defaults are the combination the user chose to make settable,
a 1.5 ATR stop with ADX 20; setting either to 0 drops that part.

Entries and exits are the Donchian breakout's (`donchian.py`): a close beyond the extreme of
the previous `entry_period` candles enters, and a close beyond the previous `exit_period`
candles' channel against the position exits. With `stop_atr` above 0 the stop sits
`stop_atr` ATR beyond the entry candle's close instead of at the exit channel's opposite
extreme; ATR is the mean true range of the last `atr_period` candles, the entry candle
included. With `adx_min` above 0 an entry is skipped while Wilder's ADX over `adx_period`
is below it; exits never wait for it. With both at 0 it acts exactly as the Donchian
breakout.

Wilder's smoothing never forgets, so an ADX carried from the first candle would differ after
a restart (D-36). It is computed afresh from the last `ADX_WINDOW_PERIODS` × `adx_period`
candles whenever an entry needs it; by the window's end its starting values weigh about
0.01%. Only the smoothing is redone: each candle's directional movement is the same in every
window, so it is computed once, as the candle arrives.
"""

from collections import deque
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from itertools import pairwise

from quantdeck_engine.binance.streams import Kline
from quantdeck_engine.strategy.base import Action, ChartLine, Enter, Exit, Position, parameter
from quantdeck_engine.strategy.donchian import ChannelWindow, channel_lines, channels
from quantdeck_engine.strategy.indicators import average_true_range
from quantdeck_engine.trading.rules import Side

ADX_WINDOW_PERIODS = 11


@dataclass
class DonchianAtrAdx:
    interval: str = "4h"
    entry_period: int = parameter(
        55,
        "진입 채널의 길이(봉 수). 종가가 직전 이 개수 봉의 최고가 위에서 마감하면 롱, "
        "최저가 아래에서 마감하면 숏으로 진입합니다.",
    )
    exit_period: int = parameter(
        20,
        "청산 채널의 길이(봉 수)로, 진입 채널 이하여야 합니다. 종가가 직전 이 개수 봉의 "
        "채널을 포지션 반대쪽으로 벗어나면 청산합니다. ATR 스톱이 0이면 스톱도 진입할 때 "
        "이 채널의 반대쪽 끝(롱이면 최저가)에 둡니다.",
    )
    stop_atr: Decimal = parameter(
        Decimal("1.5"),
        "스톱 거리(ATR 배수). 진입 신호 봉의 종가에서 이만큼 떨어진 곳에 스톱을 두고 옮기지 "
        "않습니다. 0이면 돈치안처럼 청산 채널의 반대쪽 끝에 둡니다.",
    )
    atr_period: int = parameter(
        20, "ATR(봉의 평균 변동폭)을 계산하는 봉 수. 진입 신호 봉까지 포함합니다."
    )
    adx_period: int = parameter(
        14,
        "ADX(추세 강도)를 계산하는 봉 수. 와일더 방식이며, 이 값의 11배만큼의 최근 봉으로 "
        "매번 새로 계산합니다.",
    )
    adx_min: Decimal = parameter(
        Decimal(20),
        "진입에 필요한 최소 ADX. ADX가 이보다 낮으면(추세가 약하면) 진입 신호를 무시하고, "
        "청산은 기다리지 않습니다. 0이면 필터를 쓰지 않습니다.",
    )
    _previous: deque[Kline] = field(init=False, repr=False)
    _window: ChannelWindow = field(init=False, repr=False)
    # The directional movements between the candles in `_previous`, as many as the ADX window
    # holds besides the current candle's.
    _moves: deque[tuple[Decimal, Decimal]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not 1 <= self.exit_period <= self.entry_period:
            raise ValueError("Donchian periods need 1 <= exit_period <= entry_period")
        if self.stop_atr < 0 or self.atr_period < 1:
            raise ValueError("the ATR stop needs stop_atr >= 0 and atr_period >= 1")
        if self.adx_min < 0 or self.adx_period < 2:
            raise ValueError("the ADX filter needs adx_min >= 0 and adx_period >= 2")
        self._previous = deque(maxlen=self.warmup - 1)
        self._window = ChannelWindow(self.entry_period, self.exit_period)
        self._moves = deque(maxlen=ADX_WINDOW_PERIODS * self.adx_period - 2)

    @property
    def warmup(self) -> int:
        needed = self.entry_period + 1
        if self.stop_atr:
            needed = max(needed, self.atr_period + 1)
        if self.adx_min:
            needed = max(needed, ADX_WINDOW_PERIODS * self.adx_period)
        return needed

    @property
    def limit_entries(self) -> bool:
        return False

    @property
    def chart_lines(self) -> tuple[ChartLine, ...]:
        """The channels as the Donchian breakout draws them, and the ADX while flat if the
        filter is on. The ATR stop is set once at entry, where the stop line shows it."""
        lines = channel_lines(self.entry_period, self.exit_period)
        if self.adx_min:
            label = f"ADX ({self.adx_period}봉) · 진입 최소 {format(self.adx_min, 'f')}"
            lines += (ChartLine("adx", label, ("flat",), "indicator", self.adx_min),)
        return lines

    def levels(self, previous: Sequence[Kline], candle: Kline | None) -> dict[str, Decimal]:
        found = channels(previous, self.entry_period, self.exit_period)
        values: dict[str, Decimal] = {} if found is None else asdict(found)
        if self.adx_min and candle is not None:
            window = self._adx_window([*previous, candle])
            if len(window) == ADX_WINDOW_PERIODS * self.adx_period:
                values["adx"] = adx(window, self.adx_period)
        return values

    def on_candle(self, candle: Kline, position: Position | None) -> Action | None:
        try:
            return self._decide(candle, position)
        finally:
            if self.adx_min and self._previous:
                self._moves.append(directional_move(self._previous[-1], candle))
            self._previous.append(candle)
            self._window.add(candle)

    def _decide(self, candle: Kline, position: Position | None) -> Action | None:
        previous = self._previous
        if len(previous) < (previous.maxlen or 0):
            return None
        found = self._window.channels()
        assert found is not None  # the warm-up covers the entry channel
        if position is not None:
            if position.side is Side.LONG and candle.close < found.exit_low:
                return Exit()
            if position.side is Side.SHORT and candle.close > found.exit_high:
                return Exit()
            return None
        if candle.close > found.entry_high:
            side, channel_stop = Side.LONG, found.exit_low
        elif candle.close < found.entry_low:
            side, channel_stop = Side.SHORT, found.exit_high
        else:
            return None
        # Only on a breakout, since it reads the whole window; a chart asks for it every candle.
        if self.adx_min:
            moves = [*self._moves, directional_move(previous[-1], candle)]
            if adx_of_moves(moves, self.adx_period) < self.adx_min:
                return None
        if not self.stop_atr:
            return Enter(side, channel_stop)
        atr = average_true_range([*(previous[i] for i in range(-self.atr_period, 0)), candle])
        if not atr:
            return None
        return Enter(side, candle.close - side.sign * self.stop_atr * atr)

    def _adx_window(self, candles: list[Kline]) -> list[Kline]:
        """The last candles the ADX is computed from, the current one last."""
        return candles[-ADX_WINDOW_PERIODS * self.adx_period :]


def adx(candles: Sequence[Kline], period: int) -> Decimal:
    """Wilder's ADX at the last of `candles`, starting his smoothing at the first."""
    return adx_of_moves([directional_move(before, k) for before, k in pairwise(candles)], period)


def directional_move(before: Kline, candle: Kline) -> tuple[Decimal, Decimal]:
    """+DM and −DM from `before` to `candle`."""
    up, down = candle.high - before.high, before.low - candle.low
    return (
        up if up > down and up > 0 else Decimal(0),
        down if down > up and down > 0 else Decimal(0),
    )


def adx_of_moves(moves: Sequence[tuple[Decimal, Decimal]], period: int) -> Decimal:
    """Wilder's ADX from the directional movements between consecutive candles, oldest
    first, starting his smoothing at the first.

    +DI and −DI share the smoothed true range as their denominator, which cancels in DX, so
    only the directional movements are smoothed. The period is a Decimal once, so the loops
    don't convert it at every step; the results are the same, since the conversion is exact.
    """
    if len(moves) < 2 * period - 1:
        raise ValueError(f"ADX({period}) needs at least {2 * period} candles")
    n, n_less_one = Decimal(period), Decimal(period - 1)
    plus = sum((p for p, _ in moves[:period]), Decimal(0))
    minus = sum((m for _, m in moves[:period]), Decimal(0))
    dxs = [_dx(plus, minus)]
    for p, m in moves[period:]:
        plus += p - plus / n
        minus += m - minus / n
        dxs.append(_dx(plus, minus))
    value = sum(dxs[:period], Decimal(0)) / n
    for dx in dxs[period:]:
        value = (value * n_less_one + dx) / n
    return value


def _dx(plus: Decimal, minus: Decimal) -> Decimal:
    total = plus + minus
    return abs(plus - minus) / total * 100 if total else Decimal(0)
