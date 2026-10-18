"""Bringing the account up, shared by `run` and `trial` (D-05, D-14, D-16, D-23, D-27).

Both connect, check the account, and build the same traders and guard. `run` then prepares
every configured symbol and starts the guard; `trial` does the same for its one symbol as
the first of its steps, so it exercises the real startup path.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from quantdeck_engine.binance.rest import BinanceRestClient
from quantdeck_engine.config import TradingConfig
from quantdeck_engine.trading.balance import BaseBalance
from quantdeck_engine.trading.executor import SymbolTrader
from quantdeck_engine.trading.halt import HaltStore
from quantdeck_engine.trading.order_ids import ClientIdFactory
from quantdeck_engine.trading.risk import RiskGuard
from quantdeck_engine.trading.rules import SymbolRules


class StartupFailed(Exception):
    """The account can't be traded as configured."""


@dataclass(frozen=True)
class Account:
    """The traders for the configured symbols and the guard that covers them all (D-24)."""

    rules: dict[str, SymbolRules]
    base: BaseBalance
    guard: RiskGuard
    traders: list[SymbolTrader]


async def build_account(
    client: BinanceRestClient,
    trading: TradingConfig,
    store: HaltStore,
    *,
    symbols: Iterable[str] | None = None,
) -> Account:
    """Check the account and build its traders and guard. Nothing is prepared or placed.

    Syncs the clock, requires one-way mode (D-05), reads each symbol's rules, and requires
    one margin asset across the symbols, since the daily loss limit covers one (D-23).
    `symbols` defaults to every symbol in `config.toml`; each must be configured there.
    """
    await client.sync_time()
    if (await client.account_config())["dualSidePosition"]:
        raise StartupFailed("the account is in hedge mode; QuantDeck needs one-way mode (D-05)")
    names = list(trading.symbols) if symbols is None else list(symbols)
    configs = [trading.symbols[name] for name in names]
    rules = {
        config.symbol: SymbolRules.from_exchange_info(await client.symbol_info(config.symbol))
        for config in configs
    }
    assets = sorted({r.margin_asset for r in rules.values()})
    if len(assets) != 1:
        raise StartupFailed(
            f"the configured symbols use different margin assets ({', '.join(assets)}); "
            "the daily loss limit covers one (D-23)"
        )
    base = BaseBalance(client, assets[0])
    guard = RiskGuard(client, trading, base, store)
    ids = ClientIdFactory()
    traders = [
        SymbolTrader(client, config, trading, rules[config.symbol], base, guard, ids)
        for config in configs
    ]
    guard.attach(traders)
    return Account(rules=rules, base=base, guard=guard, traders=traders)
