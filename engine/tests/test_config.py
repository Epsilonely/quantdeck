import logging
from decimal import Decimal
from pathlib import Path

import pytest

from quantdeck_engine.config import (
    MAINNET,
    TESTNET,
    ConfigError,
    load_settings,
    load_trading_config,
    parse_settings,
)
from quantdeck_engine.paths import CONFIG_FILE, ENV_FILE
from quantdeck_engine.strategy.registry import STRATEGIES

KEYS = {"BINANCE_API_KEY": "test-key-123", "BINANCE_API_SECRET": "test-secret-456"}


@pytest.mark.parametrize("raw", [None, "", "  ", "true", " true "])
def test_testnet_when_unset_or_true(raw: str | None) -> None:
    values = dict(KEYS) if raw is None else {**KEYS, "BINANCE_TESTNET": raw}
    settings = parse_settings(values)
    assert settings.testnet is True
    assert settings.endpoints == TESTNET


def test_mainnet_only_on_explicit_false(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        settings = parse_settings({**KEYS, "BINANCE_TESTNET": "false"})
    assert settings.testnet is False
    assert settings.endpoints == MAINNET
    assert "MAINNET" in caplog.text


@pytest.mark.parametrize(
    "raw", ["True", "TRUE", "False", "FALSE", "0", "1", "yes", "no", "flase", "mainnet"]
)
def test_unrecognized_testnet_value_is_rejected(raw: str) -> None:
    with pytest.raises(ConfigError, match="BINANCE_TESTNET"):
        parse_settings({**KEYS, "BINANCE_TESTNET": raw})


@pytest.mark.parametrize("name", ["BINANCE_API_KEY", "BINANCE_API_SECRET"])
def test_missing_key_is_rejected(name: str) -> None:
    values = {k: v for k, v in KEYS.items() if k != name}
    with pytest.raises(ConfigError, match=name):
        parse_settings(values)


@pytest.mark.parametrize("name", ["BINANCE_API_KEY", "BINANCE_API_SECRET"])
def test_blank_key_is_rejected(name: str) -> None:
    with pytest.raises(ConfigError, match=name):
        parse_settings({**KEYS, name: "   "})


def test_error_message_does_not_leak_the_other_key() -> None:
    with pytest.raises(ConfigError) as exc_info:
        parse_settings({"BINANCE_API_KEY": "test-key-123"})
    assert "test-key-123" not in str(exc_info.value)


def test_secrets_are_masked_everywhere() -> None:
    settings = parse_settings(KEYS)
    rendered = [
        repr(settings),
        str(settings),
        f"{settings.api_key}",
        f"{settings.api_secret!r}",
    ]
    for text in rendered:
        assert "test-key-123" not in text
        assert "test-secret-456" not in text


def test_reveal_returns_the_real_value() -> None:
    settings = parse_settings(KEYS)
    assert settings.api_key.reveal() == "test-key-123"
    assert settings.api_secret.reveal() == "test-secret-456"


def test_log_level_defaults_to_info() -> None:
    assert parse_settings(KEYS).log_level == logging.INFO
    assert parse_settings({**KEYS, "ENGINE_LOG_LEVEL": "  "}).log_level == logging.INFO


@pytest.mark.parametrize(
    ("raw", "level"),
    [("DEBUG", logging.DEBUG), ("debug", logging.DEBUG), (" warning ", logging.WARNING)],
)
def test_log_level_is_case_insensitive(raw: str, level: int) -> None:
    assert parse_settings({**KEYS, "ENGINE_LOG_LEVEL": raw}).log_level == level


@pytest.mark.parametrize("raw", ["VERBOSE", "TRACE", "10"])
def test_unknown_log_level_is_rejected(raw: str) -> None:
    with pytest.raises(ConfigError, match="ENGINE_LOG_LEVEL"):
        parse_settings({**KEYS, "ENGINE_LOG_LEVEL": raw})


def test_load_reads_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("BINANCE_API_KEY=file-key\nBINANCE_API_SECRET=file-secret\n")
    settings = load_settings(env_file, environ={})
    assert settings.api_key.reveal() == "file-key"
    assert settings.testnet is True


def test_environment_overrides_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "BINANCE_API_KEY=file-key\nBINANCE_API_SECRET=file-secret\nBINANCE_TESTNET=false\n"
    )
    settings = load_settings(env_file, environ={"BINANCE_TESTNET": "true"})
    assert settings.testnet is True


def test_missing_env_file_falls_back_to_environment(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / "missing.env", environ=KEYS)
    assert settings.api_key.reveal() == "test-key-123"


def test_default_env_file_is_at_repo_root() -> None:
    assert (ENV_FILE.parent / ".env.example").is_file()


# engine/config.toml (D-28)

EXAMPLE_CONFIG = Path(__file__).resolve().parents[1] / "config.example.toml"

VALID_TOML = """
[risk]
risk_per_trade_percent = 1
daily_loss_limit_percent = 3.5
max_notional_multiple = 2

[symbols.BTCUSDT]
leverage = 5
"""


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_example_config_is_valid() -> None:
    config = load_trading_config(EXAMPLE_CONFIG)
    assert config.risk_per_trade_percent == Decimal("1")
    assert config.daily_loss_limit_percent == Decimal("3")
    assert config.max_notional_multiple == Decimal("2")
    assert config.symbols["BTCUSDT"].leverage == 2
    assert config.strategy is not None
    assert config.strategy.describe() == "donchian 4h (entry_period=55, exit_period=20)"


def test_decimals_stay_exact(tmp_path: Path) -> None:
    config = load_trading_config(write_config(tmp_path, VALID_TOML))
    assert config.daily_loss_limit_percent == Decimal("3.5")
    assert isinstance(config.daily_loss_limit_percent, Decimal)


def test_missing_config_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="config.example.toml"):
        load_trading_config(tmp_path / "config.toml")


def test_invalid_toml_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="config.toml"):
        load_trading_config(write_config(tmp_path, "[risk\n"))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("risk_per_trade_percent", "100"),  # typo for 1.00
        ("risk_per_trade_percent", "2.01"),
        ("risk_per_trade_percent", "0"),
        ("risk_per_trade_percent", "-1"),
        ("daily_loss_limit_percent", "10.5"),
        ("max_notional_multiple", "6"),
    ],
)
def test_risk_values_outside_bounds_are_rejected(tmp_path: Path, key: str, value: str) -> None:
    text = VALID_TOML.replace(f"{key} = ", f"{key} = {value} #")
    with pytest.raises(ConfigError, match=key):
        load_trading_config(write_config(tmp_path, text))


@pytest.mark.parametrize("value", ['"1"', "true"])
def test_risk_value_must_be_a_number(tmp_path: Path, value: str) -> None:
    text = VALID_TOML.replace("risk_per_trade_percent = ", f"risk_per_trade_percent = {value} #")
    with pytest.raises(ConfigError, match="must be a number"):
        load_trading_config(write_config(tmp_path, text))


@pytest.mark.parametrize("value", ["0", "11", "5.5", "true"])
def test_leverage_outside_bounds_is_rejected(tmp_path: Path, value: str) -> None:
    text = VALID_TOML.replace("leverage = 5", f"leverage = {value}")
    with pytest.raises(ConfigError, match="leverage"):
        load_trading_config(write_config(tmp_path, text))


def test_missing_risk_value_is_rejected(tmp_path: Path) -> None:
    text = VALID_TOML.replace("max_notional_multiple = 2\n", "")
    with pytest.raises(ConfigError, match="max_notional_multiple"):
        load_trading_config(write_config(tmp_path, text))


@pytest.mark.parametrize(
    ("old", "new", "where"),
    [
        ("risk_per_trade_percent", "risk_per_trade_pct", r"\[risk\]"),
        ("leverage = 5", "leverage = 5\nlevrage = 3", r"\[symbols.BTCUSDT\]"),
        ("[risk]", "[risks]\n[risk]", "top level"),
    ],
)
def test_unknown_keys_are_rejected(tmp_path: Path, old: str, new: str, where: str) -> None:
    with pytest.raises(ConfigError, match=where):
        load_trading_config(write_config(tmp_path, VALID_TOML.replace(old, new)))


def test_at_least_one_symbol_is_required(tmp_path: Path) -> None:
    text = VALID_TOML.replace("[symbols.BTCUSDT]\nleverage = 5\n", "[symbols]\n")
    with pytest.raises(ConfigError, match="at least one symbol"):
        load_trading_config(write_config(tmp_path, text))


def test_lower_case_symbol_is_rejected(tmp_path: Path) -> None:
    text = VALID_TOML.replace("BTCUSDT", "btcusdt")
    with pytest.raises(ConfigError, match="upper case"):
        load_trading_config(write_config(tmp_path, text))


def test_default_config_file_is_in_engine_dir() -> None:
    assert CONFIG_FILE.parent == EXAMPLE_CONFIG.parent


# [strategy] (D-43)

STRATEGY_TOML = """
[strategy]
name = "donchian"
interval = "4h"

[strategy.params]
entry_period = 55
exit_period = 20
"""


def test_no_strategy_table_means_no_strategy(tmp_path: Path) -> None:
    assert load_trading_config(write_config(tmp_path, VALID_TOML)).strategy is None


def test_strategy_table_builds_a_new_instance_each_time(tmp_path: Path) -> None:
    config = load_trading_config(write_config(tmp_path, VALID_TOML + STRATEGY_TOML))
    assert config.strategy is not None
    first, second = config.strategy.build(), config.strategy.build()
    assert first is not second
    assert (first.interval, first.warmup) == ("4h", 56)


def test_strategy_params_may_be_left_out(tmp_path: Path) -> None:
    text = VALID_TOML + STRATEGY_TOML.split("[strategy.params]")[0]
    config = load_trading_config(write_config(tmp_path, text))
    assert config.strategy is not None
    assert config.strategy.describe() == "donchian 4h"


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('name = "donchian"', 'name = "ichimoku"', "unknown strategy"),
        ('name = "donchian"', "name = 1", "name must be a string"),
        ('interval = "4h"', 'interval = "4H"', "interval must be one of"),
        ('interval = "4h"', 'interval = "1M"', "interval must be one of"),
        ('interval = "4h"\n', "", "interval must be one of"),
        ("entry_period = 55", "entry_periods = 55", "has no parameter"),
        ("entry_period = 55", "entry_period = 55.0", "must be a whole number"),
        ("entry_period = 55", "entry_period = 10", "exit_period <= entry_period"),
        ("entry_period = 55", "entry_period = 1000", "needs 1001 warm-up candles"),
        ('interval = "4h"', 'interval = "4h"\nperiod = 3', r"\[strategy\]: unknown key"),
    ],
)
def test_invalid_strategy_is_rejected(tmp_path: Path, old: str, new: str, message: str) -> None:
    text = VALID_TOML + STRATEGY_TOML.replace(old, new)
    with pytest.raises(ConfigError, match=message):
        load_trading_config(write_config(tmp_path, text))


@pytest.mark.parametrize(
    ("name", "params"),
    [("trend-pullback", ""), ("bollinger", "limit_offset = 0.5\n")],
)
def test_a_strategy_with_limit_entries_is_refused(tmp_path: Path, name: str, params: str) -> None:
    text = VALID_TOML + f'[strategy]\nname = "{name}"\ninterval = "1h"\n[strategy.params]\n'
    with pytest.raises(ConfigError, match="enters with limit orders, which the engine can't"):
        load_trading_config(write_config(tmp_path, text + params))


def test_the_market_entry_settings_of_limit_strategies_load(tmp_path: Path) -> None:
    text = VALID_TOML + '[strategy]\nname = "trend-pullback"\ninterval = "1h"\n'
    text += "[strategy.params]\nlimit_atr = 0\n"
    assert load_trading_config(write_config(tmp_path, text)).strategy is not None


def test_only_limit_settings_use_limit_entries() -> None:
    # The forward test's strategy, and every market-entry default, still loads in `run`
    # (D-43, D-45).
    uses = {name: cls().limit_entries for name, cls in STRATEGIES.items()}
    assert uses == {
        "donchian": False,
        "bollinger": False,
        "daily-breakout": False,
        "move-fade": False,
        "trend-pullback": True,
    }
