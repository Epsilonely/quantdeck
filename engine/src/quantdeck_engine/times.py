"""Time arithmetic shared by the engine and the backtest.

Binance times are epoch milliseconds, and the risk day starts at 00:00 UTC (D-23).
"""

from datetime import UTC, date, datetime

DAY_MS = 24 * 60 * 60 * 1000


def day_start_ms(now_ms: int) -> int:
    """The 00:00 UTC that starts the day `now_ms` falls in."""
    return now_ms - now_ms % DAY_MS


def day_ms(day: date) -> int:
    """00:00 UTC on `day`, in ms."""
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)


def utc_text(ms: int) -> str:
    """`YYYY-MM-DD HH:MM` in UTC, for logs and reports."""
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M")


def iso_utc(seconds: float) -> str:
    """ISO 8601 UTC to the second, as the client protocol sends times (D-33)."""
    return datetime.fromtimestamp(seconds, UTC).isoformat(timespec="seconds")
