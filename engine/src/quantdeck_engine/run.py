"""`quantdeck-engine run`: the long-running engine (D-31).

Startup brings every configured symbol to a known state (D-16, D-19, D-26), and the risk
guard reads today's loss and any halt record (D-27). The engine then follows the user data
stream (D-13) and runs the guard's check at the points D-30 lists. There is no strategy yet,
so it places no entries; it protects what it finds and fires the kill switch when the daily
loss limit is reached. Clients (the GUI) connect through the WebSocket server in `server.py`
(D-32, D-33).

Only one engine process runs at a time (D-31). Stop it with Ctrl+C.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import aclosing
from decimal import Decimal
from typing import Any

from quantdeck_engine.binance.rest import BinanceRestClient
from quantdeck_engine.binance.streams import mark_price_stream
from quantdeck_engine.binance.user_stream import AccountUpdate, UserEvent, UserStreamConnected
from quantdeck_engine.config import Settings, TradingConfig
from quantdeck_engine.lock import InstanceLock
from quantdeck_engine.server import ClientServer, LogBuffer
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
        testnet: bool,
        asset: str,
        checker: RiskChecker | None = None,
        time_sync_interval: float = TIME_SYNC_INTERVAL,
    ) -> None:
        self._client = client
        self._ws_root = ws_root
        self._time_sync_interval = time_sync_interval
        self.testnet = testnet
        self.asset = asset  # the margin asset the daily loss limit covers (D-23)
        self.traders = {t.symbol: t for t in traders}
        self.guard = guard
        self.checker = checker or RiskChecker(guard)
        self.state = AccountState(list(self.traders))
        self.mark_prices: dict[str, Decimal] = {}  # for display only (D-33)

    async def run(self) -> None:
        """Follow the account until cancelled."""
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(
                follow_user_stream(
                    self._client, self._ws_root, self.state, self.on_change, self.on_event
                )
            )
            tasks.create_task(self.checker.run())
            tasks.create_task(self._follow_mark_prices())
            tasks.create_task(self._sync_time())

    # Commands from clients (D-33)

    async def emergency_close(self) -> bool:
        """The kill switch, fired by the user (D-27)."""
        return await self.guard.trip("emergency close requested by the user (D-33)")

    async def lift_halt(self) -> bool:
        return await self.guard.lift()

    # The state clients see (D-33)

    def snapshot(self) -> dict[str, Any]:
        daily = self.checker.latest
        return {
            "network": "testnet" if self.testnet else "mainnet",
            "halt": {
                "halted": self.guard.halted,
                "reason": self.guard.halt_reason,
                "since": self.guard.halted_at,
            },
            "daily_loss": None
            if daily is None
            else {
                "asset": self.asset,
                "base": _text(daily.base),
                "loss": _text(daily.loss),
                "limit": _text(daily.limit),
            },
            "balances": {
                asset: _text(amount)
                for asset, amount in sorted(self.state.wallet_balances.items())
                if amount != 0
            },
            "symbols": [self._symbol_snapshot(symbol) for symbol in self.traders],
        }

    def _symbol_snapshot(self, symbol: str) -> dict[str, Any]:
        state = self.state.symbols[symbol]
        mark = self.mark_prices.get(symbol)
        position = None
        if state.position_amount != 0:
            position = {
                "amount": _text(state.position_amount),
                "entry_price": _text(state.entry_price),
                "unrealized_pnl": None
                if mark is None
                else _text(state.position_amount * (mark - state.entry_price)),
            }
        return {
            "symbol": symbol,
            "halted": self.traders[symbol].halt_reason,
            "mark_price": None if mark is None else _text(mark),
            "position": position,
            "orders": [
                {
                    "client_id": o.client_id,
                    "side": o.side,
                    "type": o.order_type,
                    "quantity": _text(o.quantity),
                    "reduce_only": o.reduce_only,
                }
                for o in sorted(state.orders.values(), key=lambda o: o.client_id)
            ],
            "stops": [
                {
                    "client_algo_id": a.client_algo_id,
                    "side": a.side,
                    "type": a.order_type,
                    "trigger_price": _text(a.trigger_price),
                    "close_position": a.close_position,
                }
                for a in sorted(state.algo_orders.values(), key=lambda a: a.client_algo_id)
            ],
        }

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

    async def _follow_mark_prices(self) -> None:
        async with aclosing(mark_price_stream(self._ws_root, self.traders)) as stream:
            async for update in stream:
                self.mark_prices[update.symbol] = update.price

    async def _sync_time(self) -> None:
        while True:
            await asyncio.sleep(self._time_sync_interval)
            try:
                offset = await self._client.sync_time()
            except REQUEST_ERRORS as e:
                logger.warning("Clock sync failed (%s); keeping the previous offset", e)
                continue
            logger.debug("Clock offset %+d ms", offset)


def _text(amount: Decimal) -> str:
    """Fixed-point text, never exponent notation such as `0E-8` (D-33)."""
    return format(amount, "f")


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
    return Engine(
        client,
        settings.endpoints.ws,
        traders,
        guard,
        testnet=settings.testnet,
        asset=base.asset,
    )


async def run_engine(settings: Settings, trading: TradingConfig) -> None:
    if not settings.testnet:
        # Mainnet waits for its prerequisites (activeContext.md); every feature runs on
        # testnet first.
        raise RunFailed("run is testnet-only until the mainnet prerequisites are met")
    # Attached first, so a client connecting later sees the startup log too.
    logs = LogBuffer()
    root = logging.getLogger()
    root.addHandler(logs)
    try:
        with InstanceLock():
            async with BinanceRestClient(settings) as client:
                engine = await start_engine(client, settings, trading, HaltStore())
                symbols = ", ".join(engine.traders)
                if engine.guard.halted:
                    logger.warning("Engine running for %s, halted: no new orders (D-27)", symbols)
                else:
                    logger.info("Engine running for %s; no strategy yet, so no entries", symbols)
                async with asyncio.TaskGroup() as tasks:
                    tasks.create_task(engine.run())
                    tasks.create_task(_serve_clients(ClientServer(engine, logs)))
    finally:
        root.removeHandler(logs)


async def _serve_clients(server: ClientServer) -> None:
    try:
        await server.run()
    except Exception:
        # Clients only watch and send commands; losing them must not stop the engine.
        logger.exception("The client server stopped; the engine keeps running without clients")
