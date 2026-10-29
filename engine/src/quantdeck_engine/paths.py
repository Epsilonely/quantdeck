"""Where the engine keeps its files, wherever it is started from, and how it writes the ones
that must never be half-written.

Every path hangs off `ENGINE_DIR` (the `engine/` directory, found from this package), so
they are the same whether the engine runs from a terminal or is started by the GUI (D-34).
"""

import os
from pathlib import Path

# engine/src/quantdeck_engine/paths.py -> engine/
ENGINE_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = ENGINE_DIR.parent

ENV_FILE = REPO_ROOT / ".env"  # secrets and the network (techContext.md)
CONFIG_FILE = ENGINE_DIR / "config.toml"  # risk settings (D-28); first fills symbols.json (D-47)
LOG_DIR = ENGINE_DIR / "logs"  # rolling log files (D-12)
DATA_DIR = ENGINE_DIR / "data"  # historical candles for backtests (D-38)
FORWARD_DIR = DATA_DIR / "forward"  # what the strategy decided in `run`, per symbol (D-43)
STATE_DIR = ENGINE_DIR / "state"
HALT_FILE = STATE_DIR / "halt.json"  # the kill switch's halt record (D-29)
LOCK_FILE = STATE_DIR / "engine.lock"  # one engine process at a time (D-31)
CONNECTION_FILE = STATE_DIR / "connection.json"  # port and token for clients (D-32)
SYMBOLS_FILE = STATE_DIR / "symbols.json"  # the symbols, their leverage and strategy (D-47)


def write_atomic(path: Path, content: str | bytes) -> None:
    """Write the whole file or nothing: to a temporary file first, then swap it in.

    A crash never leaves a half-written file. On Windows the swap fails with OSError while
    another program has the file open (D-12, D-29); callers decide what that means.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    if isinstance(content, str):
        temporary.write_text(content, encoding="utf-8")
    else:
        temporary.write_bytes(content)
    os.replace(temporary, path)
