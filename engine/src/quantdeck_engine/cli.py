"""The `quantdeck-engine` command line: `run`, `check`, `trial`, `lift-halt`, `backtest`.

Each command is implemented in its own module; this one parses arguments, sets up logging
and settings, and turns a command's failure into a logged error and exit code 1.
"""

import argparse
import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from websockets.exceptions import WebSocketException

from quantdeck_engine.backtest.data import DataError
from quantdeck_engine.backtest.run import (
    BacktestFailed,
    fetch_market,
    leverage_for,
    run_backtest,
)
from quantdeck_engine.backtest.simulator import Costs
from quantdeck_engine.binance.rest import REQUEST_ERRORS, UnknownSymbol
from quantdeck_engine.check import CheckFailed, run_check
from quantdeck_engine.config import ConfigError, load_settings, load_trading_config
from quantdeck_engine.lock import EngineAlreadyRunning
from quantdeck_engine.logs import setup_logging
from quantdeck_engine.run import RunFailed, run_engine
from quantdeck_engine.startup import StartupFailed
from quantdeck_engine.strategy.registry import (
    STRATEGIES,
    StrategyError,
    make_strategy,
    parse_parameters,
)
from quantdeck_engine.symbols import SymbolStore, load_symbols
from quantdeck_engine.trading.executor import OrderOutcomeUnclear, TradingHalted
from quantdeck_engine.trading.halt import HaltStore
from quantdeck_engine.trading.rules import Side
from quantdeck_engine.trial import TrialFailed, run_trial

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(prog="quantdeck-engine")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser(
        "run",
        help="TESTNET ONLY: run the engine until Ctrl+C (strategy, position protection, kill "
        "switch)",
    )
    check = commands.add_parser("check", help="read-only connection check (places no orders)")
    check.add_argument("--symbol", default="BTCUSDT")
    check.add_argument("--interval", default="1m")
    check.add_argument("--count", type=int, default=3, help="kline updates to wait for")
    trial = commands.add_parser(
        "trial",
        help="TESTNET ONLY: run entry, stop, close, and the kill switch once (places orders)",
    )
    trial.add_argument("--symbol", help="a symbol from symbols.json (default: the first)")
    trial.add_argument("--side", choices=["long", "short"], default="long")
    trial.add_argument("--stop-percent", type=Decimal, default=Decimal(1))
    commands.add_parser("lift-halt", help="lift the kill switch's halt")
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    backtest = commands.add_parser(
        "backtest", help="run a strategy over mainnet history (places no orders)"
    )
    backtest.add_argument("--strategy", choices=sorted(STRATEGIES), required=True)
    backtest.add_argument("--symbol", default="BTCUSDT")
    backtest.add_argument("--interval", default="1h")
    backtest.add_argument("--start", type=date.fromisoformat, default=date(2020, 1, 1))
    backtest.add_argument("--end", type=date.fromisoformat, default=yesterday)
    backtest.add_argument(
        "--param", action="append", default=[], metavar="KEY=VALUE", help="strategy parameter"
    )
    backtest.add_argument("--balance", type=Decimal, default=Decimal(5000), help="USDT")
    backtest.add_argument("--fee", type=Decimal, default=Decimal("0.0005"), help="taker fee")
    backtest.add_argument(
        "--maker-fee", type=Decimal, default=Decimal("0.0002"), help="fee on limit entries"
    )
    backtest.add_argument("--slippage", type=Decimal, default=Decimal("0.0002"))
    backtest.add_argument("--stop-slippage", type=Decimal, default=Decimal("0.0005"))
    backtest.add_argument("--leverage", type=int, help="default: the symbol's in symbols.json")
    backtest.add_argument("--trades-csv", type=Path, help="write every trade to this file")
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return

    log_file = setup_logging()

    if args.command == "lift-halt":
        # Needs no settings or exchange access: the halt is a local record (D-29).
        try:
            lift_halt(HaltStore())
        except OSError as e:
            logger.error("Could not lift the halt: %s", e)
            raise SystemExit(1) from None
        return

    try:
        settings = load_settings()
    except ConfigError as e:
        logger.error("Config error: %s", e)
        raise SystemExit(1) from None
    logging.getLogger().setLevel(settings.log_level)

    network = "testnet" if settings.testnet else "MAINNET"
    logger.info(
        "Starting %s on %s (%s), log level %s, log file %s",
        args.command,
        network,
        settings.endpoints.rest,
        logging.getLevelName(settings.log_level),
        log_file,
    )

    if args.command == "check":
        try:
            asyncio.run(run_check(settings, args.symbol.upper(), args.interval, args.count))
        except (CheckFailed, UnknownSymbol, *REQUEST_ERRORS, WebSocketException, OSError) as e:
            logger.error("Check failed: %s", e)
            raise SystemExit(1) from None
        return

    try:
        trading = load_trading_config()
    except ConfigError as e:
        logger.error("Config error: %s", e)
        raise SystemExit(1) from None

    if args.command == "backtest":
        symbol = args.symbol.upper()
        try:
            params = parse_parameters(args.strategy, args.param)
            strategy = make_strategy(args.strategy, args.interval, params)
            # Read only: a backtest never creates symbols.json (D-47).
            symbols = load_symbols(SymbolStore(), trading, create=False)
            leverage = leverage_for(symbols, symbol, args.leverage)
            market = asyncio.run(fetch_market(settings, symbol, leverage))
            costs = Costs(args.fee, args.slippage, args.stop_slippage, args.maker_fee)
            report = run_backtest(
                strategy,
                symbol,
                args.start,
                args.end,
                market,
                trading,
                costs,
                args.balance,
                trades_csv=args.trades_csv,
            )
        except (
            ConfigError,
            StrategyError,
            BacktestFailed,
            DataError,
            UnknownSymbol,
            *REQUEST_ERRORS,
            OSError,
        ) as e:
            logger.error("Backtest failed: %s", e)
            raise SystemExit(1) from None
        print(report)
        return

    if args.command == "run":
        try:
            asyncio.run(run_engine(settings, trading))
        except KeyboardInterrupt:
            logger.info("Engine stopped by Ctrl+C")
        except (
            ConfigError,
            EngineAlreadyRunning,
            RunFailed,
            StartupFailed,
            UnknownSymbol,
            *REQUEST_ERRORS,
            OSError,
        ) as e:
            # The running engine handles request errors itself, so these come from startup.
            logger.error("Engine failed to start: %s", e)
            raise SystemExit(1) from None
        except Exception:
            logger.critical(
                "Engine crashed; exchange-side stops still protect open positions", exc_info=True
            )
            raise SystemExit(1) from None
        return

    if args.command == "trial":
        symbol = args.symbol.upper() if args.symbol else None
        side = Side.LONG if args.side == "long" else Side.SHORT
        try:
            asyncio.run(run_trial(settings, trading, symbol, side, args.stop_percent))
        except (
            ConfigError,
            TrialFailed,
            StartupFailed,
            UnknownSymbol,
            EngineAlreadyRunning,
            TradingHalted,
            OrderOutcomeUnclear,
            *REQUEST_ERRORS,
            WebSocketException,
            OSError,
            TimeoutError,
        ) as e:
            logger.error("Trial failed: %s", e)
            raise SystemExit(1) from None


def lift_halt(store: HaltStore) -> None:
    record = store.load()
    if record is None:
        print("Not halted: there is no halt record.")
        return
    store.clear()
    logger.warning(
        "Kill-switch halt lifted by the user (fired at %s: %s)",
        record.halted_at or "an unknown time",
        record.reason,
    )
    print(
        "Halt lifted. Restart the engine to trade again. On the same UTC day the entry gate "
        "still blocks entries while today's loss is at the limit."
    )
