"""Engine logging: the console plus rolling log files in engine/logs/.

File names carry the UTC time their series started and a three-digit index, e.g.
`engine_2026-10-03_14-30-05_000.log`. A written file is never renamed — on Windows a rename
fails while another program has the file open — so rolling over always opens a new file:

- the next record would push the file past `max_bytes` -> same start time, index + 1
- the UTC date changes -> that record's time, index 000
"""

import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO

# engine/src/quantdeck_engine/logs.py -> engine/logs, wherever the engine is started from
DEFAULT_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
MAX_BYTES = 10 * 1024 * 1024
RETENTION_DAYS = 30
FILE_PREFIX = "engine_"

# Kept at WARNING even when the engine logs at DEBUG: httpx logs every request URL at INFO,
# signature included, and websockets logs its handshake requests at DEBUG.
QUIET_LOGGERS = ("httpx", "httpcore", "websockets")

FORMAT = "%(asctime)s.%(msecs)03dZ %(levelname)-7s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"

logger = logging.getLogger(__name__)


def log_file_name(start: datetime, index: int) -> str:
    return f"{FILE_PREFIX}{start:%Y-%m-%d_%H-%M-%S}_{index:03d}.log"


class RollingFileHandler(logging.Handler):
    """Writes records to `engine_<start>_<index>.log` files, opening a new one as needed.

    A record is never split across files. One larger than `max_bytes` still goes whole into
    a file of its own.
    """

    def __init__(
        self,
        directory: Path,
        *,
        max_bytes: int = MAX_BYTES,
        start: datetime | None = None,
    ) -> None:
        super().__init__()
        self.directory = directory
        self.max_bytes = max_bytes
        self._stream: BinaryIO | None = None
        directory.mkdir(parents=True, exist_ok=True)
        self._open(start or datetime.now(UTC), 0)

    @property
    def path(self) -> Path:
        return self.directory / log_file_name(self._start, self._index)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            data = (self.format(record) + "\n").encode("utf-8")
            created = datetime.fromtimestamp(record.created, UTC)
            # `>` rather than `!=`: a record from just before midnight that arrives late
            # must not reopen the previous day.
            if created.date() > self._start.date():
                self._open(created, 0)
            elif self._size > 0 and self._size + len(data) > self.max_bytes:
                self._open(self._start, self._index + 1)
            self._stream.write(data)
            self._stream.flush()
            self._size += len(data)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        with self.lock:
            self._close_stream()
        super().close()

    def _open(self, start: datetime, index: int) -> None:
        self._close_stream()
        self._start = start
        self._index = index
        # Append, so a restart within the same second continues the file instead of
        # truncating it.
        self._stream = self.path.open("ab")
        self._size = self.path.stat().st_size

    def _close_stream(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None


def setup_logging(log_dir: Path = DEFAULT_LOG_DIR) -> Path:
    """Send logs to the console and to rolling files, and return the first file's path.

    Call once at startup, before loading settings, so configuration errors and the mainnet
    warning reach the file too. The level starts at INFO; apply the configured level once
    settings are loaded.
    """
    formatter = logging.Formatter(FORMAT, DATE_FORMAT)
    formatter.converter = time.gmtime
    file_handler = RollingFileHandler(log_dir)
    root = logging.getLogger()
    for handler in (logging.StreamHandler(), file_handler):
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    delete_old_logs(log_dir)
    return file_handler.path


def delete_old_logs(
    log_dir: Path,
    *,
    now: datetime | None = None,
    max_age_days: int = RETENTION_DAYS,
) -> int:
    """Delete log files last written more than `max_age_days` ago; return how many."""
    cutoff = ((now or datetime.now(UTC)) - timedelta(days=max_age_days)).timestamp()
    deleted = 0
    for path in log_dir.glob(f"{FILE_PREFIX}*.log"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                deleted += 1
        except OSError as e:
            logger.warning("Could not delete old log file %s: %s", path.name, e)
    if deleted:
        logger.info("Deleted %d log file(s) older than %d days", deleted, max_age_days)
    return deleted
