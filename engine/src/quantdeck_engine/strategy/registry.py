"""The strategies by name, and how to build one from settings.

The backtest command builds its strategy here, and `run` will build its from
`engine/config.toml` (D-28) the same way, so the strategy that was backtested is the one
that trades, with the same parameters (D-07, D-41).
"""

from collections.abc import Iterable, Mapping
from dataclasses import fields
from decimal import Decimal, InvalidOperation

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
    types = parameter_types(name)
    params: dict[str, int | Decimal | bool] = {}
    for item in items:
        key, sep, text = item.partition("=")
        if not sep or key not in types:
            raise StrategyError(_no_parameter(name, key, types))
        try:
            if types[key] is bool:
                params[key] = {"true": True, "false": False}[text]
            else:
                params[key] = int(text) if types[key] is int else Decimal(text)
        except (ValueError, InvalidOperation, KeyError):
            raise StrategyError(
                f"{name}: {key}={text!r} is not a valid {types[key].__name__}"
            ) from None
    return params


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
