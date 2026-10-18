"""The engine state clients see (D-33), as JSON-ready values.

Amounts are fixed-point strings, as Binance sends them, so no client parses them as floats.
The shape is recorded in docs/memory-bank/techContext.md.
"""

from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from quantdeck_engine.run import Engine


def fixed_point(amount: Decimal) -> str:
    """Fixed-point text, never exponent notation such as `0E-8` (D-33)."""
    return format(amount, "f")


def engine_snapshot(engine: "Engine") -> dict[str, Any]:
    daily = engine.checker.latest
    return {
        "network": "testnet" if engine.testnet else "mainnet",
        "halt": {
            "halted": engine.guard.halted,
            "reason": engine.guard.halt_reason,
            "since": engine.guard.halted_at,
        },
        "daily_loss": None
        if daily is None
        else {
            "asset": engine.asset,
            "base": fixed_point(daily.base),
            "loss": fixed_point(daily.loss),
            "limit": fixed_point(daily.limit),
        },
        "balances": {
            asset: fixed_point(amount)
            for asset, amount in sorted(engine.state.wallet_balances.items())
            if amount != 0
        },
        "symbols": [_symbol_snapshot(engine, symbol) for symbol in engine.traders],
    }


def _symbol_snapshot(engine: "Engine", symbol: str) -> dict[str, Any]:
    state = engine.state.symbols[symbol]
    mark = engine.mark_prices.get(symbol)
    position = None
    if state.position_amount != 0:
        position = {
            "amount": fixed_point(state.position_amount),
            "entry_price": fixed_point(state.entry_price),
            "unrealized_pnl": None
            if mark is None
            else fixed_point(state.position_amount * (mark - state.entry_price)),
        }
    return {
        "symbol": symbol,
        "halted": engine.traders[symbol].halt_reason,
        "mark_price": None if mark is None else fixed_point(mark),
        "position": position,
        "orders": [
            {
                "client_id": o.client_id,
                "side": o.side,
                "type": o.order_type,
                "quantity": fixed_point(o.quantity),
                "reduce_only": o.reduce_only,
            }
            for o in sorted(state.orders.values(), key=lambda o: o.client_id)
        ],
        "stops": [
            {
                "client_algo_id": a.client_algo_id,
                "side": a.side,
                "type": a.order_type,
                "trigger_price": fixed_point(a.trigger_price),
                "close_position": a.close_position,
            }
            for a in sorted(state.algo_orders.values(), key=lambda a: a.client_algo_id)
        ],
    }
