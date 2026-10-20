"""The symbols the engine trades, each with its leverage and strategy (D-47).

They live in `engine/state/symbols.json`, which only the engine writes: clients change it
through the `set_symbol` and `remove_symbol` commands, checked here against the same bounds
as `config.toml` (D-28). Risk values stay in the hand-edited `config.toml`, so no client or
engine bug can rewrite them.

The first `run` or `trial` without the file creates it from `config.toml`'s `[symbols]` and
`[strategy]`; after that those tables are ignored. A file that can't be read or holds a
value out of bounds stops the engine at startup, as `config.toml` does.

Parameter values are strings in the file and in commands, like amounts (D-33), so a Decimal
parameter never passes through a float.
"""

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from quantdeck_engine.config import (
    MAX_SYMBOLS,
    ConfigError,
    StrategyConfig,
    SymbolConfig,
    TradingConfig,
    check_leverage,
    check_symbol_name,
    reject_unknown,
    strategy_config,
)
from quantdeck_engine.paths import SYMBOLS_FILE, write_atomic
from quantdeck_engine.strategy.registry import StrategyError, parse_parameter_texts

logger = logging.getLogger(__name__)

FILE_VERSION = 1


class SymbolChangeRefused(Exception):
    """A client's symbol change can't be made now; nothing was changed."""


class SymbolStore:
    def __init__(self, path: Path = SYMBOLS_FILE) -> None:
        self.path = path

    def load(self) -> dict[str, SymbolConfig] | None:
        """The symbols in the file, or None if there is no file. Raises `ConfigError` if it
        can't be read or a value is invalid."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as e:
            raise ConfigError(f"{self.path.name} can't be read: {e}") from None
        try:
            data = json.loads(text)
        except ValueError as e:
            raise ConfigError(f"{self.path.name} is not valid JSON: {e}") from None
        return parse_symbols_file(data, self.path.name)

    def save(self, symbols: Mapping[str, SymbolConfig]) -> None:
        """Write the whole file atomically (D-29). Raises OSError if it can't be written,
        which on Windows includes another program having it open (D-12)."""
        data = {
            "version": FILE_VERSION,
            "symbols": {symbol: symbol_entry(config) for symbol, config in symbols.items()},
        }
        write_atomic(self.path, json.dumps(data, indent=2) + "\n")


def load_symbols(
    store: SymbolStore, trading: TradingConfig, *, create: bool = True
) -> dict[str, SymbolConfig]:
    """The symbols to trade: the file's, or `config.toml`'s initial ones when there is no
    file yet, which `create` then writes to the file."""
    symbols = store.load()
    if symbols is not None:
        if trading.initial_symbols is not None:
            logger.warning(
                "config.toml [symbols] and [strategy] are ignored: the symbols are in %s now "
                "and change from the GUI (D-47). Delete those tables from config.toml.",
                store.path,
            )
        return symbols
    symbols = dict(trading.initial_symbols or {})
    if create:
        store.save(symbols)
        logger.info(
            "Created %s from config.toml [symbols] and [strategy] (D-47): %s",
            store.path,
            ", ".join(symbols) or "no symbols",
        )
    return symbols


def parse_symbols_file(data: object, where: str) -> dict[str, SymbolConfig]:
    if not isinstance(data, Mapping):
        raise ConfigError(f"{where} must hold a JSON object")
    reject_unknown(data, {"version", "symbols"}, where)
    if data.get("version") != FILE_VERSION:
        raise ConfigError(f"{where}: version must be {FILE_VERSION}, got {data.get('version')!r}")
    entries = data.get("symbols")
    if not isinstance(entries, Mapping):
        raise ConfigError(f"{where}: symbols must be an object")
    if len(entries) > MAX_SYMBOLS:
        raise ConfigError(f"{where} lists {len(entries)} symbols; the most is {MAX_SYMBOLS}")
    symbols = {}
    for symbol, entry in entries.items():
        config = parse_symbol(symbol, entry, f"{where}: {symbol}")
        symbols[config.symbol] = config
    return symbols


def parse_symbol(symbol: object, entry: object, where: str) -> SymbolConfig:
    """One symbol's settings: `{"leverage": 2, "strategy": null | {"name", "interval",
    "params": {name: text}}}`."""
    name = check_symbol_name(symbol, where)
    if not isinstance(entry, Mapping):
        raise ConfigError(f"{where} must be an object")
    reject_unknown(entry, {"leverage", "strategy"}, where)
    return SymbolConfig(
        symbol=name,
        leverage=check_leverage(entry.get("leverage"), where),
        strategy=_parse_strategy(entry.get("strategy"), where),
    )


def _parse_strategy(entry: object, where: str) -> StrategyConfig | None:
    if entry is None:
        return None
    where = f"{where} strategy"
    if not isinstance(entry, Mapping):
        raise ConfigError(f"{where} must be an object or null")
    reject_unknown(entry, {"name", "interval", "params"}, where)
    name, texts = entry.get("name"), entry.get("params", {})
    if not isinstance(texts, Mapping):
        raise ConfigError(f"{where} params must be an object")
    if isinstance(name, str):
        try:
            params = parse_parameter_texts(name, texts)
        except StrategyError as e:
            raise ConfigError(f"{where} {e}") from None
    else:
        params = {}  # strategy_config reports the name
    return strategy_config(name, entry.get("interval"), params, where)


def symbol_entry(config: SymbolConfig) -> dict[str, Any]:
    """The settings as the file and clients carry them (D-33)."""
    strategy = config.strategy
    return {
        "leverage": config.leverage,
        "strategy": None
        if strategy is None
        else {
            "name": strategy.name,
            "interval": strategy.interval,
            "params": strategy.params_text(),
        },
    }


# Client commands (D-33, D-47)


def parse_set_symbol(args: Mapping[str, Any]) -> SymbolConfig:
    """`set_symbol`'s arguments: `{"symbol", "leverage", "strategy"}`."""
    entry = {key: value for key, value in args.items() if key != "symbol"}
    return parse_symbol(args.get("symbol"), entry, "set_symbol")


def parse_remove_symbol(args: Mapping[str, Any]) -> str:
    """`remove_symbol`'s arguments: `{"symbol"}`."""
    reject_unknown(args, {"symbol"}, "remove_symbol")
    return check_symbol_name(args.get("symbol"), "remove_symbol")
