"""The strategies by name, and how to build one from settings.

The backtest command builds its strategy here, and `run` builds each symbol's from its
settings (D-47) the same way, so the strategy that was backtested is the one that trades,
with the same parameters (D-07, D-41).
"""

from collections.abc import Iterable, Mapping
from dataclasses import fields
from decimal import Decimal, InvalidOperation
from typing import Any

from quantdeck_engine.strategy.base import Strategy
from quantdeck_engine.strategy.bollinger import BollingerReversion
from quantdeck_engine.strategy.daily_breakout import DailyOpenBreakout
from quantdeck_engine.strategy.donchian import DonchianBreakout
from quantdeck_engine.strategy.move_fade import MoveFade
from quantdeck_engine.strategy.trend_pullback import TrendPullback

STRATEGIES: dict[str, type[Strategy]] = {
    "donchian": DonchianBreakout,
    "bollinger": BollingerReversion,
    "daily-breakout": DailyOpenBreakout,
    "move-fade": MoveFade,
    "trend-pullback": TrendPullback,
}


class StrategyError(ValueError):
    """No such strategy, or a parameter is unknown or has the wrong type or value."""


def parameter_types(name: str) -> dict[str, type]:
    """A strategy's parameters and their types, `interval` excluded."""
    cls = _class(name)
    return {f.name: f.type for f in fields(cls) if f.init and f.name != "interval"}


def parameter_descriptions(name: str) -> dict[str, str]:
    """A strategy's parameters and their help for clients (D-48), `interval` excluded; empty
    for a parameter declared without `parameter()`."""
    cls = _class(name)
    return {
        f.name: f.metadata.get("description", "")
        for f in fields(cls)
        if f.init and f.name != "interval"
    }


def default_strategy(name: str) -> Strategy:
    """Strategy `name` with every default, its interval included."""
    return _class(name)()


def make_strategy(name: str, interval: str, params: Mapping[str, object]) -> Strategy:
    """Build strategy `name` on `interval` with `params`, as `config.toml` would give them:
    ints, Decimals, and bools (an int is accepted for a Decimal parameter, never a bool)."""
    cls = _class(name)
    types = parameter_types(name)
    unknown = sorted(set(params) - set(types))
    if unknown:
        raise StrategyError(_no_parameter(name, ", ".join(unknown), types))
    kwargs: dict[str, object] = {"interval": interval}
    for key, value in params.items():
        kwargs[key] = _typed(name, key, value, types[key])
    try:
        return cls(**kwargs)
    except ValueError as e:
        # The strategy's own check of its parameters' values.
        raise StrategyError(f"{name}: {e}") from None


def parse_parameters(name: str, items: Iterable[str]) -> dict[str, int | Decimal | bool]:
    """`KEY=VALUE` strings from the command line, typed by the strategy's fields. A bool is
    `true` or `false`, as in TOML."""
    texts: dict[str, str] = {}
    for item in items:
        key, sep, text = item.partition("=")
        if not sep:
            raise StrategyError(_no_parameter(name, key, parameter_types(name)))
        texts[key] = text
    return parse_parameter_texts(name, texts)


def parse_parameter_texts(
    name: str, texts: Mapping[str, object]
) -> dict[str, int | Decimal | bool]:
    """Parameter values as text, typed by the strategy's fields: how `symbols.json` and
    client commands give them, like amounts (D-33, D-47). A bool is `true` or `false`."""
    types = parameter_types(name)
    params: dict[str, int | Decimal | bool] = {}
    for key, text in texts.items():
        if key not in types:
            raise StrategyError(_no_parameter(name, key, types))
        if not isinstance(text, str):
            raise StrategyError(f"{name}: {key} must be given as a string, got {text!r}")
        try:
            if types[key] is bool:
                params[key] = {"true": True, "false": False}[text]
            elif types[key] is int:
                params[key] = int(text)
            else:
                number = Decimal(text)
                if not number.is_finite():
                    raise ValueError(text)
                params[key] = number
        except (ValueError, InvalidOperation, KeyError):
            raise StrategyError(
                f"{name}: {key}={text!r} is not a valid {types[key].__name__}"
            ) from None
    return params


def parameters_of(strategy: Strategy) -> dict[str, Any]:
    """Every parameter of a built strategy, defaults included, in field order."""
    return {
        f.name: getattr(strategy, f.name)
        for f in fields(strategy)  # type: ignore[arg-type]
        if f.init and f.name != "interval"
    }


def parameter_text(value: object) -> str:
    """A parameter value as `parse_parameter_texts` reads it back: `true`/`false`, digits, or
    fixed-point decimals, never exponent notation."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _class(name: str) -> type[Strategy]:
    try:
        return STRATEGIES[name]
    except KeyError:
        raise StrategyError(
            f"unknown strategy {name!r}; the strategies are {', '.join(sorted(STRATEGIES))}"
        ) from None


def _no_parameter(name: str, key: str, types: Mapping[str, type]) -> str:
    return f"{name} has no parameter {key!r}; it takes {', '.join(sorted(types))}"


def _typed(name: str, key: str, value: object, expected: type) -> object:
    number = isinstance(value, int | Decimal) and not isinstance(value, bool)
    if expected is int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise StrategyError(f"{name}: {key} must be a whole number, got {value!r}")
        return value
    if expected is Decimal:
        if not number:
            raise StrategyError(f"{name}: {key} must be a number, got {value!r}")
        return Decimal(value)
    if not isinstance(value, expected):
        raise StrategyError(f"{name}: {key} must be a {expected.__name__}, got {value!r}")
    return value
