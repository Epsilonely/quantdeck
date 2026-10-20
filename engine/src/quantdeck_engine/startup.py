"""Bringing the account up, shared by `run` and `trial` (D-05, D-14, D-16, D-23, D-27).

Both connect, check the account, and build the same traders and guard. `run` then prepares
every configured symbol and starts the guard; `trial` does the same for its one symbol as
the first of its steps, so it exercises the real startup path.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from quantdeck_engine.binance.rest import BinanceRestClient
from quantdeck_engine.config import SymbolConfig, TradingConfig
from quantdeck_engine.trading.balance import BaseBalance
from quantdeck_engine.trading.executor import SymbolTrader
from quantdeck_engine.trading.halt import HaltStore
from quantdeck_engine.trading.order_ids import ClientIdFactory
from quantdeck_engine.trading.risk import RiskGuard
from quantdeck_engine.trading.rules import SymbolRules

# The daily loss limit covers one asset (D-23), so every symbol must be margined in it.
MARGIN_ASSET = "USDT"


class StartupFailed(Exception):
    """The account can't be traded as configured."""


@dataclass(frozen=True)
class Account:
    """The traders for the configured symbols and the guard that covers them all (D-24).
    The base balance and order IDs are shared with traders added later (D-47)."""

    rules: dict[str, SymbolRules]
    base: BaseBalance
    guard: RiskGuard
    ids: ClientIdFactory
    traders: list[SymbolTrader]


async def build_account(
    client: BinanceRestClient,
    trading: TradingConfig,
    store: HaltStore,
    symbols: Iterable[SymbolConfig],
) -> Account:
    """Check the account and build its traders and guard. Nothing is prepared or placed.

    Syncs the clock, requires one-way mode (D-05), reads each symbol's rules, and requires
    every symbol to be margined in USDT, the asset the daily loss limit covers (D-23).
    """
    await client.sync_time()
    if (await client.account_config())["dualSidePosition"]:
        raise StartupFailed("the account is in hedge mode; QuantDeck needs one-way mode (D-05)")
    configs = list(symbols)
    rules = {
        config.symbol: SymbolRules.from_exchange_info(await client.symbol_info(config.symbol))
        for config in configs
    }
    for r in rules.values():
        if r.margin_asset != MARGIN_ASSET:
            raise StartupFailed(
                f"{r.symbol} is margined in {r.margin_asset}; the daily loss limit covers "
                f"{MARGIN_ASSET} only (D-23)"
            )
    base = BaseBalance(client, MARGIN_ASSET)
    guard = RiskGuard(client, trading, base, store)
    ids = ClientIdFactory()
    traders = [
        SymbolTrader(client, config, trading, rules[config.symbol], base, guard, ids)
        for config in configs
    ]
    guard.attach(traders)
    return Account(rules=rules, base=base, guard=guard, ids=ids, traders=traders)
