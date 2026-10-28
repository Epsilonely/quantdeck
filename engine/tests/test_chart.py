from decimal import Decimal

from quantdeck_engine.binance.streams import Kline
from quantdeck_engine.chart import CandleChart, candle_values
from quantdeck_engine.strategy.bollinger import BollingerReversion
from quantdeck_engine.strategy.donchian import DonchianBreakout
from quantdeck_engine.strategy.donchian_atr_adx import DonchianAtrAdx

MINUTE = 60_000
T0 = 1_788_220_800_000  # 2026-09-01 00:00 UTC


def kline(
    i: int, close: str = "100", *, high: str = "101", low: str = "99.5", closed: bool = True
) -> Kline:
    return Kline(
        symbol="ETHUSDT",
        interval="1m",
        open_time=T0 + i * MINUTE,
        close_time=T0 + (i + 1) * MINUTE - 1,
        open=Decimal(100),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(1),
        closed=closed,
    )


def stepping(n: int) -> list[Kline]:
    """Candle i reaches 100 + i at the high and 90 + i at the low."""
    return [kline(i, high=str(100 + i), low=str(90 + i)) for i in range(n)]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_candle_values_are_fixed_point_text() -> None:
    assert candle_values(kline(0, "1E+2")) == {
        "open_time": "2026-09-01T00:00:00+00:00",
        "open": "100",
        "high": "101",
        "low": "99.5",
        "close": "100",
    }


def test_payload_keeps_the_last_candles_and_is_rebuilt_only_on_change() -> None:
    chart = CandleChart("ETHUSDT", "1m", size=3)
    assert chart.payload is None  # before the warm-up

    chart.reset([kline(i) for i in range(5)])
    first = chart.payload
    assert first is not None
    assert (first["symbol"], first["interval"]) == ("ETHUSDT", "1m")
    assert [c["open_time"][11:16] for c in first["candles"]] == ["00:02", "00:03", "00:04"]
    assert chart.payload is first

    chart.add_closed(kline(4))  # already there
    assert chart.payload is first
    chart.add_closed(kline(5))
    second = chart.payload
    assert second is not first
    assert second is not None
    assert [c["open_time"][11:16] for c in second["candles"]] == ["00:03", "00:04", "00:05"]


def test_forming_candle_shows_at_most_once_per_interval() -> None:
    clock = Clock()
    chart = CandleChart("ETHUSDT", "1m", clock=clock, forming_interval=1.0)
    chart.reset([kline(0)])
    assert chart.forming() is None

    chart.set_forming(kline(1, "100.1", closed=False))
    assert chart.forming() == kline(1, "100.1", closed=False)
    chart.set_forming(kline(1, "100.2", closed=False))
    clock.now += 0.5
    assert chart.forming() == kline(1, "100.1", closed=False)  # too soon for the newer one
    clock.now += 0.5
    assert chart.forming() == kline(1, "100.2", closed=False)


def test_forming_candle_goes_once_it_closes_or_the_stream_stops() -> None:
    clock = Clock()
    chart = CandleChart("ETHUSDT", "1m", clock=clock)
    chart.reset([kline(0)])
    chart.set_forming(kline(1, closed=False))
    assert chart.forming() is not None

    chart.add_closed(kline(1))
    assert chart.forming() is None  # now among the closed candles

    clock.now += 1
    chart.set_forming(kline(2, closed=False))
    assert chart.forming() == kline(2, closed=False)
    clock.now += 1
    chart.set_forming(None)
    assert chart.forming() is None


# The strategy's lines (D-56)


def test_lines_carry_each_candles_levels_from_the_candles_before_it() -> None:
    chart = CandleChart(
        "ETHUSDT", "1m", strategy=DonchianBreakout(entry_period=3, exit_period=2), size=3
    )
    assert chart.lookback == 3  # the strategy's warm-up of 4, less the candle itself
    chart.reset(stepping(10))
    payload = chart.payload
    assert payload is not None
    assert [c["open_time"][11:16] for c in payload["candles"]] == ["00:07", "00:08", "00:09"]
    lines = {line["key"]: line for line in payload["lines"]}
    assert [line["key"] for line in payload["lines"]] == [
        "entry_high",
        "entry_low",
        "exit_low",
        "exit_high",
    ]
    # Candle 7's entry channel is candles 4 to 6, kept for it though not shown.
    assert lines["entry_high"]["values"] == ["106", "107", "108"]
    assert lines["entry_low"]["values"] == ["94", "95", "96"]
    assert lines["exit_low"]["values"] == ["95", "96", "97"]
    assert lines["entry_high"]["next"] == "109"  # candles 7 to 9 fix the forming one's
    assert lines["exit_low"] | {"values": None, "next": None} == {
        "key": "exit_low",
        "label": "청산선 (직전 2봉 최저가)",
        "when": ["long"],
        "pane": "price",
        "threshold": None,
        "values": None,
        "next": None,
    }

    chart.add_closed(kline(10, high="200", low="10"))
    payload = chart.payload
    assert payload is not None
    entry_high = payload["lines"][0]
    assert (entry_high["values"], entry_high["next"]) == (["107", "108", "109"], "200")


def test_lines_are_null_where_the_history_is_too_short() -> None:
    chart = CandleChart(
        "ETHUSDT", "1m", strategy=DonchianBreakout(entry_period=3, exit_period=2), size=5
    )
    chart.reset(stepping(4))
    assert chart.payload is not None
    assert chart.payload["lines"][0]["values"] == [None, None, None, "102"]


def test_levels_are_rounded_and_the_forming_candle_gets_none_it_doesnt_fix() -> None:
    chart = CandleChart("ETHUSDT", "1m", strategy=BollingerReversion(period=3), size=1)
    chart.reset([kline(0, "100"), kline(1, "100"), kline(2, "101")])
    assert chart.payload is not None
    middle = chart.payload["lines"][2]
    assert (middle["key"], middle["when"]) == ("middle", ["long", "short"])
    assert middle["values"] == ["100.33333333"]
    assert middle["next"] is None  # its bands take the close still to come


def test_an_indicator_line_carries_its_pane_and_threshold() -> None:
    strategy = DonchianAtrAdx(adx_period=2, adx_min=Decimal("20.0"))
    chart = CandleChart("ETHUSDT", "1m", strategy=strategy, size=2)
    chart.reset(stepping(strategy.warmup + 1))
    assert chart.payload is not None
    adx_line = chart.payload["lines"][-1]
    assert (adx_line["key"], adx_line["pane"], adx_line["threshold"]) == ("adx", "indicator", "20")
    assert all(value is not None for value in adx_line["values"])


def test_a_chart_without_a_strategy_has_no_lines() -> None:
    chart = CandleChart("ETHUSDT", "1m", size=2)
    assert chart.lookback == 0
    chart.reset([kline(i) for i in range(5)])
    assert chart.payload is not None
    assert chart.payload["lines"] == []
    assert len(chart.payload["candles"]) == 2
