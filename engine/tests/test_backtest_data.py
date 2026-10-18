import hashlib
import io
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from quantdeck_engine.backtest.data import (
    ARCHIVE_URL,
    DAY_MS,
    DataError,
    HistoricalData,
    archive_path,
    day_ms,
    read_klines,
)
from quantdeck_engine.config import MAINNET

HOUR_MS = 60 * 60 * 1000
HEADER = (
    "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
    "taker_buy_volume,taker_buy_quote_volume,ignore"
)


def row(open_ms: int, step_ms: int, price: str = "100.0") -> list[str]:
    return [
        str(open_ms),
        price,
        price,
        price,
        price,
        "1.5",
        str(open_ms + step_ms - 1),
        "150",
        "10",
        "0.5",
        "50",
        "0",
    ]


def make_zip(
    name: str, rows: list[list[str]], *, header: bool = False, header_row: str = HEADER
) -> bytes:
    lines = ([header_row] if header else []) + [",".join(r) for r in rows]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr(name.replace(".zip", ".csv"), "\n".join(lines) + "\n")
    return buffer.getvalue()


class FakeSources:
    """The archive and mainnet REST, served from dicts."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.rest_klines: list[list[object]] = []
        self.rest_mark_klines: list[list[object]] = []
        self.rest_funding: list[dict[str, object]] = []
        self.funding_page_cap = 1000  # rows per response, whatever `limit` asks
        self.server_ms = 0
        self.requests: list[str] = []

    def publish(
        self, path: str, rows: list[list[str]], *, header: bool = False, header_row: str = HEADER
    ) -> None:
        name = path.rsplit("/", 1)[1]
        content = make_zip(name, rows, header=header, header_row=header_row)
        self.files[path] = content
        digest = hashlib.sha256(content).hexdigest()
        self.files[path + ".CHECKSUM"] = f"{digest}  {name}\n".encode()

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if url.startswith(ARCHIVE_URL):
            path = request.url.path.lstrip("/")
            if path in self.files:
                return httpx.Response(200, content=self.files[path])
            return httpx.Response(404)
        assert url.startswith(MAINNET.rest)
        if request.url.path == "/fapi/v1/time":
            return httpx.Response(200, json={"serverTime": self.server_ms})
        start = int(request.url.params["startTime"])
        end = int(request.url.params["endTime"])
        limit = int(request.url.params["limit"])
        if request.url.path == "/fapi/v1/fundingRate":
            funding = [r for r in self.rest_funding if start <= int(str(r["fundingTime"])) <= end]
            return httpx.Response(200, json=funding[: min(limit, self.funding_page_cap)])
        klines = {
            "/fapi/v1/klines": self.rest_klines,
            "/fapi/v1/markPriceKlines": self.rest_mark_klines,
        }[request.url.path]
        rows = [r for r in klines if start <= int(str(r[0])) <= end]
        return httpx.Response(200, json=rows[:limit])

    def data(self, data_dir: Path) -> HistoricalData:
        return HistoricalData(httpx.Client(transport=httpx.MockTransport(self.handler)), data_dir)


def rest_row(open_ms: int, step_ms: int, price: str = "100.0") -> list[object]:
    r: list[object] = list(row(open_ms, step_ms, price))
    r[0], r[6] = open_ms, open_ms + step_ms - 1  # REST sends times as numbers
    return r


def test_archive_paths() -> None:
    assert (
        archive_path("klines", "BTCUSDT", "2026-09", "1h")
        == "data/futures/um/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2026-09.zip"
    )
    assert (
        archive_path("markPriceKlines", "BTCUSDT", "2026-10-17", "1h")
        == "data/futures/um/daily/markPriceKlines/BTCUSDT/1h/BTCUSDT-1h-2026-10-17.zip"
    )
    assert (
        archive_path("fundingRate", "BTCUSDT", "2026-09")
        == "data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2026-09.zip"
    )


def test_fetch_verifies_and_keeps_the_zip_and_its_checksum(tmp_path: Path) -> None:
    sources = FakeSources()
    path = archive_path("klines", "BTCUSDT", "2026-09", "1h")
    sources.publish(path, [row(0, HOUR_MS)])
    data = sources.data(tmp_path)

    local = data.fetch(path)

    assert local == tmp_path / path
    assert local.read_bytes() == sources.files[path]
    assert (tmp_path / (path + ".CHECKSUM")).is_file()
    # A verified local copy is used without asking the archive again.
    sources.requests.clear()
    assert data.fetch(path) == local
    assert sources.requests == []


def test_fetch_returns_none_for_a_file_not_archived(tmp_path: Path) -> None:
    sources = FakeSources()
    path = archive_path("klines", "BTCUSDT", "2026-10", "1h")
    assert sources.data(tmp_path).fetch(path) is None
    assert not (tmp_path / path).exists()


def test_fetch_refuses_a_download_that_does_not_match_its_checksum(tmp_path: Path) -> None:
    sources = FakeSources()
    path = archive_path("klines", "BTCUSDT", "2026-09", "1h")
    sources.publish(path, [row(0, HOUR_MS)])
    sources.files[path] += b"corrupted"

    with pytest.raises(DataError, match="doesn't match"):
        sources.data(tmp_path).fetch(path)
    assert not (tmp_path / path).exists()


def test_fetch_downloads_again_when_the_local_copy_is_damaged(tmp_path: Path) -> None:
    sources = FakeSources()
    path = archive_path("klines", "BTCUSDT", "2026-09", "1h")
    sources.publish(path, [row(0, HOUR_MS)])
    data = sources.data(tmp_path)
    local = data.fetch(path)
    assert local is not None
    local.write_bytes(b"damaged")

    assert data.fetch(path) == local
    assert local.read_bytes() == sources.files[path]


@pytest.mark.parametrize("header", [False, True])
def test_read_klines_with_or_without_a_header(tmp_path: Path, header: bool) -> None:
    september = day_ms(date(2026, 9, 1))
    zip_path = tmp_path / "BTCUSDT-1h-2026-09.zip"
    rows = [row(september, HOUR_MS, "1.10"), row(september + HOUR_MS, HOUR_MS)]
    zip_path.write_bytes(make_zip(zip_path.name, rows, header=header))

    klines = read_klines(zip_path, "BTCUSDT", "1h")

    assert [k.open_time for k in klines] == [september, september + HOUR_MS]
    first = klines[0]
    assert (first.symbol, first.interval, first.closed) == ("BTCUSDT", "1h", True)
    assert (first.open, first.close, first.volume) == (
        Decimal("1.10"),
        Decimal("1.10"),
        Decimal("1.5"),
    )
    assert first.close_time == september + HOUR_MS - 1


def test_read_klines_refuses_microsecond_times(tmp_path: Path) -> None:
    zip_path = tmp_path / "BTCUSDT-1h-2026-09.zip"
    microseconds = row(0, HOUR_MS)
    microseconds[0] = "1759276800000000"
    zip_path.write_bytes(make_zip(zip_path.name, [microseconds]))

    with pytest.raises(DataError, match="milliseconds"):
        read_klines(zip_path, "BTCUSDT", "1h")


def test_rest_klines_pages_and_drops_the_open_candle(tmp_path: Path) -> None:
    sources = FakeSources()
    sources.rest_klines = [rest_row(i * 60_000, 60_000) for i in range(3200)]
    # The candle at index 3100 is still open; it and anything after are dropped.
    sources.server_ms = 3100 * 60_000 + 30_000

    klines = sources.data(tmp_path).rest_klines("BTCUSDT", "1m", 0, 3200 * 60_000)

    assert [k.open_time for k in klines] == [i * 60_000 for i in range(3100)]
    kline_requests = [u for u in sources.requests if "/fapi/v1/klines" in u]
    assert len(kline_requests) == 3  # 1500 + 1500 + the rest


def test_candles_come_from_months_then_days_then_rest(tmp_path: Path) -> None:
    sources = FakeSources()
    september = day_ms(date(2026, 9, 1))
    sources.publish(
        archive_path("klines", "BTCUSDT", "2026-09", "1d"),
        [row(september + i * DAY_MS, DAY_MS) for i in range(30)],
        header=True,
    )
    october = day_ms(date(2026, 10, 1))
    sources.publish(
        archive_path("klines", "BTCUSDT", "2026-10-01", "1d"), [row(october, DAY_MS, "101")]
    )
    # 10-02 isn't archived yet, and 10-03 (today) is still open.
    sources.rest_klines = [rest_row(october + DAY_MS, DAY_MS, "102")]
    sources.rest_klines.append(rest_row(october + 2 * DAY_MS, DAY_MS, "103"))
    sources.server_ms = october + 2 * DAY_MS + HOUR_MS

    candles = sources.data(tmp_path).candles(
        "BTCUSDT", "1d", date(2026, 9, 29), date(2026, 10, 3), today=date(2026, 10, 3)
    )

    assert [k.open_time for k in candles.klines] == [
        september + 28 * DAY_MS,
        september + 29 * DAY_MS,
        october,
        october + DAY_MS,
    ]
    assert [k.close for k in candles.klines[2:]] == [Decimal("101"), Decimal("102")]
    assert candles.gaps == []


def test_a_month_not_archived_yet_is_read_day_by_day(tmp_path: Path) -> None:
    sources = FakeSources()
    september = day_ms(date(2026, 9, 1))
    for i in range(30):
        sources.publish(
            archive_path("klines", "BTCUSDT", f"2026-09-{i + 1:02d}", "1d"),
            [row(september + i * DAY_MS, DAY_MS)],
        )

    candles = sources.data(tmp_path).candles(
        "BTCUSDT", "1d", date(2026, 9, 1), date(2026, 9, 30), today=date(2026, 10, 1)
    )

    assert len(candles.klines) == 30


def test_a_gap_in_the_archive_is_filled_from_rest(tmp_path: Path) -> None:
    sources = FakeSources()
    september = day_ms(date(2026, 9, 1))
    rows = [row(september + i * HOUR_MS, HOUR_MS) for i in range(30 * 24) if i // 24 != 2]
    sources.publish(archive_path("markPriceKlines", "BTCUSDT", "2026-09", "1h"), rows)
    sources.rest_mark_klines = [
        rest_row(september + i * HOUR_MS, HOUR_MS, "77") for i in range(2 * 24, 3 * 24)
    ]
    sources.server_ms = day_ms(date(2026, 10, 18))

    candles = sources.data(tmp_path).candles(
        "BTCUSDT",
        "1h",
        date(2026, 9, 1),
        date(2026, 9, 30),
        dataset="markPriceKlines",
        today=date(2026, 10, 18),
    )

    assert candles.gaps == []
    assert len(candles.klines) == 30 * 24
    assert {k.close for k in candles.klines[48:72]} == {Decimal("77")}


def test_candles_report_gaps_rest_cannot_fill(tmp_path: Path) -> None:
    sources = FakeSources()
    september = day_ms(date(2026, 9, 1))
    rows = [row(september + i * HOUR_MS, HOUR_MS) for i in range(30 * 24) if i not in (5, 6)]
    sources.publish(archive_path("klines", "BTCUSDT", "2026-09", "1h"), rows)

    candles = sources.data(tmp_path).candles(
        "BTCUSDT", "1h", date(2026, 9, 1), date(2026, 9, 30), today=date(2026, 10, 18)
    )

    assert candles.gaps == [(september + 4 * HOUR_MS, september + 7 * HOUR_MS)]
    assert len(candles.klines) == 30 * 24 - 2


def test_candles_refuse_conflicting_duplicates(tmp_path: Path) -> None:
    sources = FakeSources()
    september = day_ms(date(2026, 9, 1))
    rows = [row(september, HOUR_MS, "100"), row(september, HOUR_MS, "200")]
    sources.publish(archive_path("klines", "BTCUSDT", "2026-09", "1h"), rows)

    with pytest.raises(DataError, match="two different candles"):
        sources.data(tmp_path).candles(
            "BTCUSDT", "1h", date(2026, 9, 1), date(2026, 9, 1), today=date(2026, 10, 18)
        )


def test_mark_price_candles_use_their_own_archive_and_endpoint(tmp_path: Path) -> None:
    sources = FakeSources()
    september = day_ms(date(2026, 9, 1))
    sources.publish(
        archive_path("markPriceKlines", "BTCUSDT", "2026-09", "1d"),
        [row(september + i * DAY_MS, DAY_MS, "99") for i in range(30)],
    )
    october = day_ms(date(2026, 10, 1))
    sources.rest_mark_klines = [rest_row(october, DAY_MS, "98")]
    sources.server_ms = october + DAY_MS + HOUR_MS

    candles = sources.data(tmp_path).candles(
        "BTCUSDT",
        "1d",
        date(2026, 9, 30),
        date(2026, 10, 1),
        dataset="markPriceKlines",
        today=date(2026, 10, 2),
    )

    assert [k.close for k in candles.klines] == [Decimal("99"), Decimal("98")]
    assert any("/fapi/v1/markPriceKlines" in u for u in sources.requests)
    assert not any("/fapi/v1/klines" in u for u in sources.requests)


FUNDING_HEADER = "calc_time,funding_interval_hours,last_funding_rate"


def test_funding_rates_from_the_archive_then_rest(tmp_path: Path) -> None:
    sources = FakeSources()
    september = day_ms(date(2026, 9, 1))
    eight_hours = 8 * HOUR_MS
    # Archive times can be a few ms past the hour.
    sources.publish(
        archive_path("fundingRate", "BTCUSDT", "2026-09"),
        [[str(september + i * eight_hours + 5), "8", "0.0001"] for i in range(90)],
        header=True,
        header_row=FUNDING_HEADER,
    )
    october = day_ms(date(2026, 10, 1))
    sources.rest_funding = [
        {
            "symbol": "BTCUSDT",
            "fundingTime": october + i * eight_hours + 2,
            "fundingRate": "-0.0002",
        }
        for i in range(6)
    ]

    rates = sources.data(tmp_path).funding_rates(
        "BTCUSDT", date(2026, 9, 30), date(2026, 10, 1), today=date(2026, 10, 2)
    )

    assert [r.time for r in rates] == [
        october - 3 * eight_hours + i * eight_hours for i in range(6)
    ]
    assert [r.rate for r in rates] == [Decimal("0.0001")] * 3 + [Decimal("-0.0002")] * 3


def test_rest_funding_rates_keep_paging_past_a_short_page(tmp_path: Path) -> None:
    sources = FakeSources()
    sources.funding_page_cap = 500  # fewer than the 1000 asked for
    eight_hours = 8 * HOUR_MS
    start = day_ms(date(2025, 1, 1))
    sources.rest_funding = [
        {"symbol": "BTCUSDT", "fundingTime": start + i * eight_hours, "fundingRate": "0.0001"}
        for i in range(1200)
    ]

    rates = sources.data(tmp_path).rest_funding_rates("BTCUSDT", start, start + 1200 * eight_hours)

    assert len(rates) == 1200


def test_candles_refuse_a_future_end(tmp_path: Path) -> None:
    with pytest.raises(DataError, match="future"):
        FakeSources().data(tmp_path).candles(
            "BTCUSDT", "1h", date(2026, 10, 1), date(2026, 10, 19), today=date(2026, 10, 18)
        )
