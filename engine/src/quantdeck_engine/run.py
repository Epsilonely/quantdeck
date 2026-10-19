"""`quantdeck-engine run`: the long-running engine (D-31).

Startup brings every configured symbol to a known state (D-16, D-19, D-26), and the risk
guard reads today's loss and any halt record (D-27). With a `[strategy]` in `config.toml`,
each symbol's strategy then loads its warm-up candles (D-43). The engine follows the user
data stream (D-13), runs the guard's check at the points D-30 lists, and runs each symbol's
strategy on its closed candles. Without a strategy it places no entries; either way it
protects what it finds and fires the kill switch when the daily loss limit is reached.
Clients (the GUI) connect through the WebSocket server in `server.py` (D-32, D-33).

Only one engine process runs at a time (D-31). Stop it with Ctrl+C or a client's
`stop_engine` command (D-34).
"""

import asyncio
import logging
from contextlib import aclosing
from decimal import Decimal
from pathlib import Path
from typing import Any

from quantdeck_engine.binance.candle_feed import ClosedCandleFeed
from quantdeck_engine.binance.rest import REQUEST_ERRORS, BinanceRestClient
from quantdeck_engine.binance.streams import mark_price_stream
from quantdeck_engine.binance.user_stream import AccountUpdate, UserEvent, UserStreamConnected
from quantdeck_engine.config import Settings, StrategyConfig, TradingConfig
from quantdeck_engine.lock import InstanceLock
from quantdeck_engine.paths import FORWARD_DIR
from quantdeck_engine.server import ClientServer, LogBuffer
from quantdeck_engine.snapshot import engine_snapshot
from quantdeck_engine.startup import build_account
from quantdeck_engine.trading.executor import SymbolTrader
from quantdeck_engine.trading.halt import HaltStore
from quantdeck_engine.trading.risk import RiskChecker, RiskGuard
from quantdeck_engine.trading.state import (
    AccountState,
    PositionClosed,
    PositionOpened,
    StateChange,
    follow_user_stream,
)
from quantdeck_engine.trading.strategy_runner import ForwardRecord, StrategyRunner

logger = logging.getLogger(__name__)

TIME_SYNC_INTERVAL = 60 * 60  # seconds; signed timestamps must stay within recvWindow


class RunFailed(Exception):
    """The engine could not start."""


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
        strategy: StrategyConfig | None = None,
        runners: list[StrategyRunner] | None = None,
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
        self.strategy = strategy  # None: no entries (D-43)
        self.runners = {r.symbol: r for r in runners or []}
        self.checker = checker or RiskChecker(guard)
        self.state = AccountState(list(self.traders))
        self.mark_prices: dict[str, Decimal] = {}  # for display only (D-33)
        self.stop_requested = asyncio.Event()

    async def run(self) -> None:
        """Follow the account and run the strategies until cancelled."""
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(
                follow_user_stream(
                    self._client, self._ws_root, self.state, self.on_change, self.on_event
                )
            )
            tasks.create_task(self.checker.run())
            tasks.create_task(self._follow_mark_prices())
            tasks.create_task(self._sync_time())
            for runner in self.runners.values():
                # Never raises: a strategy's failure stops only that strategy (D-43).
                tasks.create_task(runner.run())

    # Commands from clients (D-33)

    async def emergency_close(self) -> bool:
        """The kill switch, fired by the user (D-27)."""
        return await self.guard.trip("emergency close requested by the user (D-33)")

    async def lift_halt(self) -> bool:
        return await self.guard.lift()

    async def stop(self) -> None:
        """Stop the engine as Ctrl+C does (D-34), once any kill switch in progress is done.

        Exchange-side stops keep protecting open positions while it is stopped.
        """
        await self.guard.wait_idle()
        self.stop_requested.set()

    def snapshot(self) -> dict[str, Any]:
        """The state clients see (D-33)."""
        return engine_snapshot(self)

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


async def start_engine(
    client: BinanceRestClient,
    settings: Settings,
    trading: TradingConfig,
    store: HaltStore,
    *,
    forward_dir: Path = FORWARD_DIR,
) -> Engine:
    """Startup checks (D-05, D-16, D-19, D-26, D-27) and the strategies' warm-up (D-43);
    returns the engine ready to run."""
    account = await build_account(client, trading, store)
    for trader in account.traders:
        await trader.prepare()
    await account.guard.start()
    runners = []
    if trading.strategy is not None:
        interval = trading.strategy.interval
        for trader in account.traders:
            runner = StrategyRunner(
                trading.strategy.build(),
                trader,
                ClosedCandleFeed(client, settings.endpoints.ws, trader.symbol, interval),
                ForwardRecord(forward_dir / f"{trader.symbol}_{interval}.csv"),
            )
            await runner.warm_up()
            runners.append(runner)
    return Engine(
        client,
        settings.endpoints.ws,
        account.traders,
        account.guard,
        testnet=settings.testnet,
        asset=account.base.asset,
        strategy=trading.strategy,
        runners=runners,
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
                if engine.strategy is None:
                    strategy = "no [strategy] in config.toml, so no entries (D-43)"
                else:
                    strategy = f"strategy {engine.strategy.describe()}"
                if engine.guard.halted:
                    logger.warning(
                        "Engine running for %s, %s; halted: no new orders (D-27)", symbols, strategy
                    )
                else:
                    logger.info("Engine running for %s, %s", symbols, strategy)
                server = ClientServer(engine, logs)
                async with asyncio.TaskGroup() as tasks:
                    running = [
                        tasks.create_task(engine.run()),
                        tasks.create_task(_serve_clients(server)),
                    ]
                    await engine.stop_requested.wait()
                    # Let the stop command's result reach the client that sent it.
                    await server.flush()
                    for task in running:
                        task.cancel()
                logger.info("Engine stopped by a client (D-34)")
    finally:
        root.removeHandler(logs)


async def _serve_clients(server: ClientServer) -> None:
    try:
        await server.run()
    except Exception:
        # Clients only watch and send commands; losing them must not stop the engine.
        logger.exception("The client server stopped; the engine keeps running without clients")
