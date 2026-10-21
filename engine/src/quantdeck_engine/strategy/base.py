"""The interface every strategy implements (D-36).

A strategy is plain synchronous code with no I/O, so the live engine and the backtest run
the same code (D-07). For each closed candle of its interval it gets the candle and the
current position, and returns an entry with a stop, an exit, or nothing.

Its decisions depend only on recent candles and that position. Whatever it keeps, such as
indicator windows, must be rebuildable from `warmup` past candles, so a restart can't change
a decision (D-06), and it treats every position on its symbol as its own, adopted ones
included (D-14, D-19). Sizing, risk, and orders stay outside the strategy (D-22 to D-25).
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from quantdeck_engine.binance.streams import Kline
from quantdeck_engine.trading.position import Position
from quantdeck_engine.trading.rules import Side


@dataclass(frozen=True)
class Enter:
    """Enter at market, protected by an exchange-side stop at `stop_price` (D-18, D-22).

    With a `limit_price`, enter with a post-only limit order at that price instead (D-45),
    rounded to the tick away from the market: down for a long, up for a short. It is valid
    for the next candle only and cancelled if not filled by that candle's close, when the
    strategy decides again; a strategy never keeps track of a pending order (D-36). Only
    the backtest fills limit entries for now: `run` refuses a strategy that uses them.

    The stop is fixed for the life of the position. Ignored while a position is open: one
    entry per position (D-22).
    """

    side: Side
    stop_price: Decimal
    limit_price: Decimal | None = None


@dataclass(frozen=True)
class Exit:
    """Close the whole position at market with reduceOnly (D-17). Ignored while flat."""


Action = Enter | Exit


def parameter[T](default: T, description: str) -> T:
    """A strategy parameter: a dataclass field with `default` and the help the GUI's settings
    show next to it (D-48). The help is in Korean, the language the GUI speaks to the user."""
    return field(default=default, metadata={"description": description})


class Strategy(Protocol):
    @property
    def interval(self) -> str:
        """The candle interval it trades on, e.g. `1h`."""
        ...

    @property
    def warmup(self) -> int:
        """How many closed candles it needs, the current one included, to decide."""
        ...

    @property
    def limit_entries(self) -> bool:
        """Whether its entries can carry a limit price. `run` refuses such a strategy until
        the engine can place limit entries (D-45)."""
        ...

    def on_candle(self, candle: Kline, position: Position | None) -> Action | None:
        """Called once per closed candle, oldest first. At most one action per candle.

        Returns None until it has seen `warmup` candles. Reads nothing but its arguments,
        not even the clock, so the same candles and positions always give the same actions.
        """
        ...
