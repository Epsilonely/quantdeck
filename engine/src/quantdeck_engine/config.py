"""Engine settings, loaded once at startup.

Values come from the repo-root `.env`, overridden by real environment variables.
The rest of the engine receives a `Settings` object and never reads `os.environ` itself.
"""

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

logger = logging.getLogger(__name__)

# engine/src/quantdeck_engine/config.py -> repo root
DEFAULT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class ConfigError(Exception):
    """Settings are missing or invalid; the engine must not start."""


class Secret:
    """A string that never shows its value in repr, str, f-strings, or logs."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret('***')"

    def __str__(self) -> str:
        return "***"


@dataclass(frozen=True)
class Endpoints:
    rest: str
    ws: str  # root only; stream URLs add a route such as /market or /private


# Verified 2026-10-02 against the official docs; see docs/memory-bank/binanceNotes.md.
MAINNET = Endpoints(rest="https://fapi.binance.com", ws="wss://fstream.binance.com")
TESTNET = Endpoints(rest="https://demo-fapi.binance.com", ws="wss://demo-fstream.binance.com")


@dataclass(frozen=True)
class Settings:
    testnet: bool
    endpoints: Endpoints
    api_key: Secret
    api_secret: Secret


def load_settings(
    env_file: Path | None = DEFAULT_ENV_FILE,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    values: dict[str, str | None] = {}
    if env_file is not None and env_file.is_file():
        values.update(dotenv_values(env_file))
    values.update(os.environ if environ is None else environ)
    return parse_settings(values)


def parse_settings(values: Mapping[str, str | None]) -> Settings:
    testnet = _parse_testnet(values.get("BINANCE_TESTNET"))

    api_key = _non_blank(values.get("BINANCE_API_KEY"))
    api_secret = _non_blank(values.get("BINANCE_API_SECRET"))
    if api_key is None or api_secret is None:
        missing = [
            name
            for name, value in (("BINANCE_API_KEY", api_key), ("BINANCE_API_SECRET", api_secret))
            if value is None
        ]
        raise ConfigError(f"Missing required setting(s): {', '.join(missing)}")

    if not testnet:
        logger.warning("BINANCE_TESTNET=false: connecting to MAINNET. Orders use real funds.")

    return Settings(
        testnet=testnet,
        endpoints=TESTNET if testnet else MAINNET,
        api_key=Secret(api_key),
        api_secret=Secret(api_secret),
    )


def _parse_testnet(raw: str | None) -> bool:
    # Mainnet only on an exact "false". Anything unexpected stops the engine rather than
    # guessing, so a typo can never silently switch to real funds.
    value = (raw or "").strip()
    if value in ("", "true"):
        return True
    if value == "false":
        return False
    raise ConfigError(f"BINANCE_TESTNET must be 'true' or 'false', got {value!r}")


def _non_blank(raw: str | None) -> str | None:
    value = (raw or "").strip()
    return value or None
