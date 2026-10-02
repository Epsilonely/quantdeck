"""QuantDeck trading engine."""

import argparse
import asyncio
import logging
import sys

import httpx
from websockets.exceptions import WebSocketException

from quantdeck_engine.binance.rest import BinanceAPIError
from quantdeck_engine.check import run_check
from quantdeck_engine.config import ConfigError, load_settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="quantdeck-engine")
    commands = parser.add_subparsers(dest="command")
    check = commands.add_parser("check", help="read-only connection check (places no orders)")
    check.add_argument("--symbol", default="BTCUSDT")
    check.add_argument("--interval", default="1m")
    check.add_argument("--count", type=int, default=3, help="kline updates to wait for")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    try:
        settings = load_settings()
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        raise SystemExit(1) from None

    if args.command == "check":
        try:
            asyncio.run(run_check(settings, args.symbol.upper(), args.interval, args.count))
        except (BinanceAPIError, httpx.HTTPError, WebSocketException, OSError) as e:
            print(f"Check failed: {e}", file=sys.stderr)
            raise SystemExit(1) from None
        return

    network = "testnet" if settings.testnet else "MAINNET"
    print(f"QuantDeck engine: settings loaded ({network}, {settings.endpoints.rest}).")
    print("The trading loop is not implemented yet. Run `quantdeck-engine check`.")
