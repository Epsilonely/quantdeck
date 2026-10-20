"""`quantdeck-engine run`: the long-running engine (D-31).

Startup brings every symbol in `symbols.json` to a known state (D-16, D-19, D-26, D-47),
and the risk guard reads today's loss and any halt record (D-27). Each symbol with a
strategy then loads its warm-up candles (D-43). The engine follows the user data stream
(D-13), runs the guard's check at the points D-30 lists, and runs each symbol's strategy on
its closed candles. A symbol without a strategy places no entries; every symbol is
protected, and the kill switch fires when the daily loss limit is reached.

Clients (the GUI) connect through the WebSocket server in `server.py` (D-32, D-33). Besides
the kill switch and stopping the engine, they add, change, and remove symbols while it
runs (D-47): one symbol at a time, only while that symbol is flat, saved to `symbols.json`
before the engine acts on it, and without touching the other symbols.

Only one engine process runs at a time (D-31). Stop it with Ctrl+C or a client's
`stop_engine` command (D-34).
"""

import asyncio
import logging
import time
from collections.abc import Mapping
from contextlib import aclosing
from decimal import Decimal
from pathlib import Path
from typing import Any

from quantdeck_engine.binance.candle_feed import ClosedCandleFeed
from quantdeck_engine.binance.rest import REQUEST_ERRORS, BinanceRestClient, UnknownSymbol
from quantdeck_engine.binance.streams import mark_price_stream
from quantdeck_engine.binance.user_stream import AccountUpdate, UserEvent, UserStreamConnected
from quantdeck_engine.config import (
    MAX_SYMBOLS,
    Settings,
    StrategyConfig,
    SymbolConfig,
    TradingConfig,
)
from quantdeck_engine.lock import InstanceLock
from quantdeck_engine.paths import FORWARD_DIR
from quantdeck_engine.server import ClientServer, LogBuffer
from quantdeck_engine.snapshot import engine_snapshot
from quantdeck_engine.startup import Account, build_account
from quantdeck_engine.symbols import (
    SymbolChangeRefused,
    SymbolStore,
    load_symbols,
    parse_remove_symbol,
    parse_set_symbol,
)
from quantdeck_engine.trading.executor import PositionOpen, SymbolTrader
from quantdeck_engine.trading.halt import HaltStore
from quantdeck_engine.trading.risk import RiskChecker
from quantdeck_engine.trading.rules import SymbolRules
from quantdeck_engine.trading.state import (
    AccountState,
    PositionClosed,
    PositionOpened,
    StateChange,
    follow_user_stream,
)
from quantdeck_engine.trading.strategy_runner import (
    ForwardRecord,
    StrategyRunner,
    forward_record_path,
)

logger = logging.getLogger(__name__)

TIME_SYNC_INTERVAL = 60 * 60  # seconds; signed timestamps must stay within recvWindow


class RunFailed(Exception):
    """The engine could not start."""


class Engine:
    def __init__(
        self,
        client: BinanceRestClient,
        ws_root: str,
        trading: TradingConfig,
        account: Account,
        symbols: Mapping[str, SymbolConfig],
        symbol_store: SymbolStore,
        *,
        testnet: bool,
        runners: Mapping[str, StrategyRunner] | None = None,
        forward_dir: Path = FORWARD_DIR,
        checker: RiskChecker | None = None,
        time_sync_interval: float = TIME_SYNC_INTERVAL,
    ) -> None:
        self._client = client
        self._ws_root = ws_root
        self._trading = trading
        self._base = account.base
        self._ids = account.ids
        self._store = symbol_store
        self._forward_dir = forward_dir
        self._time_sync_interval = time_sync_interval
        self.testnet = testnet
        self.asset = account.base.asset  # the margin asset the daily loss limit covers (D-23)
        self.symbols = dict(symbols)  # as saved in symbols.json (D-47)
        self.traders = {t.symbol: t for t in account.traders}
        self.guard = account.guard
        self.runners = dict(runners or {})  # symbols with a strategy (D-43)
        self.checker = checker or RiskChecker(self.guard)
        self.state = AccountState(list(self.traders))
        self.mark_prices: dict[str, Decimal] = {}  # for display only (D-33)
        self.started_at = time.time()  # clients show the engine's uptime from it
        self.stop_requested = asyncio.Event()
        self._tasks: asyncio.TaskGroup | None = None  # set while running
        self._runner_tasks: dict[str, asyncio.Task[None]] = {}
        self._mark_task: asyncio.Task[None] | None = None
        self._changing = asyncio.Lock()  # one symbol change at a time

    async def run(self) -> None:
        """Follow the account and run the strategies until cancelled."""
        async with asyncio.TaskGroup() as tasks:
            self._tasks = tasks
            tasks.create_task(
                follow_user_stream(
                    self._client, self._ws_root, self.state, self.on_change, self.on_event
                )
            )
            tasks.create_task(self.checker.run())
            tasks.create_task(self._sync_time())
            self._follow_mark_prices()
            for runner in self.runners.values():
                self._start(runner)

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

    async def set_symbol(self, args: Mapping[str, Any]) -> str:
        """Add a symbol or change its leverage and strategy (D-47); returns what was done.

        Raises `ConfigError` for a value out of bounds and `SymbolChangeRefused` when the
        change can't be made now. Either way, and on a request error, nothing changed.
        """
        config = parse_set_symbol(args)
        async with self._changing:
            current = self.symbols.get(config.symbol)
            if current == config:
                return f"{config.symbol} unchanged"
            if current is None:
                await self._add(config)
                done = f"{config.symbol} added: {config.describe()}"
            else:
                await self._change(current, config)
                done = f"{config.symbol} changed: {config.describe()}"
        logger.info("%s (D-47)", done)
        return done

    async def remove_symbol(self, args: Mapping[str, Any]) -> str:
        """Stop trading a flat symbol and forget it (D-47)."""
        symbol = parse_remove_symbol(args)
        async with self._changing:
            if symbol not in self.symbols:
                raise SymbolChangeRefused(f"{symbol} is not one of the engine's symbols")
            trader = self.traders[symbol]
            try:
                async with trader.flat():
                    self._store.save({s: c for s, c in self.symbols.items() if s != symbol})
                    await self._stop_runner(symbol)
                    del self.symbols[symbol]
                    del self.traders[symbol]
                    self.guard.attach(list(self.traders.values()))
                    self.state.remove_symbol(symbol)
                    try:
                        await trader.clear_leftovers()
                    except REQUEST_ERRORS as e:
                        logger.warning("%s: leftover orders not checked: %s", symbol, e)
            except PositionOpen as e:
                raise _refused(e) from None
            self._follow_mark_prices()
        logger.info("%s removed (D-47)", symbol)
        return f"{symbol} removed"

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
                if (trader := self.traders.get(symbol)) is not None:
                    await trader.on_position_closed()
            case PositionOpened(symbol, amount):
                logger.info("%s: position opened: %s", symbol, amount)

    # Symbol changes (D-47)

    async def _add(self, config: SymbolConfig) -> None:
        symbol = config.symbol
        if len(self.symbols) >= MAX_SYMBOLS:
            raise SymbolChangeRefused(f"the engine trades at most {MAX_SYMBOLS} symbols")
        rules = await self._rules_for_new(symbol)
        trader = SymbolTrader(
            self._client, config, self._trading, rules, self._base, self.guard, self._ids
        )
        # Nobody else trades a symbol the engine doesn't have yet (D-14), so the position
        # can't open between this check and `prepare`.
        if (position := await trader.position()) is not None:
            raise SymbolChangeRefused(
                f"{symbol} has an open {position.side} position {position.quantity}; "
                "a symbol is added only while it is flat"
            )
        await trader.prepare()  # isolated margin, leverage, leftover orders (D-16, D-18)
        runner = None
        if config.strategy is not None:
            runner = await warmed_up_runner(
                self._client, self._ws_root, trader, config.strategy, self._forward_dir
            )
        self._store.save({**self.symbols, symbol: config})
        self.symbols[symbol] = config
        self.traders[symbol] = trader
        self.guard.attach(list(self.traders.values()))
        self.state.add_symbol(symbol)
        if runner is not None:
            self.runners[symbol] = runner
            self._start(runner)
        self._follow_mark_prices()

    async def _rules_for_new(self, symbol: str) -> SymbolRules:
        try:
            info = await self._client.symbol_info(symbol)
        except UnknownSymbol as e:
            raise SymbolChangeRefused(str(e)) from None
        if info.get("contractType") != "PERPETUAL" or info.get("status") != "TRADING":
            raise SymbolChangeRefused(
                f"{symbol} is a {info.get('contractType')} contract in status "
                f"{info.get('status')}; only trading perpetuals can be added"
            )
        rules = SymbolRules.from_exchange_info(info)
        if rules.margin_asset != self.asset:
            raise SymbolChangeRefused(
                f"{symbol} is margined in {rules.margin_asset}; the daily loss limit covers "
                f"{self.asset} only (D-23)"
            )
        return rules

    async def _change(self, current: SymbolConfig, config: SymbolConfig) -> None:
        symbol = config.symbol
        trader = self.traders[symbol]
        new_strategy = config.strategy != current.strategy
        runner = None
        if new_strategy and config.strategy is not None:
            # Before taking the lock: the warm-up reads candles only.
            runner = await warmed_up_runner(
                self._client, self._ws_root, trader, config.strategy, self._forward_dir
            )
        try:
            async with trader.flat():
                new_leverage = config.leverage != current.leverage
                if new_leverage:
                    await trader.set_leverage(config.leverage)
                try:
                    self._store.save({**self.symbols, symbol: config})
                except OSError:
                    if new_leverage:
                        await self._restore_leverage(trader, current.leverage)
                    raise
                trader.reconfigure(config)
                self.symbols[symbol] = config
                if new_strategy:
                    await self._stop_runner(symbol)
                    if runner is not None:
                        self.runners[symbol] = runner
                        self._start(runner)
        except PositionOpen as e:
            raise _refused(e) from None

    async def _restore_leverage(self, trader: SymbolTrader, leverage: int) -> None:
        try:
            await trader.set_leverage(leverage)
        except REQUEST_ERRORS as e:
            # The exchange now differs from what entries are sized for (D-16).
            trader.halt(f"leverage not restored to {leverage}x after a failed change: {e}")

    # Tasks

    def _start(self, runner: StrategyRunner) -> None:
        """Run a strategy; before `run`, it starts with the others."""
        if self._tasks is not None:
            self._runner_tasks[runner.symbol] = self._tasks.create_task(runner.run())

    async def _stop_runner(self, symbol: str) -> None:
        self.runners.pop(symbol, None)
        task = self._runner_tasks.pop(symbol, None)
        if task is not None:
            task.cancel()
            await asyncio.wait({task})

    def _follow_mark_prices(self) -> None:
        """(Re)subscribe the mark price stream to the current symbols."""
        if self._mark_task is not None:
            self._mark_task.cancel()
            self._mark_task = None
        self.mark_prices = {s: p for s, p in self.mark_prices.items() if s in self.traders}
        if self._tasks is not None and self.traders:
            self._mark_task = self._tasks.create_task(self._mark_prices(list(self.traders)))

    async def _mark_prices(self, symbols: list[str]) -> None:
        async with aclosing(mark_price_stream(self._ws_root, symbols)) as stream:
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


def _refused(error: PositionOpen) -> SymbolChangeRefused:
    return SymbolChangeRefused(f"{error}; a symbol's settings change only while it is flat")


async def warmed_up_runner(
    client: BinanceRestClient,
    ws_root: str,
    trader: SymbolTrader,
    strategy: StrategyConfig,
    forward_dir: Path,
) -> StrategyRunner:
    """A symbol's strategy with its warm-up candles loaded (D-43); its first decision, on the
    latest of them, comes once it runs."""
    runner = StrategyRunner(
        strategy.build(),
        trader,
        ClosedCandleFeed(client, ws_root, trader.symbol, strategy.interval),
        ForwardRecord(forward_record_path(forward_dir, trader.symbol, strategy)),
    )
    await runner.warm_up()
    return runner


async def start_engine(
    client: BinanceRestClient,
    settings: Settings,
    trading: TradingConfig,
    store: HaltStore,
    symbols: Mapping[str, SymbolConfig],
    symbol_store: SymbolStore,
    *,
    forward_dir: Path = FORWARD_DIR,
) -> Engine:
    """Startup checks (D-05, D-16, D-19, D-26, D-27) and the strategies' warm-up (D-43);
    returns the engine ready to run."""
    account = await build_account(client, trading, store, symbols.values())
    for trader in account.traders:
        await trader.prepare()
    await account.guard.start()
    runners = {}
    for trader in account.traders:
        strategy = symbols[trader.symbol].strategy
        if strategy is not None:
            runners[trader.symbol] = await warmed_up_runner(
                client, settings.endpoints.ws, trader, strategy, forward_dir
            )
    return Engine(
        client,
        settings.endpoints.ws,
        trading,
        account,
        symbols,
        symbol_store,
        testnet=settings.testnet,
        runners=runners,
        forward_dir=forward_dir,
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
            # Read under the lock: a running engine may be writing the file (D-47).
            symbol_store = SymbolStore()
            symbols = load_symbols(symbol_store, trading)
            async with BinanceRestClient(settings) as client:
                engine = await start_engine(
                    client, settings, trading, HaltStore(), symbols, symbol_store
                )
                if engine.symbols:
                    running = "Engine running for " + "; ".join(
                        f"{s} ({c.describe()})" for s, c in engine.symbols.items()
                    )
                else:
                    running = "Engine running with no symbols; add them from the GUI (D-47)"
                if engine.guard.halted:
                    logger.warning("%s; halted: no new orders (D-27)", running)
                else:
                    logger.info("%s", running)
                server = ClientServer(engine, logs)
                async with asyncio.TaskGroup() as tasks:
                    running_tasks = [
                        tasks.create_task(engine.run()),
                        tasks.create_task(_serve_clients(server)),
                    ]
                    await engine.stop_requested.wait()
                    # Let the stop command's result reach the client that sent it.
                    await server.flush()
                    for task in running_tasks:
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
