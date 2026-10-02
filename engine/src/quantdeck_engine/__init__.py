"""QuantDeck trading engine."""

import sys

from quantdeck_engine.config import ConfigError, load_settings


def main() -> None:
    try:
        settings = load_settings()
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        raise SystemExit(1) from None

    network = "testnet" if settings.testnet else "MAINNET"
    print(f"QuantDeck engine: settings loaded ({network}, {settings.endpoints.rest}).")
    print("Nothing else is implemented yet.")
