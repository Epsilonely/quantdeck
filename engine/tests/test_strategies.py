from decimal import Decimal

import pytest

from quantdeck_engine.binance.streams import Kline
from quantdeck_engine.strategy.base import Enter, Exit, Position
from quantdeck_engine.strategy.bollinger import BollingerReversion
from quantdeck_engine.strategy.daily_breakout import DailyOpenBreakout
from quantdeck_engine.strategy.donchian import DonchianBreakout
from quantdeck_engine.strategy.move_fade import MoveFade
from quantdeck_engine.strategy.registry import StrategyError, make_strategy, parse_parameters
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
