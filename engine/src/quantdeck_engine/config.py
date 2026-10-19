"""Engine settings, loaded once at startup.

Two sources, both read only here:

- `Settings`: secrets and environment from the repo-root `.env`, overridden by real
  environment variables. The rest of the engine never reads `os.environ` itself.
- `TradingConfig`: strategy and risk settings from `engine/config.toml` (D-28). Each risk
  value has a hard bound in code, so a typo stops the engine instead of trading on it.
"""

import logging
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from quantdeck_engine.binance.streams import INTERVAL_MS
from quantdeck_engine.paths import CONFIG_FILE, ENV_FILE
from quantdeck_engine.strategy.base import Strategy
from quantdeck_engine.strategy.registry import StrategyError, make_strategy

logger = logging.getLogger(__name__)

# Hard bounds (D-28). Raising one takes a code change on purpose.
MAX_RISK_PER_TRADE_PERCENT = Decimal(2)
MAX_DAILY_LOSS_LIMIT_PERCENT = Decimal(10)
MAX_NOTIONAL_MULTIPLE = Decimal(5)
MAX_LEVERAGE = 10
# Candles a strategy may need before it decides (D-43); the feed loads them in one request.
MAX_STRATEGY_WARMUP = 1000


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

LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


@dataclass(frozen=True)
class Settings:
    testnet: bool
    endpoints: Endpoints
    api_key: Secret
    api_secret: Secret
    log_level: int


def load_settings(
    env_file: Path | None = ENV_FILE,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    values: dict[str, str | None] = {}
    if env_file is not None and env_file.is_file():
        values.update(dotenv_values(env_file))
    values.update(os.environ if environ is None else environ)
    return parse_settings(values)


def parse_settings(values: Mapping[str, str | None]) -> Settings:
    testnet = _parse_testnet(values.get("BINANCE_TESTNET"))
    log_level = _parse_log_level(values.get("ENGINE_LOG_LEVEL"))

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
        log_level=log_level,
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


def _parse_log_level(raw: str | None) -> int:
    value = (raw or "").strip().upper()
    if not value:
        return logging.INFO
    if value not in LOG_LEVELS:
        raise ConfigError(f"ENGINE_LOG_LEVEL must be one of {', '.join(LOG_LEVELS)}, got {raw!r}")
    return LOG_LEVELS[value]


def _non_blank(raw: str | None) -> str | None:
    value = (raw or "").strip()
    return value or None


@dataclass(frozen=True)
class SymbolConfig:
    symbol: str
    leverage: int  # margin type is always isolated (D-16)


@dataclass(frozen=True)
class StrategyConfig:
    """The strategy `run` trades on every configured symbol (D-43)."""

    name: str
    interval: str
    params: Mapping[str, object]  # as config.toml gives them; `make_strategy` checks them

    def build(self) -> Strategy:
        """A new instance: each symbol gets its own."""
        return make_strategy(self.name, self.interval, self.params)

    def describe(self) -> str:
        """E.g. `donchian 4h (entry_period=55, exit_period=20)`."""
        params = ", ".join(f"{key}={value}" for key, value in self.params.items())
        return f"{self.name} {self.interval}" + (f" ({params})" if params else "")


@dataclass(frozen=True)
class TradingConfig:
    """Risk, per-symbol, and strategy settings from `engine/config.toml`.

    Percentages are of the day's base balance: the wallet balance at 00:00 UTC (D-23).
    """

    risk_per_trade_percent: Decimal  # D-22, D-23
    daily_loss_limit_percent: Decimal  # D-24
    max_notional_multiple: Decimal  # D-25: notional <= this x base balance
    symbols: Mapping[str, SymbolConfig]
    strategy: StrategyConfig | None = None  # None: `run` places no entries (D-43)


def load_trading_config(path: Path = CONFIG_FILE) -> TradingConfig:
    if not path.is_file():
        raise ConfigError(f"{path.name} not found; copy config.example.toml to {path} first")
    try:
        with path.open("rb") as f:
            # parse_float keeps TOML decimals exact: 0.1 stays Decimal("0.1").
            data = tomllib.load(f, parse_float=Decimal)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path.name}: {e}") from None
    return parse_trading_config(data)


def parse_trading_config(data: Mapping[str, Any]) -> TradingConfig:
    _reject_unknown(data, {"risk", "symbols", "strategy"}, "top level")

    risk = _table(data, "risk")
    _reject_unknown(
        risk,
        {"risk_per_trade_percent", "daily_loss_limit_percent", "max_notional_multiple"},
        "[risk]",
    )

    symbols_table = _table(data, "symbols")
    if not symbols_table:
        raise ConfigError("[symbols] must list at least one symbol, e.g. [symbols.BTCUSDT]")
    symbols: dict[str, SymbolConfig] = {}
    for name, table in symbols_table.items():
        where = f"[symbols.{name}]"
        if not isinstance(table, Mapping):
            raise ConfigError(f"{where} must be a table")
        if name != name.upper():
            raise ConfigError(f"{where}: symbol names are upper case, e.g. BTCUSDT")
        _reject_unknown(table, {"leverage"}, where)
        symbols[name] = SymbolConfig(
            symbol=name, leverage=_bounded_int(table, "leverage", 1, MAX_LEVERAGE, where)
        )

    return TradingConfig(
        risk_per_trade_percent=_bounded_decimal(
            risk, "risk_per_trade_percent", MAX_RISK_PER_TRADE_PERCENT, "[risk]"
        ),
        daily_loss_limit_percent=_bounded_decimal(
            risk, "daily_loss_limit_percent", MAX_DAILY_LOSS_LIMIT_PERCENT, "[risk]"
        ),
        max_notional_multiple=_bounded_decimal(
            risk, "max_notional_multiple", MAX_NOTIONAL_MULTIPLE, "[risk]"
        ),
        symbols=symbols,
        strategy=_parse_strategy(data.get("strategy")),
    )


def _parse_strategy(table: object) -> StrategyConfig | None:
    if table is None:
        return None
    where = "[strategy]"
    if not isinstance(table, Mapping):
        raise ConfigError(f"{where} must be a table")
    _reject_unknown(table, {"name", "interval", "params"}, where)
    name = table.get("name")
    if not isinstance(name, str):
        raise ConfigError(f"{where} name must be a string, got {name!r}")
    interval = table.get("interval")
    if not isinstance(interval, str) or interval not in INTERVAL_MS:
        raise ConfigError(
            f"{where} interval must be one of {', '.join(INTERVAL_MS)}, got {interval!r}"
        )
    params = table.get("params", {})
    if not isinstance(params, Mapping):
        raise ConfigError("[strategy.params] must be a table")
    config = StrategyConfig(name=name, interval=interval, params=dict(params))
    try:
        strategy = config.build()
    except StrategyError as e:
        raise ConfigError(f"{where} {e}") from None
    if strategy.warmup > MAX_STRATEGY_WARMUP:
        raise ConfigError(
            f"{where} {config.describe()} needs {strategy.warmup} warm-up candles; "
            f"the most is {MAX_STRATEGY_WARMUP}"
        )
    if strategy.limit_entries:
        raise ConfigError(
            f"{where} {config.describe()} enters with limit orders, which the engine can't "
            "place yet; leave it out of config.toml and run it with `quantdeck-engine "
            "backtest` only (D-45)"
        )
    return config


def _table(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = data.get(key)
    if not isinstance(value, Mapping):
        raise ConfigError(f"[{key}] table is missing")
    return value


def _reject_unknown(table: Mapping[str, Any], known: set[str], where: str) -> None:
    # A misspelled key would otherwise be ignored silently.
    unknown = sorted(set(table) - known)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {', '.join(unknown)}")


def _bounded_decimal(table: Mapping[str, Any], key: str, maximum: Decimal, where: str) -> Decimal:
    value = table.get(key)
    # bool is an int subclass: reject `true` explicitly.
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        raise ConfigError(f"{where} {key} must be a number, got {value!r}")
    number = Decimal(value)
    if not 0 < number <= maximum:
        raise ConfigError(f"{where} {key} must be > 0 and <= {maximum}, got {number}")
    return number


def _bounded_int(table: Mapping[str, Any], key: str, minimum: int, maximum: int, where: str) -> int:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where} {key} must be a whole number, got {value!r}")
    if not minimum <= value <= maximum:
        raise ConfigError(f"{where} {key} must be between {minimum} and {maximum}, got {value}")
    return value
