from decimal import Decimal

import pytest

from quantdeck_engine.binance.streams import Kline
from quantdeck_engine.strategy.base import Enter, Exit, Position
from quantdeck_engine.strategy.bollinger import BollingerReversion
from quantdeck_engine.strategy.donchian import DonchianBreakout
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
