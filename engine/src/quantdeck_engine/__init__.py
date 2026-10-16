"""QuantDeck trading engine."""

import argparse
import asyncio
import logging
from decimal import Decimal

import httpx
from websockets.exceptions import WebSocketException

from quantdeck_engine.binance.rest import BinanceAPIError
from quantdeck_engine.check import CheckFailed, run_check
from quantdeck_engine.config import ConfigError, load_settings, load_trading_config
from quantdeck_engine.lock import EngineAlreadyRunning
from quantdeck_engine.logs import setup_logging
from quantdeck_engine.run import RunFailed, run_engine
from quantdeck_engine.trading.executor import OrderOutcomeUnclear, TradingHalted
from quantdeck_engine.trading.halt import HaltStore
from quantdeck_engine.trading.rules import Side
from quantdeck_engine.trial import TrialFailed, run_trial

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(prog="quantdeck-engine")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser(
        "run", help="TESTNET ONLY: run the engine until Ctrl+C (protects positions, kill switch)"
    )
    check = commands.add_parser("check", help="read-only connection check (places no orders)")
    check.add_argument("--symbol", default="BTCUSDT")
    check.add_argument("--interval", default="1m")
    check.add_argument("--count", type=int, default=3, help="kline updates to wait for")
    trial = commands.add_parser(
        "trial",
        help="TESTNET ONLY: run entry, stop, close, and the kill switch once (places orders)",
    )
    trial.add_argument("--symbol", help="a symbol from config.toml (default: the first)")
    trial.add_argument("--side", choices=["long", "short"], default="long")
    trial.add_argument("--stop-percent", type=Decimal, default=Decimal(1))
    commands.add_parser("lift-halt", help="lift the kill switch's halt (D-27)")
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
        except (CheckFailed, BinanceAPIError, httpx.HTTPError, WebSocketException, OSError) as e:
            logger.error("Check failed: %s", e)
            raise SystemExit(1) from None
        return

    try:
        trading = load_trading_config()
    except ConfigError as e:
        logger.error("Config error: %s", e)
        raise SystemExit(1) from None

    if args.command == "run":
        try:
            asyncio.run(run_engine(settings, trading))
        except KeyboardInterrupt:
            logger.info("Engine stopped by Ctrl+C")
        except (EngineAlreadyRunning, RunFailed, BinanceAPIError, httpx.HTTPError, OSError) as e:
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
        symbol = (args.symbol or next(iter(trading.symbols))).upper()
        side = Side.LONG if args.side == "long" else Side.SHORT
        try:
            asyncio.run(run_trial(settings, trading, symbol, side, args.stop_percent))
        except (
            TrialFailed,
            EngineAlreadyRunning,
            TradingHalted,
            OrderOutcomeUnclear,
            BinanceAPIError,
            httpx.HTTPError,
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
        "still blocks entries while today's loss is at the limit (D-27)."
    )
