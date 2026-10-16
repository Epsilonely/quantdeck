"""One engine process per account at a time (D-31).

An OS file lock on `engine/state/engine.lock`. The OS releases it when the process exits,
even after a crash, so a file left behind never blocks a restart.
"""

import sys
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Self

# engine/src/quantdeck_engine/lock.py -> engine/state, wherever the engine is started from
DEFAULT_LOCK_FILE = Path(__file__).resolve().parents[2] / "state" / "engine.lock"


class EngineAlreadyRunning(Exception):
    """Another engine process holds the lock."""


class InstanceLock:
    def __init__(self, path: Path = DEFAULT_LOCK_FILE) -> None:
        self.path = path
        self._file: BinaryIO | None = None

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file = self.path.open("a+b")
        try:
            _lock(file)
        except OSError:
            file.close()
            raise EngineAlreadyRunning(
                f"another engine process is running (it holds {self.path})"
            ) from None
        self._file = file
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._file is not None:
            _unlock(self._file)
            self._file.close()
            self._file = None


if sys.platform == "win32":
    import msvcrt

    def _lock(file: BinaryIO) -> None:
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(file: BinaryIO) -> None:
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(file: BinaryIO) -> None:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(file: BinaryIO) -> None:
        fcntl.flock(file.fileno(), fcntl.LOCK_UN)
