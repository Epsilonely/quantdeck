"""Historical candles for backtests (D-38).

Mainnet data only, since testnet prices differ: monthly zips from Binance's public archive
(data.binance.vision), daily zips for a month not archived yet, and REST klines for days
not archived yet. Every zip is checked against its `.CHECKSUM` and kept as downloaded,
with its checksum file, under `engine/data/` (gitignored) in the archive's own layout.
Archive details are in docs/memory-bank/binanceNotes.md.
"""

import csv
import hashlib
import io
import logging
import os
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import httpx

from quantdeck_engine.binance.streams import Kline, kline_from_row
from quantdeck_engine.config import MAINNET

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://data.binance.vision"
# engine/src/quantdeck_engine/backtest/data.py -> engine/data
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
TIMEOUT_SECONDS = 30.0
REST_KLINES_LIMIT = 1500  # the maximum per request
MAX_GAPS_LOGGED = 10

DAY_MS = 24 * 60 * 60 * 1000
INTERVAL_MS = {
    "1m": 60_000,
    "3m": 3 * 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "2h": 2 * 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "6h": 6 * 60 * 60_000,
    "8h": 8 * 60 * 60_000,
    "12h": 12 * 60 * 60_000,
    "1d": DAY_MS,
    "3d": 3 * DAY_MS,
    "1w": 7 * DAY_MS,
}


class DataError(Exception):
    """Historical data couldn't be fetched, or doesn't add up."""


@dataclass(frozen=True)
class Candles:
    symbol: str
    interval: str
    klines: list[Kline]  # closed, oldest first, no duplicates
    gaps: list[tuple[int, int]]  # open times (ms) of the candles on either side of each gap


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
    """The candles in one archive kline zip.

    Some files start with a header row and some don't, so a first row that doesn't start
    with a number is skipped.
    """
    klines: list[Kline] = []
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        if len(names) != 1:
            raise DataError(f"{zip_path.name}: expected one file inside, found {names}")
        with z.open(names[0]) as f:
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
                    raise DataError(f"{zip_path.name}: open time {row[0]} is not in milliseconds")
                klines.append(kline_from_row(row, symbol, interval))
    return klines


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
        self, symbol: str, interval: str, start: date, end: date, *, today: date | None = None
    ) -> Candles:
        """Closed candles opening from `start` 00:00 UTC through the end of `end`.

        Gaps are logged and returned rather than raised: the exchange produces no candles
        while trading is down, so some gaps are real.
        """
        if interval not in INTERVAL_MS:
            raise DataError(f"Unsupported interval {interval!r}")
        today = today or datetime.now(UTC).date()
        if start > end:
            raise DataError(f"Start {start} is after end {end}")
        if end > today:
            raise DataError(f"End {end} is in the future")

        klines: list[Kline] = []
        month = start.replace(day=1)
        while month <= end:
            next_month = (month + timedelta(days=32)).replace(day=1)
            if next_month <= today:
                path = archive_path("klines", symbol, f"{month:%Y-%m}", interval)
                local = self.fetch(path)
                if local is not None:
                    klines += read_klines(local, symbol, interval)
                    month = next_month
                    continue
            # Not archived as a month (yet): day by day.
            day = max(month, start)
            while day < next_month and day <= end:
                klines += self._day_klines(symbol, interval, day, today)
                day += timedelta(days=1)
            month = next_month

        start_ms, end_ms = day_ms(start), day_ms(end + timedelta(days=1))
        klines = [k for k in klines if start_ms <= k.open_time < end_ms]
        return _checked(symbol, interval, klines)

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

    def rest_klines(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[Kline]:
        """Closed mainnet candles opening in [start_ms, end_ms), from `GET /fapi/v1/klines`."""
        server_ms = int(self._get_rest("/fapi/v1/time", {})["serverTime"])
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
            rows = self._get_rest("/fapi/v1/klines", params)
            for row in rows:
                if int(row[6]) >= server_ms:
                    return klines  # the last candle is still open
                klines.append(kline_from_row(row, symbol, interval))
            if len(rows) < REST_KLINES_LIMIT:
                break
            cursor = int(rows[-1][0]) + step
        return klines

    def _day_klines(self, symbol: str, interval: str, day: date, today: date) -> list[Kline]:
        if day < today:
            local = self.fetch(archive_path("klines", symbol, f"{day:%Y-%m-%d}", interval))
            if local is not None:
                return read_klines(local, symbol, interval)
        logger.info("%s %s %s is not archived (yet); reading it from REST", symbol, interval, day)
        return self.rest_klines(symbol, interval, day_ms(day), day_ms(day + timedelta(days=1)))

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


def _checked(symbol: str, interval: str, klines: list[Kline]) -> Candles:
    """Sort, drop exact duplicates, refuse conflicting ones, and find gaps."""
    klines.sort(key=lambda k: k.open_time)
    unique: list[Kline] = []
    for k in klines:
        if unique and k.open_time == unique[-1].open_time:
            if k != unique[-1]:
                raise DataError(
                    f"{symbol} {interval}: two different candles open at {utc_text(k.open_time)}"
                )
            continue
        unique.append(k)
    step = INTERVAL_MS[interval]
    gaps = [
        (a.open_time, b.open_time) for a, b in pairwise(unique) if b.open_time - a.open_time != step
    ]
    for before, after in gaps[:MAX_GAPS_LOGGED]:
        logger.warning(
            "%s %s: no candles between %s and %s UTC",
            symbol,
            interval,
            utc_text(before),
            utc_text(after),
        )
    if len(gaps) > MAX_GAPS_LOGGED:
        logger.warning("%s %s: %d more gaps", symbol, interval, len(gaps) - MAX_GAPS_LOGGED)
    return Candles(symbol=symbol, interval=interval, klines=unique, gaps=gaps)


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
