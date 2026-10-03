import logging
from collections.abc import Iterator

import pytest

from quantdeck_engine.logs import QUIET_LOGGERS


@pytest.fixture
def isolated_logging() -> Iterator[None]:
    """Undo `setup_logging()`: remove its handlers and restore logger levels."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    saved_quiet = {name: logging.getLogger(name).level for name in QUIET_LOGGERS}
    yield
    for handler in root.handlers[:]:
        if handler not in saved_handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(saved_level)
    for name, level in saved_quiet.items():
        logging.getLogger(name).setLevel(level)
