"""Per-symbol order rules from `exchangeInfo`, and rounding to them.

Filter values differ per symbol and per network, so they are always read at runtime
(binanceNotes.md, Symbol filters).
"""

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import StrEnum
from typing import Any


class Side(StrEnum):
    """Position direction. One-way mode (D-05): a symbol has one net position."""

    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def entry_order_side(self) -> str:
        return "BUY" if self is Side.LONG else "SELL"

    @property
    def close_order_side(self) -> str:
        return "SELL" if self is Side.LONG else "BUY"

    @property
    def sign(self) -> int:
        return 1 if self is Side.LONG else -1


@dataclass(frozen=True)
class SymbolRules:
    symbol: str
    margin_asset: str
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    max_qty: Decimal
    min_notional: Decimal

    @classmethod
    def from_exchange_info(cls, symbol_info: dict[str, Any]) -> "SymbolRules":
        filters = {f["filterType"]: f for f in symbol_info["filters"]}
        price = filters["PRICE_FILTER"]
        lot = filters["LOT_SIZE"]
        # Entries and exits are market orders (D-17), which MARKET_LOT_SIZE also limits.
        # Apply the stricter of the two lot filters.
        market_lot = filters.get("MARKET_LOT_SIZE", lot)
        return cls(
            symbol=symbol_info["symbol"],
            margin_asset=symbol_info["marginAsset"],
            tick_size=Decimal(price["tickSize"]),
            step_size=max(Decimal(lot["stepSize"]), Decimal(market_lot["stepSize"])),
            min_qty=max(Decimal(lot["minQty"]), Decimal(market_lot["minQty"])),
            max_qty=min(Decimal(lot["maxQty"]), Decimal(market_lot["maxQty"])),
            min_notional=Decimal(filters["MIN_NOTIONAL"]["notional"]),
        )

    def floor_quantity(self, quantity: Decimal) -> Decimal:
        return _to_multiple(quantity, self.step_size, ROUND_FLOOR)

    def floor_price(self, price: Decimal) -> Decimal:
        return _to_multiple(price, self.tick_size, ROUND_FLOOR)

    def ceil_price(self, price: Decimal) -> Decimal:
        return _to_multiple(price, self.tick_size, ROUND_CEILING)


def _to_multiple(value: Decimal, step: Decimal, rounding: str) -> Decimal:
    return (value / step).to_integral_value(rounding=rounding) * step
