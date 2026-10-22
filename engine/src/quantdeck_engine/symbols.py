"""The symbols the engine trades, each with its leverage and strategy (D-47).

They live in `engine/state/symbols.json`, which only the engine writes: clients change it
through the `set_symbol` and `remove_symbol` commands, checked here against the same bounds
as `config.toml` (D-28), and learn what they can set from the catalog sent with `welcome`
(D-48). Risk values stay in the hand-edited `config.toml`, so no client or
engine bug can rewrite them.

The file also keeps the user's pause of a symbol (D-50) as `paused_at` in its entry, so a
restart doesn't trade a symbol the user stopped. A pause isn't a setting: `set_symbol`
neither takes nor clears it, and removing the symbol forgets it.

The first `run` or `trial` without the file creates it from `config.toml`'s `[symbols]` and
`[strategy]`; after that those tables are ignored. A file that can't be read or holds a
value out of bounds stops the engine at startup, as `config.toml` does.

Parameter values are strings in the file and in commands, like amounts (D-33), so a Decimal
parameter never passes through a float.
"""

import json
import logging
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from quantdeck_engine.binance.streams import INTERVAL_MS
from quantdeck_engine.config import (
    MAX_LEVERAGE,
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
from quantdeck_engine.strategy.registry import (
    STRATEGIES,
    StrategyError,
    default_strategy,
    parameter_descriptions,
    parameter_text,
    parameter_types,
    parameters_of,
    parse_parameter_texts,
)

logger = logging.getLogger(__name__)

FILE_VERSION = 1

# How the catalog names a parameter's type (D-48).
_TYPE_NAMES: dict[type, str] = {int: "int", Decimal: "decimal", bool: "bool"}


class SymbolChangeRefused(Exception):
    """A client's symbol change can't be made now; nothing was changed."""


class PauseNotSaved(Exception):
    """`close_symbol` closed the position and the pause holds in the running engine, but the
    file couldn't be written, so a restart would trade the symbol again (D-50)."""


class SymbolStore:
    def __init__(self, path: Path = SYMBOLS_FILE) -> None:
        self.path = path

    def load(self) -> dict[str, SymbolConfig] | None:
        """The symbols in the file, or None if there is no file. Raises `ConfigError` if it
        can't be read or a value is invalid."""
        data = self._read()
        return None if data is None else parse_symbols_file(data, self.path.name)

    def load_paused(self) -> dict[str, str]:
        """The symbols the user paused, each with when (ISO 8601 UTC); empty without a file.
        Raises `ConfigError` as `load` does."""
        data = self._read()
        return {} if data is None else parse_paused(data, self.path.name)

    def save(
        self, symbols: Mapping[str, SymbolConfig], paused: Mapping[str, str] | None = None
    ) -> None:
        """Write the whole file atomically (D-29): every symbol's settings, and `paused_at`
        for those in `paused`. Raises OSError if it can't be written, which on Windows
        includes another program having it open (D-12)."""
        paused = paused or {}
        entries = {}
        for symbol, config in symbols.items():
            entries[symbol] = symbol_entry(config)
            if symbol in paused:
                entries[symbol]["paused_at"] = paused[symbol]
        data = {"version": FILE_VERSION, "symbols": entries}
        write_atomic(self.path, json.dumps(data, indent=2) + "\n")

    def _read(self) -> Mapping[str, Any] | None:
        """The file's JSON object, or None if there is no file."""
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
        if not isinstance(data, Mapping):
            raise ConfigError(f"{self.path.name} must hold a JSON object")
        return data


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
        if isinstance(entry, Mapping) and "paused_at" in entry:
            _check_paused_at(entry["paused_at"], f"{where}: {symbol}")
            entry = {key: value for key, value in entry.items() if key != "paused_at"}
        config = parse_symbol(symbol, entry, f"{where}: {symbol}")
        symbols[config.symbol] = config
    return symbols


def parse_paused(data: Mapping[str, Any], where: str) -> dict[str, str]:
    """The symbols the user paused (D-50): `paused_at` in a symbol's entry."""
    parse_symbols_file(data, where)  # checks the whole file first
    return {
        symbol: entry["paused_at"]
        for symbol, entry in data["symbols"].items()
        if "paused_at" in entry
    }


def _check_paused_at(value: object, where: str) -> None:
    try:
        valid = isinstance(value, str) and datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        valid = False
    if not valid:
        raise ConfigError(f"{where} paused_at must be an ISO 8601 time with a zone, got {value!r}")


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


def parse_symbol_arg(args: Mapping[str, Any], command: str) -> str:
    """The arguments of `remove_symbol` (D-47) and of `pause_symbol`, `close_symbol`, and
    `resume_symbol` (D-50): `{"symbol"}`."""
    reject_unknown(args, {"symbol"}, command)
    return check_symbol_name(args.get("symbol"), command)


def catalog() -> dict[str, Any]:
    """What `set_symbol` accepts, for a client's settings screen (D-48): the bounds, the
    intervals, and every registered strategy with its default interval and parameters, each
    with the help the GUI shows next to it.

    Built from the strategy registry, so no client keeps its own copy. Defaults are text,
    as parameters are everywhere else. Whether a setting enters with limit orders depends
    on its values, so it isn't listed: `set_symbol` refuses those (D-45).
    """
    return {
        "max_leverage": MAX_LEVERAGE,
        "max_symbols": MAX_SYMBOLS,
        "intervals": list(INTERVAL_MS),
        "strategies": [_catalog_entry(name) for name in STRATEGIES],
    }


def _catalog_entry(name: str) -> dict[str, Any]:
    strategy = default_strategy(name)
    types = parameter_types(name)
    descriptions = parameter_descriptions(name)
    return {
        "name": name,
        "interval": strategy.interval,
        "params": [
            {
                "name": key,
                "type": _TYPE_NAMES[types[key]],
                "default": parameter_text(value),
                "description": descriptions[key],
            }
            for key, value in parameters_of(strategy).items()
        ],
    }
