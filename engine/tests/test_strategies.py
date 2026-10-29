import random
from decimal import Decimal

import pytest

from quantdeck_engine.binance.streams import Kline
from quantdeck_engine.strategy.base import Action, Enter, Exit, Position, Strategy
from quantdeck_engine.strategy.bollinger import BollingerReversion
from quantdeck_engine.strategy.daily_breakout import DailyOpenBreakout
from quantdeck_engine.strategy.donchian import DonchianBreakout
from quantdeck_engine.strategy.donchian_atr_adx import DonchianAtrAdx, adx
from quantdeck_engine.strategy.move_fade import MoveFade
from quantdeck_engine.strategy.registry import (
    StrategyError,
    make_strategy,
    parse_parameter_grid,
    parse_parameters,
)
from quantdeck_engine.strategy.trend_pullback import TrendPullback
from quantdeck_engine.trading.rules import Side

HOUR_MS = 60 * 60 * 1000


def candle(i: int, high: str, low: str, close: str) -> Kline:
    return Kline(
        symbol="BTCUSDT",
        interval="1h",
        open_time=i * HOUR_MS,
        close_time=(i + 1) * HOUR_MS - 1,
        open=Decimal(close),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(1),
        closed=True,
    )


def ranging(n: int) -> list[Kline]:
    """Candles between 99 and 101, lows rising so the last few are the highest lows."""
    return [candle(i, "101", str(Decimal("99") + Decimal(i) / 100), "100") for i in range(n)]


LONG = Position(Side.LONG, Decimal(1), Decimal(100))
SHORT = Position(Side.SHORT, Decimal(1), Decimal(100))


def test_donchian_waits_for_its_warmup() -> None:
    strategy = DonchianBreakout(entry_period=4, exit_period=2)
    assert strategy.warmup == 5
    actions = [strategy.on_candle(k, None) for k in ranging(4)]
    assert actions == [None] * 4


def test_donchian_enters_on_a_close_beyond_the_channel_with_the_exit_channel_as_stop() -> None:
    strategy = DonchianBreakout(entry_period=4, exit_period=2)
    for k in ranging(4):
        strategy.on_candle(k, None)

    action = strategy.on_candle(candle(4, "103", "100", "102"), None)

    # The stop is the lowest low of the previous 2 candles (99.02 and 99.03).
    assert action == Enter(Side.LONG, Decimal("99.02"))


def test_donchian_short_and_no_entry_inside_the_channel() -> None:
    strategy = DonchianBreakout(entry_period=4, exit_period=2)
    for k in ranging(4):
        strategy.on_candle(k, None)
    assert strategy.on_candle(candle(4, "100.5", "99.5", "100"), None) is None
    assert strategy.on_candle(candle(5, "99", "97", "98"), None) == Enter(
        Side.SHORT, Decimal("101")
    )


def test_donchian_exits_a_long_on_a_close_below_the_exit_channel() -> None:
    strategy = DonchianBreakout(entry_period=4, exit_period=2)
    for k in ranging(4):
        strategy.on_candle(k, LONG)
    assert strategy.on_candle(candle(4, "100", "99.1", "99.5"), LONG) is None
    assert strategy.on_candle(candle(5, "99.5", "98", "99.0"), LONG) == Exit()


def test_donchian_ignores_entries_while_holding() -> None:
    strategy = DonchianBreakout(entry_period=4, exit_period=2)
    for k in ranging(4):
        strategy.on_candle(k, SHORT)
    # A breakout up while short: the exit channel is crossed, so it exits; never re-enters.
    assert strategy.on_candle(candle(4, "103", "100", "102"), SHORT) == Exit()


def test_donchian_rejects_bad_periods() -> None:
    with pytest.raises(ValueError):
        DonchianBreakout(entry_period=5, exit_period=6)


def closes(values: list[str]) -> list[Kline]:
    return [candle(i, v, v, v) for i, v in enumerate(values)]


def test_bollinger_enters_against_a_close_outside_the_bands() -> None:
    strategy = BollingerReversion(period=4, width=Decimal(1), stop_width=Decimal(1))
    assert strategy.warmup == 4
    for k in closes(["100", "102", "100"]):
        assert strategy.on_candle(k, None) is None

    action = strategy.on_candle(candle(3, "96", "96", "96"), None)

    # Closes 100, 102, 100, 96: mean 99.5, standard deviation sqrt(4.75) ~ 2.179.
    assert isinstance(action, Enter)
    assert action.side is Side.LONG
    deviation = Decimal("4.75").sqrt()
    assert action.stop_price == Decimal(96) - deviation


def test_bollinger_short_above_the_upper_band() -> None:
    strategy = BollingerReversion(period=4, width=Decimal(1), stop_width=Decimal("0.5"))
    for k in closes(["100", "98", "100"]):
        strategy.on_candle(k, None)
    action = strategy.on_candle(candle(3, "104", "104", "104"), None)
    assert isinstance(action, Enter)
    assert action.side is Side.SHORT
    assert action.stop_price > Decimal(104)


def test_bollinger_exits_at_the_middle_band() -> None:
    strategy = BollingerReversion(period=4, width=Decimal(2))
    for k in closes(["100", "100", "96"]):
        strategy.on_candle(k, LONG)
    assert strategy.on_candle(candle(3, "97", "97", "97"), LONG) is None  # mean 98.25
    assert strategy.on_candle(candle(4, "100", "100", "100"), LONG) == Exit()  # mean 98.25


def test_bollinger_does_nothing_on_a_flat_window() -> None:
    strategy = BollingerReversion(period=3)
    actions = [strategy.on_candle(k, None) for k in closes(["100", "100", "100"])]
    assert actions == [None, None, None]


def test_bollinger_limit_offset_puts_the_limit_beyond_the_close_and_the_stop_beyond_it() -> None:
    strategy = BollingerReversion(
        period=4, width=Decimal(1), stop_width=Decimal(1), limit_offset=Decimal("0.5")
    )
    assert strategy.limit_entries
    for k in closes(["100", "102", "100"]):
        strategy.on_candle(k, None)

    action = strategy.on_candle(candle(3, "96", "96", "96"), None)

    deviation = Decimal("4.75").sqrt()
    limit = Decimal(96) - Decimal("0.5") * deviation
    assert action == Enter(Side.LONG, limit - deviation, limit)


def test_bollinger_limit_offset_short_sells_above_the_close() -> None:
    strategy = BollingerReversion(
        period=4, width=Decimal(1), stop_width=Decimal(1), limit_offset=Decimal(1)
    )
    for k in closes(["100", "98", "100"]):
        strategy.on_candle(k, None)
    action = strategy.on_candle(candle(3, "104", "104", "104"), None)
    assert isinstance(action, Enter) and action.limit_price is not None
    assert Decimal(104) < action.limit_price < action.stop_price


def test_bollinger_without_an_offset_enters_at_market() -> None:
    assert not BollingerReversion().limit_entries
    with pytest.raises(ValueError, match="limit_offset >= 0"):
        BollingerReversion(limit_offset=Decimal("-0.5"))


# Registry (D-28, D-39)


def test_registry_builds_a_strategy_with_typed_parameters() -> None:
    strategy = make_strategy("donchian", "4h", {"entry_period": 55, "exit_period": 20})
    assert isinstance(strategy, DonchianBreakout)
    assert (strategy.interval, strategy.entry_period, strategy.exit_period) == ("4h", 55, 20)


def test_registry_takes_whole_numbers_for_decimal_parameters() -> None:
    strategy = make_strategy("bollinger", "1h", {"width": 2, "stop_width": Decimal("1.5")})
    assert isinstance(strategy, BollingerReversion)
    assert strategy.width == 2 and isinstance(strategy.width, Decimal)
    assert strategy.stop_width == Decimal("1.5")


@pytest.mark.parametrize(
    "name, params, message",
    [
        ("macd", {}, "unknown strategy"),
        ("donchian", {"period": 20}, "no parameter 'period'"),
        ("donchian", {"entry_period": Decimal("20.5")}, "whole number"),
        ("donchian", {"entry_period": True}, "whole number"),
        ("bollinger", {"width": "2"}, "must be a number"),
        ("donchian", {"entry_period": 5, "exit_period": 10}, "exit_period <= entry_period"),
    ],
)
def test_registry_refuses_bad_settings(name: str, params: dict[str, object], message: str) -> None:
    with pytest.raises(StrategyError, match=message):
        make_strategy(name, "1h", params)


def test_command_line_parameters_are_typed_by_field() -> None:
    params = parse_parameters("bollinger", ["period=30", "width=2.5"])
    assert params == {"period": 30, "width": Decimal("2.5")}
    assert isinstance(params["period"], int)
    with pytest.raises(StrategyError, match="not a valid int"):
        parse_parameters("donchian", ["entry_period=x"])
    with pytest.raises(StrategyError, match="no parameter 'entry_period'"):
        parse_parameters("donchian", ["entry_period"])


def test_command_line_values_separated_by_commas_give_every_combination() -> None:
    grid = parse_parameter_grid("donchian", ["entry_period=20,55", "exit_period=10,20"])
    assert grid == [
        {"entry_period": 20, "exit_period": 10},
        {"entry_period": 20, "exit_period": 20},
        {"entry_period": 55, "exit_period": 10},
        {"entry_period": 55, "exit_period": 20},
    ]
    assert parse_parameter_grid("donchian", []) == [{}]
    with pytest.raises(StrategyError, match="not a valid int"):
        parse_parameter_grid("donchian", ["entry_period=20,"])


def test_command_line_bools_are_true_or_false() -> None:
    params = parse_parameters("daily-breakout", ["long_only=true", "k=0.8"])
    assert params == {"long_only": True, "k": Decimal("0.8")}
    strategy = make_strategy("daily-breakout", "15m", params)
    assert isinstance(strategy, DailyOpenBreakout) and strategy.long_only is True
    with pytest.raises(StrategyError, match="not a valid bool"):
        parse_parameters("daily-breakout", ["long_only=yes"])


# Daily-open breakout (#15)

QUARTER_MS = 15 * 60 * 1000
DAY_MS = 24 * HOUR_MS


def bar(start_ms: int, open_: str, high: str, low: str, close: str) -> Kline:
    return Kline(
        symbol="BTCUSDT",
        interval="15m",
        open_time=start_ms,
        close_time=start_ms + QUARTER_MS - 1,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(1),
        closed=True,
    )


def yesterday_range(strategy: DailyOpenBreakout, low: str = "90", high: str = "110") -> None:
    """Day 0: 96 candles at 100, one reaching `high` and one `low`."""
    for i in range(96):
        h = high if i == 10 else "100"
        lo = low if i == 20 else "100"
        strategy.on_candle(bar(i * QUARTER_MS, "100", h, lo, "100"), None)


def today(i: int, close: str, open_: str = "100") -> Kline:
    """Day 1's candle `i`."""
    return bar(DAY_MS + i * QUARTER_MS, open_, close, close, close)


def test_daily_breakout_warms_up_on_two_days() -> None:
    assert DailyOpenBreakout().warmup == 192
    assert DailyOpenBreakout(interval="1h").warmup == 48


def test_daily_breakout_enters_on_the_first_close_beyond_the_range_with_the_open_as_stop() -> None:
    strategy = DailyOpenBreakout(k=Decimal("0.5"))  # range 20: levels 110 and 90
    yesterday_range(strategy)
    assert strategy.on_candle(today(0, "105"), None) is None
    assert strategy.on_candle(today(1, "111"), None) == Enter(Side.LONG, Decimal(100))


def test_daily_breakout_takes_one_entry_a_day() -> None:
    strategy = DailyOpenBreakout(k=Decimal("0.5"))
    yesterday_range(strategy)
    strategy.on_candle(today(0, "100"), None)
    assert strategy.on_candle(today(1, "89"), None) == Enter(Side.SHORT, Decimal(100))
    # Stopped out: flat again, but the day's entry has come.
    assert strategy.on_candle(today(2, "112"), None) is None


def test_daily_breakout_long_only_ignores_the_lower_level() -> None:
    strategy = DailyOpenBreakout(k=Decimal("0.5"), long_only=True)
    yesterday_range(strategy)
    strategy.on_candle(today(0, "100"), None)
    assert strategy.on_candle(today(1, "89"), None) is None
    assert strategy.on_candle(today(2, "110"), None) == Enter(Side.LONG, Decimal(100))


def test_daily_breakout_exits_on_the_last_candle_of_the_day() -> None:
    strategy = DailyOpenBreakout()
    yesterday_range(strategy)
    for i in range(95):
        assert strategy.on_candle(today(i, "100"), LONG) is None
    assert strategy.on_candle(today(95, "100"), LONG) == Exit()


def test_daily_breakout_never_enters_on_the_last_candle_of_the_day() -> None:
    strategy = DailyOpenBreakout(k=Decimal("0.5"))
    yesterday_range(strategy)
    for i in range(95):
        strategy.on_candle(today(i, "100"), None)
    assert strategy.on_candle(today(95, "120"), None) is None


def test_daily_breakout_needs_all_of_yesterday() -> None:
    strategy = DailyOpenBreakout(k=Decimal("0.5"))
    for i in range(1, 96):  # the first candle of day 0 is missing
        strategy.on_candle(bar(i * QUARTER_MS, "100", "110", "90", "100"), None)
    strategy.on_candle(today(0, "100"), None)
    assert strategy.on_candle(today(1, "120"), None) is None


def test_daily_breakout_rejects_bad_settings() -> None:
    with pytest.raises(ValueError, match="divides a day"):
        DailyOpenBreakout(interval="1d")
    with pytest.raises(ValueError, match="k > 0"):
        DailyOpenBreakout(k=Decimal(0))


# Move fade (#15)


def fade_bars(strategy: MoveFade, returns: list[str], position: Position | None = None) -> list:
    """30-minute bars of two 15m candles each, from 100 with the given returns; the actions
    of each bar's closing candle."""
    actions = []
    for n, r in enumerate(returns):
        start = n * 2 * QUARTER_MS
        close = str(Decimal(100) * (1 + Decimal(r)))
        strategy.on_candle(bar(start, "100", "100", "100", "100"), position)
        actions.append(
            strategy.on_candle(bar(start + QUARTER_MS, "100", close, close, close), position)
        )
    return actions


def test_move_fade_warms_up_on_the_lookback_and_the_current_bar() -> None:
    assert MoveFade().warmup == 968
    assert MoveFade(bar_minutes=60).warmup == 484


def test_move_fade_buys_a_drop_of_k_sigma_with_the_same_move_as_stop() -> None:
    strategy = MoveFade(bar_minutes=30, k=Decimal(2), lookback=4)
    # σ of ±1% is 1%: a 3% drop is beyond 2σ.
    actions = fade_bars(strategy, ["0.01", "-0.01", "0.01", "-0.01", "-0.03"])
    assert actions[:4] == [None] * 4
    assert actions[4] == Enter(Side.LONG, Decimal(97) * Decimal("0.97"))


def test_move_fade_sells_a_rise_and_ignores_a_small_move() -> None:
    strategy = MoveFade(bar_minutes=30, k=Decimal(2), lookback=4)
    actions = fade_bars(strategy, ["0.01", "-0.01", "0.01", "-0.01", "0.015", "0.03"])
    assert actions[4] is None  # 1.5% < 2σ
    assert actions[5] == Enter(Side.SHORT, Decimal(103) * Decimal("1.03"))


def test_move_fade_exits_when_the_next_bar_closes() -> None:
    strategy = MoveFade(bar_minutes=30, k=Decimal(2), lookback=4)
    fade_bars(strategy, ["0.01", "-0.01", "0.01", "-0.01", "-0.03"])
    assert strategy.on_candle(bar(10 * QUARTER_MS, "100", "100", "100", "100"), LONG) is None
    assert strategy.on_candle(bar(11 * QUARTER_MS, "100", "100", "100", "100"), LONG) == Exit()


def test_move_fade_skips_a_bar_with_a_missing_candle() -> None:
    strategy = MoveFade(bar_minutes=30, k=Decimal(2), lookback=4)
    fade_bars(strategy, ["0.01", "-0.01", "0.01", "-0.01"])
    # Bar 4 has only its closing candle: it neither signals nor counts toward σ.
    assert strategy.on_candle(bar(9 * QUARTER_MS, "100", "90", "90", "90"), None) is None
    assert len(strategy._returns) == 4


def test_move_fade_rejects_bad_settings() -> None:
    with pytest.raises(ValueError, match="whole number of candles"):
        MoveFade(bar_minutes=20)
    with pytest.raises(ValueError, match="whole number of candles"):
        MoveFade(bar_minutes=7 * 60)  # doesn't divide a day
    with pytest.raises(ValueError, match="lookback >= 2"):
        MoveFade(lookback=1)


# Trend pullback (#17)


def small_pullback(**kwargs: Decimal) -> TrendPullback:
    """2h bars of two 1h candles, the trend over 3 bars, ATR over 2."""
    return TrendPullback(bar_minutes=120, trend_period=3, atr_period=2, **kwargs)


def feed_bars(
    strategy: TrendPullback,
    bars: list[tuple[str, str, str]],
    position: Position | None = None,
    first: int = 0,
) -> list:
    """Each bar (high, low, close) as two equal 1h candles; the actions of every candle."""
    actions = []
    for n, (high, low, close) in enumerate(bars, start=first):
        for i in (2 * n, 2 * n + 1):
            actions.append(strategy.on_candle(candle(i, high, low, close), position))
    return actions


RISING = [("101", "99", "100"), ("103", "101", "102"), ("105", "103", "104")]
FALLING = [("105", "103", "104"), ("103", "101", "102"), ("101", "99", "100")]


def test_trend_pullback_warms_up_on_its_bars() -> None:
    assert TrendPullback().warmup == 224  # (55 + 1) 4h bars of 1h candles
    assert small_pullback().warmup == 8


def test_trend_pullback_buys_below_the_close_in_an_uptrend() -> None:
    strategy = small_pullback(limit_atr=Decimal("0.5"), stop_atr=Decimal(2))
    assert strategy.limit_entries
    actions = feed_bars(strategy, RISING)

    # Closes 100, 102, 104: the last is above the mean (102). True ranges 3 and 3: ATR 3.
    assert actions[:5] == [None] * 5
    assert actions[5] == Enter(Side.LONG, Decimal("96.5"), Decimal("102.5"))
    # Inside the next bar it quotes again from the latest close.
    assert strategy.on_candle(candle(6, "106", "104", "105"), None) == Enter(
        Side.LONG, Decimal("97.5"), Decimal("103.5")
    )


def test_trend_pullback_sells_above_the_close_in_a_downtrend() -> None:
    strategy = small_pullback(limit_atr=Decimal(1), stop_atr=Decimal(3))
    actions = feed_bars(strategy, FALLING)
    assert actions[5] == Enter(Side.SHORT, Decimal(112), Decimal(103))


def test_trend_pullback_without_a_limit_enters_at_market() -> None:
    strategy = small_pullback(limit_atr=Decimal(0), stop_atr=Decimal(2))
    assert not strategy.limit_entries
    assert feed_bars(strategy, RISING)[5] == Enter(Side.LONG, Decimal(98))


def test_trend_pullback_exits_when_a_bar_closes_across_the_mean() -> None:
    strategy = small_pullback()
    feed_bars(strategy, RISING, LONG)
    # Closes 102, 104, 98: the mean is about 101.3, so the trend turns down at the bar's close.
    actions = feed_bars(strategy, [("104", "97", "98")], LONG, first=3)
    assert actions == [None, Exit()]


def test_trend_pullback_closes_a_position_against_the_trend() -> None:
    strategy = small_pullback()
    actions = feed_bars(strategy, RISING, SHORT)
    assert actions[5] == Exit()


def test_trend_pullback_skips_a_bar_with_a_missing_candle() -> None:
    strategy = small_pullback()
    feed_bars(strategy, RISING[:1])
    strategy.on_candle(candle(3, "103", "101", "102"), None)  # bar 1 lacks its first candle
    actions = feed_bars(strategy, RISING[2:], first=2)
    assert actions == [None, None]
    assert len(strategy._bars) == 2


def test_trend_pullback_rejects_bad_settings() -> None:
    with pytest.raises(ValueError, match="whole number of candles"):
        TrendPullback(bar_minutes=90)
    with pytest.raises(ValueError, match="trend_period >= 2"):
        TrendPullback(trend_period=1)
    with pytest.raises(ValueError, match="limit_atr >= 0"):
        TrendPullback(limit_atr=Decimal(-1))
    with pytest.raises(ValueError, match="stop_atr > 0"):
        TrendPullback(stop_atr=Decimal(0))


def random_walk(n: int, seed: int) -> list[Kline]:
    rng = random.Random(seed)
    price = Decimal(100)
    candles = []
    for i in range(n):
        close = price + Decimal(rng.randint(-100, 100)) / 100
        high = max(price, close) + Decimal(rng.randint(0, 50)) / 100
        low = min(price, close) - Decimal(rng.randint(0, 50)) / 100
        candles.append(candle(i, str(high), str(low), str(close)))
        price = close
    return candles


def test_donchian_atr_adx_without_stop_or_filter_acts_as_the_donchian_breakout() -> None:
    plain = DonchianBreakout(entry_period=20, exit_period=10)
    variant = DonchianAtrAdx(
        entry_period=20, exit_period=10, stop_atr=Decimal(0), adx_min=Decimal(0)
    )
    assert variant.warmup == plain.warmup
    position: Position | None = None
    entries = exits = 0
    for k in random_walk(3000, seed=7):
        action = plain.on_candle(k, position)
        assert variant.on_candle(k, position) == action
        if isinstance(action, Enter):
            position, entries = Position(action.side, Decimal(1), k.close), entries + 1
        elif isinstance(action, Exit):
            position, exits = None, exits + 1
    assert entries > 20 and exits > 20


def test_donchian_atr_adx_defaults_to_the_atr_stop_with_the_adx_filter() -> None:
    strategy = DonchianAtrAdx()
    assert (strategy.interval, strategy.entry_period, strategy.exit_period) == ("4h", 55, 20)
    assert (strategy.stop_atr, strategy.adx_min) == (Decimal("1.5"), Decimal(20))
    assert not strategy.limit_entries


def test_donchian_atr_adx_warmup_covers_the_atr_and_the_adx() -> None:
    no_adx = Decimal(0)
    assert DonchianAtrAdx(entry_period=4, exit_period=2, atr_period=10, adx_min=no_adx).warmup == 11
    assert DonchianAtrAdx(entry_period=55, exit_period=20, adx_min=no_adx).warmup == 56
    assert DonchianAtrAdx(entry_period=55, exit_period=20).warmup == 11 * 14


def atr_only() -> DonchianAtrAdx:
    """A 2 ATR stop over the last 2 candles, without the ADX filter."""
    return DonchianAtrAdx(
        entry_period=4, exit_period=2, stop_atr=Decimal(2), atr_period=2, adx_min=Decimal(0)
    )


def test_donchian_atr_adx_puts_the_stop_atr_beyond_the_close() -> None:
    strategy = atr_only()
    for k in ranging(4):
        assert strategy.on_candle(k, None) is None

    action = strategy.on_candle(candle(4, "103", "100", "102"), None)

    # True ranges of the last 2 candles: 101 - 99.03 and 103 - 100, so ATR 2.485.
    assert action == Enter(Side.LONG, Decimal("97.03"))


def test_donchian_atr_adx_short_stop_above_the_close() -> None:
    strategy = atr_only()
    for k in ranging(4):
        strategy.on_candle(k, None)
    assert strategy.on_candle(candle(4, "100.5", "99.5", "100"), None) is None

    action = strategy.on_candle(candle(5, "99", "97", "98"), None)

    # True ranges 100.5 - 99.5 and 100 - 97, so ATR 2.
    assert action == Enter(Side.SHORT, Decimal("102"))


def reference_adx(candles: list[Kline], period: int) -> Decimal:
    """Wilder's ADX over the whole history, by the textbook steps with the true range."""
    trs, pluses, minuses = [], [], []
    for before, k in zip(candles, candles[1:], strict=False):
        trs.append(max(k.high, before.close) - min(k.low, before.close))
        up, down = k.high - before.high, before.low - k.low
        pluses.append(up if up > down and up > 0 else Decimal(0))
        minuses.append(down if down > up and down > 0 else Decimal(0))
    tr, plus, minus = sum(trs[:period]), sum(pluses[:period]), sum(minuses[:period])
    dxs = []
    for i in range(period - 1, len(trs)):
        if i >= period:
            tr += trs[i] - tr / period
            plus += pluses[i] - plus / period
            minus += minuses[i] - minus / period
        plus_di, minus_di = 100 * plus / tr, 100 * minus / tr
        dxs.append(100 * abs(plus_di - minus_di) / (plus_di + minus_di))
    value = sum(dxs[:period]) / period
    for dx in dxs[period:]:
        value = (value * (period - 1) + dx) / period
    return value


def test_adx_from_its_window_matches_the_whole_history() -> None:
    candles = random_walk(600, seed=3)
    for end in (200, 400, 600):
        windowed = adx(candles[end - 11 * 14 : end], 14)
        assert abs(windowed - reference_adx(candles[:end], 14)) < Decimal("0.01")


def chop(n: int) -> list[Kline]:
    """Candles that step up and down by the same amount, so ADX stays near 0."""
    return [
        candle(i, "101", "99.5", "100") if i % 2 == 0 else candle(i, "100.5", "99", "100")
        for i in range(n)
    ]


def test_donchian_atr_adx_skips_a_breakout_while_adx_is_low() -> None:
    breakout = candle(60, "103", "100", "102")
    for adx_min, expected in ((Decimal(20), None), (Decimal(0), Enter(Side.LONG, Decimal(99)))):
        strategy = DonchianAtrAdx(
            entry_period=20, exit_period=10, stop_atr=Decimal(0), adx_period=5, adx_min=adx_min
        )
        for k in chop(60):
            strategy.on_candle(k, None)
        assert strategy.on_candle(breakout, None) == expected


def test_donchian_atr_adx_enters_a_breakout_in_a_strong_trend() -> None:
    strategy = DonchianAtrAdx(
        entry_period=20, exit_period=10, stop_atr=Decimal(0), adx_period=5, adx_min=Decimal(25)
    )
    rising = [candle(i, str(100 + i), str(99 + i), str(Decimal("99.9") + i)) for i in range(60)]
    actions = [strategy.on_candle(k, None) for k in rising]
    assert actions[-1] == Enter(Side.LONG, Decimal(99 + 49))


def test_donchian_atr_adx_exits_without_waiting_for_adx() -> None:
    strategy = DonchianAtrAdx(entry_period=20, exit_period=10, adx_period=5, adx_min=Decimal(50))
    for k in chop(60):
        strategy.on_candle(k, LONG)
    assert strategy.on_candle(candle(60, "99.5", "98", "98.5"), LONG) == Exit()


def test_donchian_atr_adx_rejects_bad_settings() -> None:
    with pytest.raises(ValueError, match="exit_period <= entry_period"):
        DonchianAtrAdx(entry_period=5, exit_period=6)
    with pytest.raises(ValueError, match="stop_atr >= 0"):
        DonchianAtrAdx(stop_atr=Decimal(-1))
    with pytest.raises(ValueError, match="atr_period >= 1"):
        DonchianAtrAdx(atr_period=0)
    with pytest.raises(ValueError, match="adx_min >= 0"):
        DonchianAtrAdx(adx_min=Decimal(-1))
    with pytest.raises(ValueError, match="adx_period >= 2"):
        DonchianAtrAdx(adx_period=1)


def test_registry_builds_the_donchian_variant() -> None:
    strategy = make_strategy("donchian-atr-adx", "4h", {"stop_atr": 0, "adx_min": 20})
    assert isinstance(strategy, DonchianAtrAdx)
    assert (strategy.stop_atr, strategy.adx_min, strategy.warmup) == (0, 20, 154)


# Chart levels (D-56): each strategy's levels are what its decisions compare against.

Replay = list[tuple[Kline, Action | None, dict[str, Decimal]]]


def replay(strategy: Strategy, candles: list[Kline], position: Position | None) -> Replay:
    """Each candle with the strategy's action on it, `position` held throughout, and the
    levels it gives for that candle from the candles before it, as a chart asks."""
    keys = {line.key for line in strategy.chart_lines}
    seen: list[Kline] = []
    results: Replay = []
    for k in candles:
        levels = strategy.levels(seen[-(strategy.warmup - 1) :], k)
        assert set(levels) <= keys
        results.append((k, strategy.on_candle(k, position), levels))
        seen.append(k)
    return results


def test_donchian_levels_are_its_channels() -> None:
    candles = random_walk(2000, seed=3)
    entries = 0
    for k, action, levels in replay(DonchianBreakout(entry_period=20), candles, None):
        if not levels:
            assert action is None
        elif k.close > levels["entry_high"]:
            assert action == Enter(Side.LONG, levels["exit_low"])
        elif k.close < levels["entry_low"]:
            assert action == Enter(Side.SHORT, levels["exit_high"])
        else:
            assert action is None
        entries += action is not None
    assert entries > 20

    for position, key in ((LONG, "exit_low"), (SHORT, "exit_high")):
        exits = 0
        for k, action, levels in replay(DonchianBreakout(entry_period=20), candles, position):
            beyond = bool(levels) and (k.close - levels[key]) * position.side.sign < 0
            assert action == (Exit() if beyond else None)
            exits += action is not None
        assert exits > 20


def test_donchian_levels_for_the_next_candle_are_already_fixed() -> None:
    strategy = DonchianBreakout(entry_period=20)
    candles = random_walk(100, seed=4)
    for i in range(len(candles)):
        previous = candles[max(i - 20, 0) : i]
        assert strategy.levels(previous, None) == strategy.levels(previous, candles[i])


def test_donchian_draws_the_entry_channel_while_flat_and_one_exit_side_while_holding() -> None:
    lines = {
        line.key: line for line in DonchianBreakout(entry_period=55, exit_period=20).chart_lines
    }
    assert {key: line.when for key, line in lines.items()} == {
        "entry_high": ("flat",),
        "entry_low": ("flat",),
        "exit_low": ("long",),
        "exit_high": ("short",),
    }
    assert lines["entry_high"].label == "진입 채널 위 (직전 55봉 최고가)"
    assert lines["exit_low"].label == "청산선 (직전 20봉 최저가)"


def test_bollinger_levels_are_its_bands() -> None:
    candles = random_walk(2000, seed=5)
    entries = 0
    for k, action, levels in replay(BollingerReversion(), candles, None):
        if not levels:
            assert action is None
        elif k.close < levels["lower"]:
            assert isinstance(action, Enter) and action.side is Side.LONG
        elif k.close > levels["upper"]:
            assert isinstance(action, Enter) and action.side is Side.SHORT
        else:
            assert action is None
        entries += action is not None
    assert entries > 20

    for position in (LONG, SHORT):
        exits = 0
        for k, action, levels in replay(BollingerReversion(), candles, position):
            back = bool(levels) and (k.close - levels["middle"]) * position.side.sign >= 0
            assert action == (Exit() if back else None)
            exits += action is not None
        assert exits > 20


def test_bollinger_has_no_levels_for_a_close_still_to_come() -> None:
    strategy = BollingerReversion(period=4)
    assert strategy.levels(closes(["100", "102", "100", "96"]), None) == {}
    assert [line.when for line in strategy.chart_lines] == [
        ("flat",),
        ("flat",),
        ("long", "short"),
    ]
    assert strategy.chart_lines[0].label == "위 밴드 (4봉 평균 + 2σ)"


def test_donchian_atr_adx_levels_are_its_channels_and_adx() -> None:
    candles = random_walk(3000, seed=6)
    strategy = DonchianAtrAdx(entry_period=20, exit_period=10, adx_period=5, adx_min=Decimal(40))
    taken = skipped = 0
    for i, (k, action, levels) in enumerate(replay(strategy, candles, None)):
        if i < strategy.warmup - 1:
            continue  # it waits for its whole warm-up, which a chart has
        side = None
        if k.close > levels["entry_high"]:
            side = Side.LONG
        elif k.close < levels["entry_low"]:
            side = Side.SHORT
        if side is not None and levels["adx"] >= strategy.adx_min:
            assert isinstance(action, Enter) and action.side is side
            taken += 1
        else:
            assert action is None
            skipped += side is not None
    assert taken > 10 and skipped > 10

    for position, key in ((LONG, "exit_low"), (SHORT, "exit_high")):
        strategy = DonchianAtrAdx(entry_period=20, exit_period=10, adx_period=5)
        for i, (k, action, levels) in enumerate(replay(strategy, candles, position)):
            if i >= strategy.warmup - 1:
                beyond = (k.close - levels[key]) * position.side.sign < 0
                assert action == (Exit() if beyond else None)


def test_donchian_atr_adx_draws_adx_only_with_the_filter_on() -> None:
    lines = DonchianAtrAdx(adx_period=14, adx_min=Decimal(20)).chart_lines
    (adx_line,) = [line for line in lines if line.key == "adx"]
    assert (adx_line.when, adx_line.pane, adx_line.threshold) == (
        ("flat",),
        "indicator",
        Decimal(20),
    )
    assert adx_line.label == "ADX (14봉) · 진입 최소 20"

    without = DonchianAtrAdx(adx_min=Decimal(0))
    assert "adx" not in {line.key for line in without.chart_lines}
    candles = random_walk(200, seed=1)
    assert "adx" not in without.levels(candles[:-1], candles[-1])


def test_daily_breakout_levels_are_the_days_entry_levels() -> None:
    candles = random_walk(60 * 24, seed=8)  # 60 days of 1h candles from 00:00 UTC
    entries = 0
    strategy = DailyOpenBreakout(interval="1h", k=Decimal("0.3"))
    for k, action, levels in replay(strategy, candles, None):
        if "upper" in levels and k.close >= levels["upper"]:
            assert isinstance(action, Enter) and action.side is Side.LONG
        elif "lower" in levels and k.close <= levels["lower"]:
            assert isinstance(action, Enter) and action.side is Side.SHORT
        else:
            assert action is None
        entries += action is not None
    assert entries > 20


def test_daily_breakout_levels_end_once_the_days_entry_has_come() -> None:
    yesterday = [
        bar(i * QUARTER_MS, "100", "110" if i == 10 else "100", "100", "100") for i in range(96)
    ]
    levels = {"upper": Decimal(105), "lower": Decimal(95)}  # range 10 from the open at 100
    strategy = DailyOpenBreakout(k=Decimal("0.5"))
    assert strategy.levels(yesterday, None) == {}  # the next candle's open isn't known yet
    assert strategy.levels(yesterday, today(0, "100")) == levels
    assert strategy.levels([*yesterday, today(0, "100")], None) == levels
    assert strategy.levels([*yesterday, today(0, "100"), today(1, "106")], None) == {}

    long_only = DailyOpenBreakout(k=Decimal("0.5"), long_only=True)
    assert long_only.levels(yesterday, today(0, "100")) == {"upper": Decimal(105)}
    assert [line.key for line in long_only.chart_lines] == ["upper"]


def test_strategies_deciding_on_bars_draw_nothing() -> None:
    candles = random_walk(5, seed=1)
    for strategy in (MoveFade(), TrendPullback()):
        assert strategy.chart_lines == ()
        assert strategy.levels(candles[:-1], candles[-1]) == {}
