import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from quantdeck_engine.binance.rest import BinanceRestClient
from quantdeck_engine.config import parse_settings
from quantdeck_engine.logs import (
    DEFAULT_LOG_DIR,
    RollingFileHandler,
    delete_old_logs,
    setup_logging,
)

START = datetime(2026, 10, 3, 14, 30, 5, tzinfo=UTC)


def make_handler(directory: Path, max_bytes: int = 100) -> RollingFileHandler:
    handler = RollingFileHandler(directory, max_bytes=max_bytes, start=START)
    handler.setFormatter(logging.Formatter("%(message)s"))
    return handler


def emit(handler: RollingFileHandler, message: str, at: datetime = START) -> None:
    record = logging.makeLogRecord({"msg": message, "levelno": logging.INFO})
    record.created = at.timestamp()
    handler.handle(record)


def file_names(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


def test_default_log_dir_is_engine_logs() -> None:
    assert DEFAULT_LOG_DIR.name == "logs"
    assert (DEFAULT_LOG_DIR.parent / "pyproject.toml").is_file()


def test_first_file_is_named_after_start_with_index_000(tmp_path: Path) -> None:
    handler = make_handler(tmp_path)
    emit(handler, "hello")
    handler.close()

    assert file_names(tmp_path) == ["engine_2026-10-03_14-30-05_000.log"]
    assert handler.path.read_text(encoding="utf-8") == "hello\n"


def test_rolls_to_next_index_without_renaming_or_splitting(tmp_path: Path) -> None:
    handler = make_handler(tmp_path, max_bytes=100)
    lines = [f"{i}" * 39 for i in range(5)]  # 40 bytes each with the newline
    for line in lines:
        emit(handler, line)
    handler.close()

    assert file_names(tmp_path) == [
        "engine_2026-10-03_14-30-05_000.log",
        "engine_2026-10-03_14-30-05_001.log",
        "engine_2026-10-03_14-30-05_002.log",
    ]
    contents = [(tmp_path / name).read_text(encoding="utf-8") for name in file_names(tmp_path)]
    assert contents == [
        f"{lines[0]}\n{lines[1]}\n",
        f"{lines[2]}\n{lines[3]}\n",
        f"{lines[4]}\n",
    ]


def test_record_larger_than_limit_goes_whole_into_its_own_file(tmp_path: Path) -> None:
    handler = make_handler(tmp_path, max_bytes=10)
    emit(handler, "x" * 50)
    emit(handler, "y")
    handler.close()

    assert [(tmp_path / n).read_text(encoding="utf-8") for n in file_names(tmp_path)] == [
        "x" * 50 + "\n",
        "y\n",
    ]


def test_new_utc_date_starts_a_new_series(tmp_path: Path) -> None:
    handler = RollingFileHandler(tmp_path, start=datetime(2026, 10, 3, 23, 59, 58, tzinfo=UTC))
    handler.setFormatter(logging.Formatter("%(message)s"))
    emit(handler, "before", at=datetime(2026, 10, 3, 23, 59, 59, tzinfo=UTC))
    emit(handler, "after", at=datetime(2026, 10, 4, 0, 0, 3, tzinfo=UTC))
    emit(handler, "late", at=datetime(2026, 10, 3, 23, 59, 59, 900000, tzinfo=UTC))
    handler.close()

    assert file_names(tmp_path) == [
        "engine_2026-10-03_23-59-58_000.log",
        "engine_2026-10-04_00-00-03_000.log",
    ]
    assert (tmp_path / "engine_2026-10-03_23-59-58_000.log").read_text() == "before\n"
    # A late record from the previous day stays in the current file.
    assert (tmp_path / "engine_2026-10-04_00-00-03_000.log").read_text() == "after\nlate\n"


def test_reopening_the_same_name_appends(tmp_path: Path) -> None:
    first = make_handler(tmp_path)
    emit(first, "one")
    first.close()
    second = make_handler(tmp_path)
    emit(second, "two")
    second.close()

    assert second.path.read_text(encoding="utf-8") == "one\ntwo\n"


def test_delete_old_logs_removes_only_expired_engine_logs(tmp_path: Path) -> None:
    now = datetime(2026, 10, 3, tzinfo=UTC)
    ages = {"engine_old.log": 31, "engine_recent.log": 29, "notes.txt": 31}
    for name, days in ages.items():
        path = tmp_path / name
        path.write_text("x")
        mtime = (now - timedelta(days=days)).timestamp()
        os.utime(path, (mtime, mtime))

    assert delete_old_logs(tmp_path, now=now, max_age_days=30) == 1
    assert file_names(tmp_path) == ["engine_recent.log", "notes.txt"]


@pytest.mark.usefixtures("isolated_logging")
async def test_debug_log_records_requests_without_credentials(tmp_path: Path) -> None:
    settings = parse_settings(
        {"BINANCE_API_KEY": "test-key-123", "BINANCE_API_SECRET": "test-secret-456"}
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[], headers={"X-MBX-USED-WEIGHT-1M": "7"})

    log_file = setup_logging(tmp_path)
    logging.getLogger().setLevel(logging.DEBUG)
    async with BinanceRestClient(settings, transport=httpx.MockTransport(handler)) as client:
        await client.balances()

    text = log_file.read_text(encoding="utf-8")
    assert "GET /fapi/v3/balance -> HTTP 200" in text
    assert "weight 1m: 7" in text
    signature = seen[0].url.params["signature"]
    for secret in ("test-key-123", "test-secret-456", signature):
        assert secret not in text
