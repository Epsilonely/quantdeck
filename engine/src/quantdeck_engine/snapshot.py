"""The engine state clients see (D-33), as JSON-ready values.

Amounts are fixed-point strings, as Binance sends them, so no client parses them as floats.
The shape is recorded in docs/memory-bank/techContext.md.
"""

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from quantdeck_engine.config import StrategyConfig
from quantdeck_engine.times import iso_utc
from quantdeck_engine.trading.state import SymbolState
from quantdeck_engine.trading.strategy_runner import StrategyRunner

if TYPE_CHECKING:
    from quantdeck_engine.run import Engine


CENT = Decimal("0.01")


def fixed_point(amount: Decimal) -> str:
    """Fixed-point text, never exponent notation such as `0E-8` (D-33)."""
    return format(amount, "f")


def engine_snapshot(engine: "Engine") -> dict[str, Any]:
    daily = engine.checker.latest
    unrealized = _unrealized_total(engine)
    return {
        "network": "testnet" if engine.testnet else "mainnet",
        "started_at": iso_utc(engine.started_at),
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
            # Display only: the limit and the kill switch count realized results (D-24 note).
            "unrealized_pnl": None
            if unrealized is None
            else fixed_point(unrealized.quantize(CENT)),
            "loss_with_unrealized": None
            if unrealized is None
            else fixed_point((daily.loss - unrealized).quantize(CENT)),
        },
        "balances": {
            asset: fixed_point(amount)
            for asset, amount in sorted(engine.state.wallet_balances.items())
            if amount != 0
        },
        "symbols": [_symbol_snapshot(engine, symbol) for symbol in engine.traders],
    }


def _unrealized(state: SymbolState, mark: Decimal) -> Decimal:
    """A position's unrealized PnL at `mark`, before the fee to close it."""
    return state.position_amount * (mark - state.entry_price)


def _roi_pct(state: SymbolState, unrealized: Decimal, leverage: int) -> Decimal:
    """`unrealized` as a percent of the position's initial margin at entry, |amount| × entry
    price ÷ leverage: the price move times the leverage."""
    margin = abs(state.position_amount) * state.entry_price / leverage
    return unrealized / margin * 100


def _unrealized_total(engine: "Engine") -> Decimal | None:
    """Every open position's unrealized PnL at the display-only mark prices (D-33), or None
    when no position is open or a position's mark price hasn't arrived yet."""
    open_positions = [(s, st) for s, st in engine.state.symbols.items() if st.position_amount]
    if not open_positions:
        return None
    total = Decimal(0)
    for symbol, state in open_positions:
        mark = engine.mark_prices.get(symbol)
        if mark is None:
            return None
        total += _unrealized(state, mark)
    return total


def _symbol_snapshot(engine: "Engine", symbol: str) -> dict[str, Any]:
    state = engine.state.symbols[symbol]
    mark = engine.mark_prices.get(symbol)
    config = engine.symbols[symbol]
    position = None
    if state.position_amount != 0:
        unrealized = None if mark is None else _unrealized(state, mark)
        position = {
            "amount": fixed_point(state.position_amount),
            "entry_price": fixed_point(state.entry_price),
            "unrealized_pnl": None
            if unrealized is None
            else fixed_point(unrealized.quantize(CENT)),
            # Display only, on the symbol's set leverage. A position found at startup under
            # another leverage halts the symbol (D-16), and this is then off by their ratio.
            "roi_pct": None
            if unrealized is None
            else fixed_point(_roi_pct(state, unrealized, config.leverage).quantize(CENT)),
        }
    trader = engine.traders[symbol]
    return {
        "symbol": symbol,
        "halted": trader.halt_reason,  # the engine's own halt (D-16, D-19, D-20)
        "paused_at": trader.paused_at,  # the user's pause (D-50)
        "leverage": config.leverage,
        "strategy": _strategy(config.strategy, engine.runners.get(symbol)),
        # The stream's mark price carries more digits than the symbol's prices; the
        # unrealized PnL above uses it unrounded.
        "mark_price": None if mark is None else fixed_point(trader.rules.nearest_price(mark)),
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


def _strategy(
    config: StrategyConfig | None, runner: StrategyRunner | None
) -> dict[str, Any] | None:
    """The symbol's strategy setting (D-47) and what it last did (D-43)."""
    if config is None:
        return None
    candle = None if runner is None else runner.last_candle
    signal = None if runner is None else runner.last_signal
    return {
        "name": config.name,
        "interval": config.interval,
        # Strings, like amounts, so a Decimal parameter never becomes a float.
        "params": config.params_text(),
        "last_candle": None
        if candle is None
        else {"open_time": iso_utc(candle.open_time / 1000), "close": fixed_point(candle.close)},
        "last_signal": None
        if signal is None
        else {
            "candle_open_time": iso_utc(signal.candle_open_time / 1000),
            "action": signal.action,
            "stop_price": None if signal.stop_price is None else fixed_point(signal.stop_price),
            "result": str(signal.result),
            "outcome": signal.outcome,
        },
        "stopped": None if runner is None else runner.stopped,
    }
