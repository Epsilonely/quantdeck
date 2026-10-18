"""Historical candles and funding rates for backtests (D-38).

Mainnet data only, since testnet prices differ: monthly zips from Binance's public archive
(data.binance.vision), daily zips for a month not archived yet, and REST for days not
archived yet. Candles come in last price and mark price; the backtest triggers stops on
the mark price, as the live stops do (D-18). Every zip is checked against its `.CHECKSUM`
and kept as downloaded, with its checksum file, under `engine/data/` (gitignored) in the
archive's own layout. Archive details are in docs/memory-bank/binanceNotes.md.
"""

import csv
import hashlib
import io
import logging
import os
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

import httpx

from quantdeck_engine.binance.candle_feed import SETTLE_MS
from quantdeck_engine.binance.streams import INTERVAL_MS, Kline, kline_from_row
from quantdeck_engine.config import MAINNET

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://data.binance.vision"
# engine/src/quantdeck_engine/backtest/data.py -> engine/data
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
TIMEOUT_SECONDS = 30.0
REST_KLINES_LIMIT = 1500  # the maximum per request
REST_FUNDING_LIMIT = 1000  # the maximum per request
MAX_GAPS_LOGGED = 10

# Candle datasets: the archive's name and the REST endpoint with the same rows.
KLINE_DATASETS = {
    "klines": "/fapi/v1/klines",  # last price
    "markPriceKlines": "/fapi/v1/markPriceKlines",  # volumes are 0
}


class DataError(Exception):
    """Historical data couldn't be fetched, or doesn't add up."""


@dataclass(frozen=True)
class Candles:
    symbol: str
    interval: str
    klines: list[Kline]  # closed, oldest first, no duplicates
    gaps: list[tuple[int, int]]  # open times (ms) of the candles on either side of each gap


@dataclass(frozen=True)
class FundingRate:
    time: int  # ms, on the minute: the archive's times can be a few ms past the hour
    rate: Decimal  # positive: longs pay shorts


def archive_path(dataset: str, symbol: str, period: str, interval: str | None = None) -> str:
    """One file's path in the archive, e.g.
    `data/futures/um/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2026-09.zip`.

    `period` is `YYYY-MM` for a monthly file, `YYYY-MM-DD` for a daily one. A dataset
    without an interval (`fundingRate`) puts its own name in the file name instead.
    """
    frequency = "monthly" if len(period) == 7 else "daily"
    if interval is None:
        return f"data/futures/um/{frequency}/{dataset}/{symbol}/{symbol}-{dataset}-{period}.zip"
    return (
        f"data/futures/um/{frequency}/{dataset}/{symbol}/{interval}/"
        f"{symbol}-{interval}-{period}.zip"
    )


def read_klines(zip_path: Path, symbol: str, interval: str) -> list[Kline]:
    """The candles in one archive kline or mark price kline zip."""
    return [kline_from_row(row, symbol, interval) for row in _csv_rows(zip_path)]


def read_funding_rates(zip_path: Path) -> list[FundingRate]:
    """The rates in one archive `fundingRate` zip: calc_time, interval hours, rate."""
    return [
        FundingRate(_on_the_minute(int(row[0])), Decimal(row[2])) for row in _csv_rows(zip_path)
    ]


def _csv_rows(zip_path: Path) -> list[list[str]]:
    """The data rows of an archive zip's one CSV file, without any header row.

    Some files start with a header row and some don't, so a first row that doesn't start
    with a number is skipped.
    """
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        if len(names) != 1:
            raise DataError(f"{zip_path.name}: expected one file inside, found {names}")
        with z.open(names[0]) as f:
            data: list[list[str]] = []
            rows = csv.reader(io.TextIOWrapper(f, encoding="utf-8", newline=""))
            for line, row in enumerate(rows):
                if not row:
                    continue
                if not row[0].isdigit():
                    if line == 0:
                        continue  # header
                    raise DataError(f"{zip_path.name}: unexpected row {line + 1}: {row[:3]}")
                if len(row[0]) != 13:
                    # Spot files switched to microseconds in 2025; futures still use ms.
                    raise DataError(f"{zip_path.name}: time {row[0]} is not in milliseconds")
                data.append(row)
    return data


def _on_the_minute(ms: int) -> int:
    return ms - ms % 60_000


def day_ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)


def utc_text(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M")


class HistoricalData:
    """Fetches mainnet candles: from the local copy, the archive, or REST, in that order."""

    def __init__(self, http: httpx.Client, data_dir: Path = DEFAULT_DATA_DIR) -> None:
        self._http = http
        self._dir = data_dir

    def candles(
        self,
        symbol: str,
        interval: str,
        start: date,
        end: date,
        *,
        dataset: str = "klines",
        today: date | None = None,
    ) -> Candles:
        """Closed candles opening from `start` 00:00 UTC through the end of `end`.

        `dataset` is `klines` (last price) or `markPriceKlines`. A gap in the archive files
        is filled from REST: the BTCUSDT mark price files lack whole days that REST has.
        Gaps REST can't fill either are logged and returned rather than raised: the
        exchange produces no candles while trading is down, so some gaps are real.
        """
        if interval not in INTERVAL_MS:
            raise DataError(f"Unsupported interval {interval!r}")
        if dataset not in KLINE_DATASETS:
            raise DataError(f"Unsupported dataset {dataset!r}")
        today = _check_range(start, end, today)

        klines: list[Kline] = []
        month = start.replace(day=1)
        while month <= end:
            next_month = _next_month(month)
            if next_month <= today:
                path = archive_path(dataset, symbol, f"{month:%Y-%m}", interval)
                local = self.fetch(path)
                if local is not None:
                    klines += read_klines(local, symbol, interval)
                    month = next_month
                    continue
            # Not archived as a month (yet): day by day.
            day = max(month, start)
            while day < next_month and day <= end:
                klines += self._day_klines(dataset, symbol, interval, day, today)
                day += timedelta(days=1)
            month = next_month

        label = f"{symbol} {interval} {dataset}"
        start_ms, end_ms = day_ms(start), day_ms(end + timedelta(days=1))
        klines = _unique(label, [k for k in klines if start_ms <= k.open_time < end_ms])
        step = INTERVAL_MS[interval]
        gaps = _gaps(klines, step)
        if gaps:
            filled: list[Kline] = []
            for before, after in gaps:
                filled += self.rest_klines(symbol, interval, before + step, after, dataset=dataset)
            if filled:
                logger.info(
                    "%s: read %d candle(s) missing from the archive from REST", label, len(filled)
                )
                klines = _unique(label, klines + filled)
                gaps = _gaps(klines, step)
        for before, after in gaps[:MAX_GAPS_LOGGED]:
            logger.warning(
                "%s: no candles between %s and %s UTC", label, utc_text(before), utc_text(after)
            )
        if len(gaps) > MAX_GAPS_LOGGED:
            logger.warning("%s: %d more gaps", label, len(gaps) - MAX_GAPS_LOGGED)
        return Candles(symbol=symbol, interval=interval, klines=klines, gaps=gaps)

    def funding_rates(
        self, symbol: str, start: date, end: date, *, today: date | None = None
    ) -> list[FundingRate]:
        """Funding rates settled from `start` 00:00 UTC through the end of `end`, oldest first.

        The archive has them monthly only; a month not archived yet comes from REST.
        """
        today = _check_range(start, end, today)
        rates: list[FundingRate] = []
        month = start.replace(day=1)
        while month <= end:
            next_month = _next_month(month)
            local = None
            if next_month <= today:
                local = self.fetch(archive_path("fundingRate", symbol, f"{month:%Y-%m}"))
            if local is not None:
                rates += read_funding_rates(local)
            else:
                logger.info(
                    "%s funding for %s is not archived (yet); reading it from REST",
                    symbol,
                    f"{month:%Y-%m}",
                )
                rates += self.rest_funding_rates(symbol, day_ms(month), day_ms(next_month))
            month = next_month

        start_ms, end_ms = day_ms(start), day_ms(end + timedelta(days=1))
        by_time = {r.time: r for r in rates if start_ms <= r.time < end_ms}
        return [by_time[t] for t in sorted(by_time)]

    def fetch(self, path: str) -> Path | None:
        """The local copy of an archive file, downloaded first if there is none.

        Returns None if the archive doesn't have the file (HTTP 404): not published yet,
        or never. Raises `DataError` if a download doesn't match its checksum.
        """
        local = self._dir / path
        checksum_file = local.with_name(local.name + ".CHECKSUM")
        if local.is_file() and checksum_file.is_file():
            expected = _expected_sha256(checksum_file.read_text(encoding="utf-8"), local.name)
            if hashlib.sha256(local.read_bytes()).hexdigest() == expected:
                return local
            logger.warning("%s doesn't match its checksum; downloading it again", path)

        checksum = self._get_archive(path + ".CHECKSUM")
        if checksum is None:
            return None
        content = self._get_archive(path)
        if content is None:
            return None
        expected = _expected_sha256(checksum.decode("utf-8"), local.name)
        actual = hashlib.sha256(content).hexdigest()
        if actual != expected:
            raise DataError(f"{path}: SHA-256 {actual} doesn't match the checksum {expected}")
        _write_atomic(local, content)
        _write_atomic(checksum_file, checksum)
        logger.info("Downloaded %s (%d bytes)", path, len(content))
        return local

    def rest_klines(
        self, symbol: str, interval: str, start_ms: int, end_ms: int, *, dataset: str = "klines"
    ) -> list[Kline]:
        """Closed mainnet candles opening in [start_ms, end_ms), from `GET /fapi/v1/klines`
        or, for `markPriceKlines`, `GET /fapi/v1/markPriceKlines`. A candle that closed
        less than a minute ago is left out: REST's copy may not be final yet."""
        server_ms = int(self._get_rest("/fapi/v1/time", {})["serverTime"]) - SETTLE_MS
        step = INTERVAL_MS[interval]
        klines: list[Kline] = []
        cursor = start_ms
        while cursor < end_ms:
            params = {
                "symbol": symbol,
                "interval": interval,
                "startTime": cursor,
                "endTime": end_ms - 1,  # matched against open times, inclusive
                "limit": REST_KLINES_LIMIT,
            }
            rows = self._get_rest(KLINE_DATASETS[dataset], params)
            for row in rows:
                if int(row[6]) >= server_ms:
                    return klines  # still open, or closed only just now
                klines.append(kline_from_row(row, symbol, interval))
            if len(rows) < REST_KLINES_LIMIT:
                break
            cursor = int(rows[-1][0]) + step
        return klines

    def rest_funding_rates(self, symbol: str, start_ms: int, end_ms: int) -> list[FundingRate]:
        """Funding rates settled in [start_ms, end_ms), from `GET /fapi/v1/fundingRate`."""
        rates: list[FundingRate] = []
        cursor = max(start_ms, 1)  # startTime=0 counts as not sent
        while cursor < end_ms:
            params = {
                "symbol": symbol,
                "startTime": cursor,
                "endTime": end_ms - 1,
                "limit": REST_FUNDING_LIMIT,
            }
            rows = self._get_rest("/fapi/v1/fundingRate", params)
            if not rows:
                break
            rates += [
                FundingRate(_on_the_minute(int(r["fundingTime"])), Decimal(r["fundingRate"]))
                for r in rows
            ]
            # Keep paging until a page comes back empty: it returned fewer rows than
            # `limit` asked for when tried without times (binanceNotes.md).
            cursor = int(rows[-1]["fundingTime"]) + 1
        return rates

    def _day_klines(
        self, dataset: str, symbol: str, interval: str, day: date, today: date
    ) -> list[Kline]:
        if day < today:
            local = self.fetch(archive_path(dataset, symbol, f"{day:%Y-%m-%d}", interval))
            if local is not None:
                return read_klines(local, symbol, interval)
        logger.info(
            "%s %s %s %s is not archived (yet); reading it from REST",
            symbol,
            interval,
            dataset,
            day,
        )
        return self.rest_klines(
            symbol, interval, day_ms(day), day_ms(day + timedelta(days=1)), dataset=dataset
        )

    def _get_archive(self, path: str) -> bytes | None:
        response = self._http.get(f"{ARCHIVE_URL}/{path}", timeout=TIMEOUT_SECONDS)
        if response.status_code == 404:
            return None
        if response.is_error:
            raise DataError(f"{path}: HTTP {response.status_code}")
        return response.content

    def _get_rest(self, path: str, params: dict[str, str | int]) -> Any:
        response = self._http.get(f"{MAINNET.rest}{path}", params=params, timeout=TIMEOUT_SECONDS)
        if response.is_error:
            raise DataError(f"{path}: HTTP {response.status_code}: {response.text[:200]}")
        return response.json()


def _check_range(start: date, end: date, today: date | None) -> date:
    today = today or datetime.now(UTC).date()
    if start > end:
        raise DataError(f"Start {start} is after end {end}")
    if end > today:
        raise DataError(f"End {end} is in the future")
    return today


def _next_month(month: date) -> date:
    return (month + timedelta(days=32)).replace(day=1)


def _unique(label: str, klines: list[Kline]) -> list[Kline]:
    """Sorted by open time, exact duplicates dropped; conflicting duplicates refused."""
    unique: list[Kline] = []
    for k in sorted(klines, key=lambda k: k.open_time):
        if unique and k.open_time == unique[-1].open_time:
            if k != unique[-1]:
                raise DataError(f"{label}: two different candles open at {utc_text(k.open_time)}")
            continue
        unique.append(k)
    return unique


def _gaps(klines: list[Kline], step: int) -> list[tuple[int, int]]:
    return [
        (a.open_time, b.open_time) for a, b in pairwise(klines) if b.open_time - a.open_time != step
    ]


def _expected_sha256(checksum_text: str, file_name: str) -> str:
    """The digest from a `.CHECKSUM` file: `<sha256 hex>  <file name>`."""
    parts = checksum_text.split()
    if len(parts) != 2 or parts[1] != file_name or len(parts[0]) != 64:
        raise DataError(f"Unexpected checksum file for {file_name}: {checksum_text[:120]!r}")
    return parts[0].lower()


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)
