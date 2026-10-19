"""`quantdeck-engine backtest`: one strategy over one symbol's mainnet history (D-37 to D-40).

Sizing uses the configured network's current symbol rules and leverage brackets (testnet by
default, so the same conditions as the forward test), and the risk settings in
`engine/config.toml` (D-28, D-37). Prices come from mainnet history (D-38).
"""

import csv
import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx

from quantdeck_engine.backtest.data import HistoricalData
from quantdeck_engine.backtest.report import format_report
from quantdeck_engine.backtest.simulator import Costs, Market, Simulator, Trade
from quantdeck_engine.binance.rest import BinanceRestClient
from quantdeck_engine.config import MAX_LEVERAGE, Settings, TradingConfig
from quantdeck_engine.strategy.base import Strategy
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


def leverage_for(trading: TradingConfig, symbol: str, override: int | None) -> int:
    if override is not None:
        if not 1 <= override <= MAX_LEVERAGE:
            raise BacktestFailed(f"--leverage must be between 1 and {MAX_LEVERAGE}")
        return override
    if symbol in trading.symbols:
        return trading.symbols[symbol].leverage
    raise BacktestFailed(f"{symbol} is not in config.toml; pass --leverage")


def run_backtest(
    strategy: Strategy,
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
    with httpx.Client() as http:
        data = HistoricalData(http)
        candles = data.candles(symbol, strategy.interval, start, end)
        marks = data.candles(symbol, strategy.interval, start, end, dataset="markPriceKlines")
        funding = data.funding_rates(symbol, start, end)
    logger.info(
        "Backtesting %r on %s: %d candles, %d mark price candles, %d funding rates",
        strategy,
        symbol,
        len(candles.klines),
        len(marks.klines),
        len(funding),
    )
    result = Simulator(strategy, market, trading, costs, balance).run(
        candles.klines, marks.klines, funding
    )
    if trades_csv is not None:
        write_trades_csv(trades_csv, result.trades)
    maker = f" (maker {costs.maker_fee * 100}%)" if result.limit_entries.placed else ""
    title = (
        f"{strategy!r} on {symbol}, {start} to {end}, leverage {market.leverage}x, "
        f"risk {trading.risk_per_trade_percent}%/trade, daily limit "
        f"{trading.daily_loss_limit_percent}%, fee {costs.taker_fee * 100}%{maker}, slippage "
        f"{costs.market_slippage * 100}% (stops {costs.stop_slippage * 100}%)"
    )
    return format_report(title, result)


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
