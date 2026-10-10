"""Client order IDs that encode the order's role (D-21).

Format: `qd_<role>_<symbol>_<ms timestamp>_<seq>`, e.g. `qd_s_BTCUSDT_1759480000123_01`.
After a restart the engine can tell from the exchange alone what each open order is for.
Binance allows `^[\\.A-Z\\:/a-z0-9_-]{1,36}$` for both `newClientOrderId` and `clientAlgoId`
and enforces uniqueness only among open orders. A retry reuses the original ID (D-20).
"""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

MAX_LENGTH = 36
# Symbols may contain underscores (e.g. delivery contracts), so match the symbol greedily
# and anchor the fixed-width timestamp and sequence at the end.
_PATTERN = re.compile(r"^qd_([exsk])_([A-Z0-9_]+)_(\d{13})_(\d{2})$")


class Role(StrEnum):
    ENTRY = "e"
    EXIT = "x"  # strategy exits, and closes after a failed stop (D-19)
    STOP = "s"
    KILL = "k"


@dataclass(frozen=True)
class ClientId:
    role: Role
    symbol: str
    timestamp_ms: int
    seq: int


def parse_client_id(client_id: str) -> ClientId | None:
    """Return the parts of a QuantDeck ID, or None for any other ID."""
    match = _PATTERN.match(client_id)
    if match is None:
        return None
    role, symbol, timestamp, seq = match.groups()
    return ClientId(Role(role), symbol, int(timestamp), int(seq))


class ClientIdFactory:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._seq = 0

    def new(self, role: Role, symbol: str) -> str:
        # The sequence only separates IDs created within the same millisecond.
        seq = self._seq % 100
        self._seq += 1
        client_id = f"qd_{role}_{symbol}_{int(self._clock() * 1000)}_{seq:02d}"
        if len(client_id) > MAX_LENGTH:
            raise ValueError(f"Client ID for {symbol} would exceed {MAX_LENGTH} characters")
        return client_id
