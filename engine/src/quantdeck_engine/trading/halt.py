"""The kill switch's halt record (D-27, D-29): `engine/state/halt.json`, present only while halted.

The kill switch writes it, and `quantdeck-engine lift-halt` deletes it. It carries a halt into
later UTC days; within the same day, today's loss rebuilt from the exchange keeps the engine
halted even without it. A file that exists but can't be read counts as halted.
"""

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from quantdeck_engine.paths import HALT_FILE, write_atomic

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HaltRecord:
    reason: str
    halted_at: str  # ISO 8601 UTC; empty when the file couldn't be read


class HaltStore:
    def __init__(self, path: Path = HALT_FILE) -> None:
        self.path = path

    def load(self) -> HaltRecord | None:
        """The current halt, or None when not halted."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as e:
            return self._unreadable(e)
        try:
            data = json.loads(text)
            return HaltRecord(reason=str(data["reason"]), halted_at=str(data["halted_at"]))
        except (ValueError, KeyError, TypeError) as e:
            return self._unreadable(e)

    def save(self, record: HaltRecord) -> None:
        """Write the record atomically. Raises OSError if it can't be written, which on
        Windows includes another program having the file open (D-12, D-29)."""
        write_atomic(self.path, json.dumps(asdict(record), indent=2) + "\n")

    def clear(self) -> None:
        """Delete the record. Raises OSError if it exists and can't be deleted."""
        self.path.unlink(missing_ok=True)

    def _unreadable(self, error: Exception) -> HaltRecord:
        logger.error("Halt record %s is unreadable (%s); treating it as halted", self.path, error)
        return HaltRecord(reason=f"halt record unreadable: {error}", halted_at="")
