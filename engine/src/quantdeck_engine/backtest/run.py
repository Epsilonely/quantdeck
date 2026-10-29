"""`quantdeck-engine backtest`: one strategy over one symbol's mainnet history (D-37 to D-40).

Sizing uses the configured network's current symbol rules and leverage brackets (testnet by
default, so the same conditions as the forward test), and the risk settings in
`engine/config.toml` (D-28, D-37). Prices come from mainnet history (D-38). Several settings
of the strategy run over the same data, read once.
"""

import csv
import logging
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx

from quantdeck_engine.backtest.data import HistoricalData
from quantdeck_engine.backtest.report import format_grid, format_report, summarize
from quantdeck_engine.backtest.simulator import Costs, Market, Result, Simulator, Trade
from quantdeck_engine.binance.rest import BinanceRestClient
from quantdeck_engine.config import MAX_LEVERAGE, Settings, SymbolConfig, TradingConfig
from quantdeck_engine.strategy.base import Strategy
from quantdeck_engine.strategy.registry import (
    StrategyError,
    make_strategy,
    parameter_text,
    parameters_of,
    parse_parameter_grid,
)
from quantdeck_engine.times import utc_text
from quantdeck_engine.trading.rules import SymbolRules
from quantdeck_engine.trading.sizing import Bracket

logger = logging.getLogger(__name__)


class BacktestFailed(Exception):
    """The backtest couldn't run."""


async def fetch_market(settings: Settings, symbol: str, leverage: int) -> Market:
    """The symbol's current rules and brackets from the configured network (read only)."""
    async with BinanceRestClient(settings) as client:
        await client.sync_time()
        symbol_info = await client.symbol_info(symbol)
        brackets = [Bracket.from_api(b) for b in await client.leverage_brackets(symbol)]
    return Market(SymbolRules.from_exchange_info(symbol_info), brackets, leverage)


def leverage_for(symbols: Mapping[str, SymbolConfig], symbol: str, override: int | None) -> int:
    """`--leverage`, or else the symbol's in `symbols.json` (D-47)."""
    if override is not None:
        if not 1 <= override <= MAX_LEVERAGE:
            raise BacktestFailed(f"--leverage must be between 1 and {MAX_LEVERAGE}")
        return override
    if symbol in symbols:
        return symbols[symbol].leverage
    raise BacktestFailed(f"{symbol} is not one of the engine's symbols; pass --leverage")


def strategies_for(name: str, interval: str, items: Sequence[str]) -> list[Strategy]:
    """Strategy `name` with each setting the `--param` items give. Of several, a setting the
    strategy refuses, such as an exit channel longer than its entry channel, is left out with
    a warning."""
    settings = parse_parameter_grid(name, items)
    if len(settings) == 1:
        return [make_strategy(name, interval, settings[0])]
    strategies = []
    for params in settings:
        try:
            strategies.append(make_strategy(name, interval, params))
        except StrategyError as e:
            text = " ".join(f"{key}={parameter_text(value)}" for key, value in params.items())
            logger.warning("Leaving out %s: %s", text, e)
    if not strategies:
        raise BacktestFailed(f"{name} refused every setting")
    return strategies


def run_backtest(
    strategies: Sequence[Strategy],
    symbol: str,
    start: date,
    end: date,
    market: Market,
    trading: TradingConfig,
    costs: Costs,
    balance: Decimal,
    *,
    trades_csv: Path | None = None,
) -> str:
    """The report of one strategy, or a line for each of several settings of one strategy on
    one interval."""
    if trades_csv is not None and len(strategies) > 1:
        raise BacktestFailed("--trades-csv takes a single setting")
    interval = strategies[0].interval
    with httpx.Client() as http:
        data = HistoricalData(http)
        candles = data.candles(symbol, interval, start, end)
        marks = data.candles(symbol, interval, start, end, dataset="markPriceKlines")
        funding = data.funding_rates(symbol, start, end)
    logger.info(
        "Backtesting on %s %s: %d candles, %d mark price candles, %d funding rates",
        symbol,
        interval,
        len(candles.klines),
        len(marks.klines),
        len(funding),
    )

    def run(strategy: Strategy) -> Result:
        logger.info("Backtesting %r", strategy)
        return Simulator(strategy, market, trading, costs, balance).run(
            candles.klines, marks.klines, funding
        )

    def conditions(maker: bool) -> str:
        maker_fee = f" (maker {costs.maker_fee * 100}%)" if maker else ""
        return (
            f"on {symbol}, {start} to {end}, leverage {market.leverage}x, "
            f"risk {trading.risk_per_trade_percent}%/trade, daily limit "
            f"{trading.daily_loss_limit_percent}%, fee {costs.taker_fee * 100}%{maker_fee}, "
            f"slippage {costs.market_slippage * 100}% (stops {costs.stop_slippage * 100}%)"
        )

    if len(strategies) == 1:
        result = run(strategies[0])
        if trades_csv is not None:
            write_trades_csv(trades_csv, result.trades)
        return format_report(
            f"{strategies[0]!r} {conditions(result.limit_entries.placed > 0)}", result
        )
    common, labels = _settings(strategies)
    # Only the summary is kept: a result holds the equity at every candle.
    rows = [(label, summarize(run(s))) for label, s in zip(labels, strategies, strict=True)]
    title = f"{common} {conditions(any(s.limit_entries for s in strategies))}"
    return format_grid(title, rows)


def _settings(strategies: Sequence[Strategy]) -> tuple[str, list[str]]:
    """The strategy with the parameters its settings share, and each setting by the ones
    that differ."""
    settings = [parameters_of(s) for s in strategies]
    varying = [key for key in settings[0] if len({repr(p[key]) for p in settings}) > 1]
    shared = ", ".join(
        f"{key}={parameter_text(value)}" for key, value in settings[0].items() if key not in varying
    )
    first = strategies[0]
    common = f"{type(first).__name__}({first.interval}" + (f", {shared})" if shared else ")")
    labels = [" ".join(f"{key}={parameter_text(p[key])}" for key in varying) for p in settings]
    return common, labels


def write_trades_csv(path: Path, trades: list[Trade]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "entry_time_utc",
                "side",
                "quantity",
                "entry_price",
                "stop_price",
                "risk",
                "exit_time_utc",
                "exit_price",
                "exit_reason",
                "pnl",
                "fees",
                "funding",
                "net",
                "r",
            ]
        )
        for t in trades:
            writer.writerow(
                [
                    utc_text(t.entry_time),
                    t.side,
                    t.quantity,
                    f"{t.entry_price:.2f}",
                    t.stop_price,
                    f"{t.risk:.4f}",
                    utc_text(t.exit_time),
                    f"{t.exit_price:.2f}",
                    t.exit_reason,
                    f"{t.pnl:.4f}",
                    f"{t.fees:.4f}",
                    f"{t.funding:.4f}",
                    f"{t.net:.4f}",
                    f"{t.r_multiple:.3f}",
                ]
            )
