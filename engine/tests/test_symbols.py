import json
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fake_exchange import TRADING

from quantdeck_engine.config import ConfigError, SymbolConfig, TradingConfig, strategy_config
from quantdeck_engine.strategy.registry import STRATEGIES
from quantdeck_engine.symbols import (
    SymbolStore,
    catalog,
    load_symbols,
    parse_remove_symbol,
    parse_set_symbol,
)

D = Decimal
DONCHIAN = strategy_config("donchian", "4h", {"entry_period": 55, "exit_period": 20}, "test")
BTC = SymbolConfig("BTCUSDT", 2, DONCHIAN)
ETH = SymbolConfig("ETHUSDT", 3)


@pytest.fixture
def store(tmp_path: Path) -> SymbolStore:
    return SymbolStore(tmp_path / "state" / "symbols.json")


def write(store: SymbolStore, data: object) -> None:
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps(data), encoding="utf-8")


def entry(**overrides: Any) -> dict[str, Any]:
    return {
        "leverage": 2,
        "strategy": {
            "name": "donchian",
            "interval": "4h",
            "params": {"entry_period": "55", "exit_period": "20"},
        },
        **overrides,
    }


# The file (D-47)


def test_missing_file_loads_as_none(store: SymbolStore) -> None:
    assert store.load() is None


def test_saved_symbols_load_back(store: SymbolStore) -> None:
    store.save({"BTCUSDT": BTC, "ETHUSDT": ETH})
    assert store.load() == {"BTCUSDT": BTC, "ETHUSDT": ETH}
    # Parameters are strings, like amounts (D-33).
    assert json.loads(store.path.read_text(encoding="utf-8")) == {
        "version": 1,
        "symbols": {"BTCUSDT": entry(), "ETHUSDT": {"leverage": 3, "strategy": None}},
    }


def test_decimal_parameters_stay_exact(store: SymbolStore) -> None:
    bollinger = strategy_config("bollinger", "1h", {"width": Decimal("2.5")}, "test")
    store.save({"BTCUSDT": SymbolConfig("BTCUSDT", 2, bollinger)})
    text = store.path.read_text(encoding="utf-8")
    assert '"width": "2.5"' in text
    loaded = store.load()
    assert loaded is not None and loaded["BTCUSDT"].strategy == bollinger


def test_no_file_starts_from_config_toml_and_creates_it(
    store: SymbolStore, caplog: pytest.LogCaptureFixture
) -> None:
    trading = TradingConfig(D(1), D(3), D(2), initial_symbols={"BTCUSDT": BTC})
    with caplog.at_level(logging.INFO):
        assert load_symbols(store, trading) == {"BTCUSDT": BTC}
    assert store.load() == {"BTCUSDT": BTC}
    assert "Created" in caplog.text


def test_no_file_and_no_tables_creates_an_empty_file(store: SymbolStore) -> None:
    assert load_symbols(store, TRADING) == {}
    assert store.load() == {}


def test_read_only_load_never_creates_the_file(store: SymbolStore) -> None:
    trading = TradingConfig(D(1), D(3), D(2), initial_symbols={"BTCUSDT": BTC})
    assert load_symbols(store, trading, create=False) == {"BTCUSDT": BTC}
    assert not store.path.exists()


def test_the_file_wins_over_config_toml(
    store: SymbolStore, caplog: pytest.LogCaptureFixture
) -> None:
    store.save({"ETHUSDT": ETH})
    trading = TradingConfig(D(1), D(3), D(2), initial_symbols={"BTCUSDT": BTC})
    with caplog.at_level(logging.WARNING):
        assert load_symbols(store, trading) == {"ETHUSDT": ETH}
    assert "config.toml [symbols] and [strategy] are ignored" in caplog.text


def test_left_out_params_are_saved_filled_in(store: SymbolStore) -> None:
    write(
        store,
        {
            "version": 1,
            "symbols": {"BTCUSDT": entry(strategy={"name": "donchian", "interval": "4h"})},
        },
    )
    loaded = store.load()
    assert loaded is not None
    strategy = loaded["BTCUSDT"].strategy
    assert strategy is not None and strategy.params == {"entry_period": 20, "exit_period": 10}


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([], "must hold a JSON object"),
        ({"version": 2, "symbols": {}}, "version must be 1"),
        ({"version": 1, "symbols": {}, "extra": 1}, "unknown key"),
        ({"version": 1, "symbols": []}, "symbols must be an object"),
        ({"version": 1, "symbols": {"btcusdt": entry()}}, "upper case"),
        ({"version": 1, "symbols": {"BTC/USDT": entry()}}, "upper case"),
        ({"version": 1, "symbols": {"BTCUSDT": entry(leverage=0)}}, "leverage"),
        ({"version": 1, "symbols": {"BTCUSDT": entry(leverage=11)}}, "leverage"),
        ({"version": 1, "symbols": {"BTCUSDT": entry(leverage=2.0)}}, "leverage"),
        ({"version": 1, "symbols": {"BTCUSDT": entry(leverage=True)}}, "leverage"),
        ({"version": 1, "symbols": {"BTCUSDT": entry(levrage=2)}}, "unknown key"),
        (
            {"version": 1, "symbols": {"BTCUSDT": entry(strategy={"name": "ichimoku"})}},
            "unknown strategy",
        ),
        (
            {
                "version": 1,
                "symbols": {
                    "BTCUSDT": entry(
                        strategy={
                            "name": "donchian",
                            "interval": "4h",
                            "params": {"entry_period": 55},
                        }
                    )
                },
            },
            "must be given as a string",
        ),
        (
            {
                "version": 1,
                "symbols": {
                    "BTCUSDT": entry(strategy={"name": "trend-pullback", "interval": "1h"})
                },
            },
            "enters with limit orders",
        ),
        (
            {"version": 1, "symbols": {f"COIN{i}USDT": entry() for i in range(11)}},
            "the most is 10",
        ),
    ],
)
def test_invalid_file_stops_the_engine(store: SymbolStore, data: object, message: str) -> None:
    write(store, data)
    with pytest.raises(ConfigError, match=message):
        store.load()


def test_broken_json_stops_the_engine(store: SymbolStore) -> None:
    store.path.parent.mkdir(parents=True)
    store.path.write_text("{", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        store.load()


# Command arguments (D-47)


def test_set_symbol_arguments() -> None:
    config = parse_set_symbol({"symbol": "BTCUSDT", **entry()})
    assert config == BTC
    assert parse_set_symbol({"symbol": "ETHUSDT", "leverage": 3, "strategy": None}) == ETH


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"leverage": 2}, "symbol names"),
        ({"symbol": "BTCUSDT"}, "leverage"),
        ({"symbol": "BTCUSDT", "leverage": 20}, "leverage must be between 1 and 10"),
        ({"symbol": "BTCUSDT", "leverage": 2, "strategy": "donchian"}, "object or null"),
        ({"symbol": "BTCUSDT", "leverage": 2, "side": "long"}, "unknown key"),
        (
            {
                "symbol": "BTCUSDT",
                "leverage": 2,
                "strategy": {"name": "donchian", "interval": "4h", "params": {"entry_period": "x"}},
            },
            "not a valid int",
        ),
        (
            {
                "symbol": "BTCUSDT",
                "leverage": 2,
                "strategy": {"name": "donchian", "interval": "2d"},
            },
            "interval must be one of",
        ),
    ],
)
def test_invalid_set_symbol_arguments(args: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_set_symbol(args)


def test_remove_symbol_arguments() -> None:
    assert parse_remove_symbol({"symbol": "BTCUSDT"}) == "BTCUSDT"
    with pytest.raises(ConfigError, match="unknown key"):
        parse_remove_symbol({"symbol": "BTCUSDT", "close": True})
    with pytest.raises(ConfigError, match="symbol names"):
        parse_remove_symbol({})


# The catalog clients get with welcome (D-48)


def test_catalog_lists_the_bounds_and_every_strategy() -> None:
    data = catalog()
    assert data["max_leverage"] == 10
    assert data["max_symbols"] == 10
    assert "4h" in data["intervals"]
    assert [s["name"] for s in data["strategies"]] == list(STRATEGIES)
    donchian = data["strategies"][0]
    assert donchian["name"] == "donchian"
    assert donchian["interval"] == "1h"
    assert [{k: p[k] for k in ("name", "type", "default")} for p in donchian["params"]] == [
        {"name": "entry_period", "type": "int", "default": "20"},
        {"name": "exit_period", "type": "int", "default": "10"},
    ]
    assert set(donchian["params"][0]) == {"name", "type", "default", "description"}
    types = {
        (s["name"], p["name"]): (p["type"], p["default"])
        for s in data["strategies"]
        for p in s["params"]
    }
    assert types["bollinger", "width"] == ("decimal", "2")
    assert types["daily-breakout", "long_only"] == ("bool", "false")
    json.dumps(data)  # sent as it is


def test_catalog_describes_every_parameter() -> None:
    # The GUI shows it next to the parameter; a parameter added without parameter() has none.
    for strategy in catalog()["strategies"]:
        for p in strategy["params"]:
            assert p["description"].strip(), f"{strategy['name']}.{p['name']} has no description"


def test_catalog_defaults_are_accepted_by_set_symbol() -> None:
    for strategy in catalog()["strategies"]:
        args = {
            "symbol": "BTCUSDT",
            "leverage": 2,
            "strategy": {
                "name": strategy["name"],
                "interval": strategy["interval"],
                "params": {p["name"]: p["default"] for p in strategy["params"]},
            },
        }
        if strategy["name"] == "trend-pullback":
            # Its defaults enter with limit orders, which only the backtest fills (D-45).
            with pytest.raises(ConfigError, match="limit orders"):
                parse_set_symbol(args)
        else:
            config = parse_set_symbol(args)
            assert config.strategy is not None
            assert config.strategy.params_text() == args["strategy"]["params"]
