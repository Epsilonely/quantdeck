from decimal import Decimal

from quantdeck_engine.binance.streams import Kline
from quantdeck_engine.chart import CandleChart, candle_values

MINUTE = 60_000
T0 = 1_788_220_800_000  # 2026-09-01 00:00 UTC


def kline(i: int, close: str = "100", *, closed: bool = True) -> Kline:
    return Kline(
        symbol="ETHUSDT",
        interval="1m",
        open_time=T0 + i * MINUTE,
        close_time=T0 + (i + 1) * MINUTE - 1,
        open=Decimal(100),
        high=Decimal(101),
        low=Decimal("99.5"),
        close=Decimal(close),
        volume=Decimal(1),
        closed=closed,
    )


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
