"""`quantdeck-engine run`: the long-running engine (D-31).

Startup brings every configured symbol to a known state (D-16, D-19, D-26), and the risk
guard reads today's loss and any halt record (D-27). The engine then follows the user data
stream (D-13) and runs the guard's check at the points D-30 lists. There is no strategy yet,
so it places no entries; it protects what it finds and fires the kill switch when the daily
loss limit is reached.

Only one engine process runs at a time (D-31). Stop it with Ctrl+C.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from quantdeck_engine.binance.rest import BinanceRestClient
from quantdeck_engine.binance.user_stream import AccountUpdate, UserEvent, UserStreamConnected
from quantdeck_engine.config import Settings, TradingConfig
from quantdeck_engine.lock import InstanceLock
from quantdeck_engine.trading.balance import BaseBalance
from quantdeck_engine.trading.executor import REQUEST_ERRORS, SymbolTrader
from quantdeck_engine.trading.halt import HaltStore
from quantdeck_engine.trading.order_ids import ClientIdFactory
from quantdeck_engine.trading.risk import DailyLoss, RiskGuard
from quantdeck_engine.trading.rules import SymbolRules
from quantdeck_engine.trading.state import (
    AccountState,
    PositionClosed,
    PositionOpened,
    StateChange,
    follow_user_stream,
)

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 60.0  # seconds; D-30: "about once a minute"
# Income records appeared 1.1 s after a fill on testnet (D-30), so a check run the moment a
# position closes would usually miss its loss.
CHECK_DELAY = 3.0  # seconds
TIME_SYNC_INTERVAL = 60 * 60  # seconds; signed timestamps must stay within recvWindow


class RunFailed(Exception):
    """The engine could not start."""


class RiskChecker:
    """Runs the guard's check (D-30): once at start, a few seconds after each request, and
    every minute regardless. Requests that arrive close together share one check."""

    def __init__(
        self,
        guard: RiskGuard,
        *,
        interval: float = CHECK_INTERVAL,
        delay: float = CHECK_DELAY,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._guard = guard
        self._interval = interval
        self._delay = delay
        self._sleep = sleep
        self._wake = asyncio.Event()
        self.latest: DailyLoss | None = None

    @property
    def requested(self) -> bool:
        return self._wake.is_set()

    def request(self) -> None:
        """Something may have changed today's loss: check soon."""
        self._wake.set()

    async def run(self) -> None:
        while True:
            self._wake.clear()
            try:
                self.latest = await self._guard.check()
            except REQUEST_ERRORS as e:
                logger.warning("Risk check failed (%s); trying again at the next check", e)
            try:
                async with asyncio.timeout(self._interval):
                    await self._wake.wait()
            except TimeoutError:
                continue
            await self._sleep(self._delay)


class Engine:
    def __init__(
        self,
        client: BinanceRestClient,
        ws_root: str,
        traders: list[SymbolTrader],
        guard: RiskGuard,
        *,
        checker: RiskChecker | None = None,
        time_sync_interval: float = TIME_SYNC_INTERVAL,
    ) -> None:
        self._client = client
        self._ws_root = ws_root
        self._time_sync_interval = time_sync_interval
        self.traders = {t.symbol: t for t in traders}
        self.guard = guard
        self.checker = checker or RiskChecker(guard)
        self.state = AccountState(list(self.traders))

    async def run(self) -> None:
        """Follow the account until cancelled."""
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(
                follow_user_stream(
                    self._client, self._ws_root, self.state, self.on_change, self.on_event
                )
            )
            tasks.create_task(self.checker.run())
            tasks.create_task(self._sync_time())

    def on_event(self, event: UserEvent) -> None:
        # D-30: events sent while disconnected are lost, and funding changes today's loss.
        if isinstance(event, UserStreamConnected) or (
            isinstance(event, AccountUpdate) and event.reason == "FUNDING_FEE"
        ):
            self.checker.request()

    async def on_change(self, change: StateChange) -> None:
        match change:
            case PositionClosed(symbol):
                logger.info("%s: position closed", symbol)
                self.checker.request()
                await self.traders[symbol].on_position_closed()
            case PositionOpened(symbol, amount):
                logger.info("%s: position opened: %s", symbol, amount)

    async def _sync_time(self) -> None:
        while True:
            await asyncio.sleep(self._time_sync_interval)
            try:
                offset = await self._client.sync_time()
            except REQUEST_ERRORS as e:
                logger.warning("Clock sync failed (%s); keeping the previous offset", e)
                continue
            logger.debug("Clock offset %+d ms", offset)


async def start_engine(
    client: BinanceRestClient, settings: Settings, trading: TradingConfig, store: HaltStore
) -> Engine:
    """Startup checks (D-05, D-16, D-19, D-26, D-27); returns the engine ready to run."""
    await client.sync_time()
    if (await client.account_config())["dualSidePosition"]:
        raise RunFailed("the account is in hedge mode; QuantDeck needs one-way mode (D-05)")
    info = await client.exchange_info()
    by_symbol = {s["symbol"]: s for s in info["symbols"]}
    rules: dict[str, SymbolRules] = {}
    for config in trading.symbols.values():
        if config.symbol not in by_symbol:
            raise RunFailed(f"{config.symbol} is not in exchangeInfo")
        rules[config.symbol] = SymbolRules.from_exchange_info(by_symbol[config.symbol])
    assets = sorted({r.margin_asset for r in rules.values()})
    if len(assets) != 1:
        raise RunFailed(
            f"the configured symbols use different margin assets ({', '.join(assets)}); "
            "the daily loss limit covers one (D-23)"
        )

    base = BaseBalance(client, assets[0])
    guard = RiskGuard(client, trading, base, store)
    ids = ClientIdFactory()
    traders = [
        SymbolTrader(client, config, trading, rules[config.symbol], base, guard, ids)
        for config in trading.symbols.values()
    ]
    guard.attach(traders)
    for trader in traders:
        await trader.prepare()
    await guard.start()
    return Engine(client, settings.endpoints.ws, traders, guard)


async def run_engine(settings: Settings, trading: TradingConfig) -> None:
    if not settings.testnet:
        # Mainnet waits for its prerequisites (activeContext.md); every feature runs on
        # testnet first.
        raise RunFailed("run is testnet-only until the mainnet prerequisites are met")
    with InstanceLock():
        async with BinanceRestClient(settings) as client:
            engine = await start_engine(client, settings, trading, HaltStore())
            symbols = ", ".join(engine.traders)
            if engine.guard.halted:
                logger.warning("Engine running for %s, halted: no new orders (D-27)", symbols)
            else:
                logger.info("Engine running for %s; no strategy yet, so no entries", symbols)
            await engine.run()
