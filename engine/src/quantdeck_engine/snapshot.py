"""The engine state clients see (D-33), as JSON-ready values.

Amounts are fixed-point strings, as Binance sends them, so no client parses them as floats.
The shape is recorded in docs/memory-bank/techContext.md.
"""

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from quantdeck_engine.config import StrategyConfig
from quantdeck_engine.times import iso_utc
from quantdeck_engine.trading.strategy_runner import StrategyRunner

if TYPE_CHECKING:
    from quantdeck_engine.run import Engine


def fixed_point(amount: Decimal) -> str:
    """Fixed-point text, never exponent notation such as `0E-8` (D-33)."""
    return format(amount, "f")


def engine_snapshot(engine: "Engine") -> dict[str, Any]:
    daily = engine.checker.latest
    return {
        "network": "testnet" if engine.testnet else "mainnet",
        "started_at": iso_utc(engine.started_at),
        "strategy": _strategy_config(engine.strategy),
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
        "strategy": _strategy_state(engine.runners.get(symbol)),
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


def _strategy_config(config: StrategyConfig | None) -> dict[str, Any] | None:
    if config is None:
        return None
    return {
        "name": config.name,
        "interval": config.interval,
        # Strings, like amounts, so a Decimal parameter never becomes a float.
        "params": {
            key: fixed_point(value) if isinstance(value, Decimal) else str(value)
            for key, value in config.params.items()
        },
    }


def _strategy_state(runner: StrategyRunner | None) -> dict[str, Any] | None:
    if runner is None:
        return None
    candle = runner.last_candle
    signal = runner.last_signal
    return {
        "last_candle": None
        if candle is None
        else {"open_time": iso_utc(candle.open_time / 1000), "close": fixed_point(candle.close)},
        "last_signal": None
        if signal is None
        else {
            "candle_open_time": iso_utc(signal.candle_open_time / 1000),
            "action": signal.action,
            "stop_price": None if signal.stop_price is None else fixed_point(signal.stop_price),
            "outcome": signal.outcome,
        },
        "stopped": runner.stopped,
    }
