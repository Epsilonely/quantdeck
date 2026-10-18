"""A symbol's open position: what a strategy sees (D-36), and what the exchange reports."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from quantdeck_engine.trading.rules import Side


@dataclass(frozen=True)
class Position:
    """The symbol's open position: the exchange's live (D-06), the simulator's in a backtest."""

    side: Side
    quantity: Decimal  # absolute size
    entry_price: Decimal


@dataclass(frozen=True)
class ExchangePosition(Position):
    """A position as `GET /fapi/v3/positionRisk` reports it, with the exchange's own
    liquidation price, or None while the exchange reports 0 for it."""

    liquidation_price: Decimal | None

    @classmethod
    def from_position_risk(cls, p: dict[str, Any]) -> "ExchangePosition | None":
        """None for a flat entry. One-way mode: only the BOTH side holds a position (D-05)."""
        amount = Decimal(p["positionAmt"])
        if p["positionSide"] != "BOTH" or amount == 0:
            return None
        liquidation = Decimal(p["liquidationPrice"])
        return cls(
            side=Side.from_amount(amount),
            quantity=abs(amount),
            entry_price=Decimal(p["entryPrice"]),
            liquidation_price=liquidation or None,
        )
