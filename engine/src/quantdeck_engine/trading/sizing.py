"""Entry sizing (D-22, D-25), the liquidation check (D-16, D-25), emergency stops (D-26),
and the daily loss arithmetic (D-23, D-24).

Pure functions and values: callers fetch prices, balances, rules, and brackets and pass
them in, so the backtest reuses all of it (D-37).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from quantdeck_engine.trading.rules import Side, SymbolRules

# D-25: the stop must sit within this fraction of the distance to the liquidation price.
MAX_STOP_TO_LIQUIDATION = Decimal("0.5")


class EntryRejected(Exception):
    """The entry can't be sized safely; skip it."""


@dataclass(frozen=True)
class Bracket:
    """One notional bracket from `leverageBracket`."""

    notional_floor: Decimal
    notional_cap: Decimal
    maint_margin_ratio: Decimal
    cum: Decimal  # maintenance amount, subtracted from notional x ratio
    initial_leverage: int  # the highest leverage allowed in this bracket

    @classmethod
    def from_api(cls, b: dict[str, Any]) -> "Bracket":
        # These arrive as JSON numbers; str() keeps a float's shortest decimal form.
        return cls(
            notional_floor=Decimal(str(b["notionalFloor"])),
            notional_cap=Decimal(str(b["notionalCap"])),
            maint_margin_ratio=Decimal(str(b["maintMarginRatio"])),
            cum=Decimal(str(b["cum"])),
            initial_leverage=int(b["initialLeverage"]),
        )


@dataclass(frozen=True)
class EntryPlan:
    side: Side
    quantity: Decimal
    stop_price: Decimal  # rounded to the tick size
    reference_price: Decimal  # the price the plan was sized at
    risk: Decimal  # quantity x stop distance; at most the requested risk amount
    liquidation_price: Decimal  # estimated
    trimmed_by: str | None  # the cap that reduced the quantity, if any


def round_stop(side: Side, stop_price: Decimal, rules: SymbolRules) -> Decimal:
    """Round toward the entry, so the stop never sits farther away than asked."""
    return rules.ceil_price(stop_price) if side is Side.LONG else rules.floor_price(stop_price)


def bracket_for(brackets: list[Bracket], notional: Decimal) -> Bracket:
    for bracket in brackets:
        if bracket.notional_floor <= notional < bracket.notional_cap:
            return bracket
    raise EntryRejected(f"No leverage bracket covers a notional of {notional}")


def max_notional_at(brackets: list[Bracket], leverage: int) -> Decimal:
    """The largest position notional the exchange allows at this leverage."""
    caps = [b.notional_cap for b in brackets if b.initial_leverage >= leverage]
    if not caps:
        raise EntryRejected(f"Leverage {leverage}x exceeds every bracket")
    return max(caps)


def isolated_liquidation_price(
    side: Side,
    entry_price: Decimal,
    quantity: Decimal,
    leverage: int,
    brackets: list[Bracket],
) -> Decimal:
    """Estimate where a new isolated position would be liquidated.

    Binance's formula (liquidation price FAQ) for an isolated one-way position: liquidation
    happens when the isolated wallet balance + PnL falls to the maintenance margin
    (notional x ratio - cum). For a new position that balance is the initial margin,
    notional / leverage. If the notional at the result falls in another bracket, the FAQ
    says to recompute with that bracket.
    """
    notional = quantity * entry_price
    margin = notional / leverage
    bracket = bracket_for(brackets, notional)
    for _ in range(len(brackets)):
        mmr, cum = bracket.maint_margin_ratio, bracket.cum
        if side is Side.LONG:
            price = (notional - margin - cum) / (quantity * (1 - mmr))
        else:
            price = (notional + margin + cum) / (quantity * (1 + mmr))
        at_price = bracket_for(brackets, max(quantity * price, Decimal(0)))
        if at_price == bracket:
            break
        bracket = at_price
    return price


def size_entry(
    *,
    side: Side,
    reference_price: Decimal,
    stop_price: Decimal,
    risk_amount: Decimal,
    base_balance: Decimal,
    max_notional_multiple: Decimal,
    available_balance: Decimal,
    leverage: int,
    rules: SymbolRules,
    brackets: list[Bracket],
) -> EntryPlan:
    """Size an entry so a stop-out loses at most `risk_amount` (D-22).

    `risk_amount` must already include every risk cap the caller applies, such as the room
    left under the daily loss limit (D-24). Raises `EntryRejected` when the entry must be
    skipped.
    """
    stop = round_stop(side, stop_price, rules)
    distance = (reference_price - stop) * side.sign
    if distance <= 0:
        raise EntryRejected(
            f"Stop {stop} is not on the losing side of {reference_price} for a {side} entry"
        )
    if risk_amount <= 0:
        raise EntryRejected(f"No risk amount available ({risk_amount})")

    quantity = risk_amount / distance
    caps = {
        "position cap": base_balance * max_notional_multiple / reference_price,
        "available margin": available_balance * leverage / reference_price,
        "leverage bracket": max_notional_at(brackets, leverage) / reference_price,
        "max quantity": rules.max_qty,
    }
    trimmed_by = None
    for name, cap in caps.items():
        if cap < quantity:
            quantity, trimmed_by = cap, name
    quantity = rules.floor_quantity(quantity)

    if quantity < rules.min_qty or quantity * reference_price < rules.min_notional:
        raise EntryRejected(
            f"Quantity {quantity} is below the minimum (min qty {rules.min_qty}, "
            f"min notional {rules.min_notional})"
            + (f"; trimmed by {trimmed_by}" if trimmed_by else "")
        )

    liquidation = isolated_liquidation_price(side, reference_price, quantity, leverage, brackets)
    liquidation_distance = (reference_price - liquidation) * side.sign
    if distance > liquidation_distance * MAX_STOP_TO_LIQUIDATION:
        raise EntryRejected(
            f"Stop {stop} is {distance} from {reference_price}, more than half the distance "
            f"to the estimated liquidation price {liquidation:.2f}"
        )

    return EntryPlan(
        side=side,
        quantity=quantity,
        stop_price=stop,
        reference_price=reference_price,
        risk=quantity * distance,
        liquidation_price=liquidation,
        trimmed_by=trimmed_by,
    )


def emergency_stop_price(
    side: Side,
    entry_price: Decimal,
    quantity: Decimal,
    risk_amount: Decimal,
    rules: SymbolRules,
) -> Decimal:
    """The stop for a position found without one: where its loss equals one risk amount (D-26).

    `quantity` is the absolute position size. The caller still checks that the price isn't
    already crossed and doesn't sit beyond the liquidation price (D-19).
    """
    distance = risk_amount / abs(quantity)
    return round_stop(side, entry_price - distance * side.sign, rules)


# Daily loss (D-23, D-24)


def risk_amount(base_balance: Decimal, risk_per_trade_percent: Decimal) -> Decimal:
    """The loss one stop-out may cost: a fraction of the day's base balance (D-22, D-23)."""
    return base_balance * risk_per_trade_percent / 100


def daily_limit(base_balance: Decimal, daily_loss_limit_percent: Decimal) -> Decimal:
    """The net loss that fires the kill switch: a fraction of the day's base balance (D-24)."""
    return base_balance * daily_loss_limit_percent / 100


@dataclass(frozen=True)
class DailyLoss:
    """Today's loss against the daily limit, both from the day's base balance (D-23, D-24)."""

    base: Decimal
    loss: Decimal  # negative after a net gain
    limit: Decimal

    @property
    def room(self) -> Decimal:
        return self.limit - self.loss

    @property
    def reached(self) -> bool:
        return self.loss >= self.limit

    def describe(self, asset: str) -> str:
        return (
            f"today's loss {self.loss:.2f} {asset}, daily limit {self.limit:.2f} {asset} "
            f"(base {self.base:.2f})"
        )
