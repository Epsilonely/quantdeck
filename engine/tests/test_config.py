import logging
from pathlib import Path

import pytest

from quantdeck_engine.config import (
    DEFAULT_ENV_FILE,
    MAINNET,
    TESTNET,
    ConfigError,
    load_settings,
    parse_settings,
)

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
    assert (DEFAULT_ENV_FILE.parent / ".env.example").is_file()
