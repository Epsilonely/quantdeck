"""QuantDeck trading engine."""

import argparse
import asyncio
import logging

import httpx
from websockets.exceptions import WebSocketException

from quantdeck_engine.binance.rest import BinanceAPIError
from quantdeck_engine.check import CheckFailed, run_check
from quantdeck_engine.config import ConfigError, load_settings
from quantdeck_engine.logs import setup_logging

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(prog="quantdeck-engine")
    commands = parser.add_subparsers(dest="command")
    check = commands.add_parser("check", help="read-only connection check (places no orders)")
    check.add_argument("--symbol", default="BTCUSDT")
    check.add_argument("--interval", default="1m")
    check.add_argument("--count", type=int, default=3, help="kline updates to wait for")
    args = parser.parse_args()

    log_file = setup_logging()

    try:
        settings = load_settings()
    except ConfigError as e:
        logger.error("Config error: %s", e)
        raise SystemExit(1) from None
    logging.getLogger().setLevel(settings.log_level)

    network = "testnet" if settings.testnet else "MAINNET"
    logger.info(
        "Starting %s on %s (%s), log level %s, log file %s",
        args.command or "engine",
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

    print(f"QuantDeck engine: settings loaded ({network}, {settings.endpoints.rest}).")
    print("The trading loop is not implemented yet. Run `quantdeck-engine check`.")
