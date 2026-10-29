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
before the engine acts on it, and without touching the other symbols. They also pause one
symbol, close its position and pause it, or resume it (D-50); the pause is saved with the
symbol, so a restart keeps it, and the other symbols keep trading. They also get the last 7
days' trades, read from the exchange (D-51), and each strategy's candles for a chart (D-54).

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

from quantdeck_engine.amounts import fixed_point
from quantdeck_engine.binance.candle_feed import ClosedCandleFeed
from quantdeck_engine.binance.rest import REQUEST_ERRORS, BinanceRestClient, UnknownSymbol
from quantdeck_engine.binance.streams import mark_price_stream
from quantdeck_engine.binance.user_stream import (
    AccountUpdate,
    OrderUpdate,
    UserEvent,
    UserStreamConnected,
)
from quantdeck_engine.chart import CandleChart
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
    PauseNotSaved,
    SymbolChangeRefused,
    SymbolStore,
    load_symbols,
    parse_set_symbol,
    parse_symbol_arg,
)
from quantdeck_engine.times import iso_utc
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
from quantdeck_engine.trading.trades import TradeHistory

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
        paused: Mapping[str, str] | None = None,
        forward_dir: Path = FORWARD_DIR,
    ) -> None:
        self._client = client
        self._ws_root = ws_root
        self._trading = trading
        self._base = account.base
        self._ids = account.ids
        self._store = symbol_store
        self._forward_dir = forward_dir
        self.testnet = testnet
        self.asset = account.base.asset  # the margin asset the daily loss limit covers (D-23)
        self.symbols = dict(symbols)  # as saved in symbols.json (D-47)
        self.traders = {t.symbol: t for t in account.traders}
        self.guard = account.guard
        self.runners = dict(runners or {})  # symbols with a strategy (D-43)
        self.checker = RiskChecker(self.guard)
        self.state = AccountState(list(self.traders))
        self.mark_prices: dict[str, Decimal] = {}  # for display only (D-33)
        self.history = TradeHistory(client, lambda: list(self.traders), self.asset)  # D-51
        self.started_at = time.time()  # clients show the engine's uptime from it
        self.stop_requested = asyncio.Event()
        self._tasks: asyncio.TaskGroup | None = None  # set while running
        self._runner_tasks: dict[str, asyncio.Task[None]] = {}
        self._mark_task: asyncio.Task[None] | None = None
        self._changing = asyncio.Lock()  # one symbol change at a time
        for symbol, since in (paused or {}).items():
            # Before any strategy runs, so a startup decision can't enter (D-50).
            self.traders[symbol].pause(since)
            logger.warning(
                "%s: paused by the user since %s: no new entries until resumed",
                symbol,
                since,
            )

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
            tasks.create_task(self.history.run())
            tasks.create_task(self._sync_time())
            self._follow_mark_prices()
            for runner in self.runners.values():
                self._start(runner)

    # Commands from clients (D-33)

    async def emergency_close(self) -> bool:
        """The kill switch, fired by the user (D-27)."""
        return await self.guard.trip("emergency close requested by the user")

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
        logger.info("%s", done)
        return done

    async def remove_symbol(self, args: Mapping[str, Any]) -> str:
        """Stop trading a flat symbol and forget it (D-47), with its pause (D-50)."""
        symbol = parse_symbol_arg(args, "remove_symbol")
        async with self._changing:
            trader = self._trader(symbol)
            try:
                async with trader.flat():
                    self._save({s: c for s, c in self.symbols.items() if s != symbol})
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
        logger.info("%s removed", symbol)
        return f"{symbol} removed"

    async def pause_symbol(self, args: Mapping[str, Any]) -> str:
        """The user's pause (D-50): no new entries on this symbol until resumed, kept across
        restarts. Its position, stop, and exit signals carry on.

        Returns once any entry that started before the pause has finished. Raises OSError if
        the pause can't be saved, having changed nothing.
        """
        symbol = parse_symbol_arg(args, "pause_symbol")
        async with self._changing:
            trader = self._trader(symbol)
            if trader.paused_at is not None:
                return f"{symbol} already paused since {trader.paused_at}"
            trader.pause(iso_utc(time.time()))
            try:
                self._save(self.symbols)
            except OSError:
                trader.resume()
                raise
            logger.warning(
                "%s: paused by the user: no new entries; its position, stop, and exit "
                "signals carry on",
                symbol,
            )
            await trader.idle()
        return f"{symbol} paused: no new entries until resumed"

    async def close_symbol(self, args: Mapping[str, Any]) -> str:
        """Pause the symbol (D-50), then close its position with reduceOnly market orders and
        cancel its stop. The other symbols keep trading.

        A pause that can't be saved still holds in this engine and never blocks the close (as
        D-29); `PauseNotSaved` then reports it once the position is closed.
        """
        symbol = parse_symbol_arg(args, "close_symbol")
        async with self._changing:
            trader = self._trader(symbol)
            unsaved: OSError | None = None
            if trader.paused_at is None:
                trader.pause(iso_utc(time.time()))
                try:
                    self._save(self.symbols)
                except OSError as e:
                    unsaved = e
                    logger.error(
                        "%s: the pause could not be saved to %s (%s); it holds until the "
                        "engine restarts",
                        symbol,
                        self._store.path,
                        e,
                    )
            logger.warning("%s: closing its position and pausing it, as the user asked", symbol)
            position = await trader.close_position()
        closed = (
            "had no position"
            if position is None
            else f"closed {position.side} {fixed_point(position.quantity)}"
        )
        done = f"{symbol} {closed}; paused since {trader.paused_at}"
        if unsaved is not None:
            raise PauseNotSaved(
                f"{done}, but the pause could not be saved, so a restart would trade "
                f"{symbol} again: {unsaved}"
            )
        return done

    async def resume_symbol(self, args: Mapping[str, Any]) -> str:
        """Lift the user's pause (D-50). The engine's own halt of the symbol (D-16, D-19,
        D-20) and the kill switch's halt (D-27) stay."""
        symbol = parse_symbol_arg(args, "resume_symbol")
        async with self._changing:
            trader = self._trader(symbol)
            since = trader.paused_at
            if since is None:
                return f"{symbol} not paused"
            trader.resume()
            try:
                self._save(self.symbols)
            except OSError:
                trader.pause(since)
                raise
        logger.warning("%s: pause lifted by the user (paused since %s)", symbol, since)
        done = f"{symbol} resumed: new entries from its strategy's next signal"
        if trader.halted:
            done += f"; still halted by the engine: {trader.halt_reason}"
        if self.guard.halted:
            done += "; the kill switch still halts the account"
        return done

    def snapshot(self) -> dict[str, Any]:
        """The state clients see (D-33)."""
        return engine_snapshot(self)

    def trades(self) -> dict[str, Any] | None:
        """The last 7 days' trades clients see (D-51); None until first read."""
        return self.history.payload

    def candles(self) -> dict[str, dict[str, Any]]:
        """Each strategy's closed candles for clients' charts (D-54), by symbol."""
        return {
            symbol: payload
            for symbol, runner in self.runners.items()
            if runner.chart is not None and (payload := runner.chart.payload) is not None
        }

    def on_event(self, event: UserEvent) -> None:
        # D-30: events sent while disconnected are lost, and funding changes today's loss.
        if isinstance(event, UserStreamConnected) or (
            isinstance(event, AccountUpdate) and event.reason == "FUNDING_FEE"
        ):
            self.checker.request()
        # The trades list reads the exchange again, as the risk check does (D-51).
        if isinstance(event, UserStreamConnected):
            self.history.request()
        elif isinstance(event, OrderUpdate) and event.execution_type == "TRADE":
            self.history.request(event.symbol)

    async def on_change(self, change: StateChange) -> None:
        match change:
            case PositionClosed(symbol):
                logger.info("%s: position closed", symbol)
                self.checker.request()
                if (trader := self.traders.get(symbol)) is None:
                    return
                # Not awaited while running: when the engine closes the position itself (a
                # strategy exit, the user's close, the kill switch), the close still holds the
                # symbol's lock, and the stream would hold back every symbol's events until it
                # ended.
                if self._tasks is not None:
                    self._tasks.create_task(self._clear_leftovers(trader))
                else:
                    await self._clear_leftovers(trader)
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
        self._save({**self.symbols, symbol: config})
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
                f"{self.asset} only"
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
                    # A paused symbol stays paused (D-50).
                    self._save({**self.symbols, symbol: config})
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

    def _trader(self, symbol: str) -> SymbolTrader:
        trader = self.traders.get(symbol)
        if trader is None:
            raise SymbolChangeRefused(f"{symbol} is not one of the engine's symbols")
        return trader

    def _save(self, symbols: Mapping[str, SymbolConfig]) -> None:
        """Write `symbols` to symbols.json with the user's pauses of them (D-50)."""
        paused = {
            s: t.paused_at
            for s, t in self.traders.items()
            if s in symbols and t.paused_at is not None
        }
        self._store.save(symbols, paused)

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

    async def _clear_leftovers(self, trader: SymbolTrader) -> None:
        """Cancel what a closed position left on the exchange (D-18). The trader checks under
        its lock that the symbol is still flat, so an entry made meanwhile keeps its stop."""
        try:
            await trader.on_position_closed()
        except REQUEST_ERRORS:
            # A backstop: Binance itself expires a closePosition stop once its position is gone.
            logger.exception("%s: cancelling leftover orders failed", trader.symbol)

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
            await asyncio.sleep(TIME_SYNC_INTERVAL)
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
    latest of them, comes once it runs. Its feed also fills the symbol's chart (D-54), which
    asks the same strategy for its lines (D-56)."""
    built = strategy.build()
    chart = CandleChart(trader.symbol, strategy.interval, strategy=built)
    runner = StrategyRunner(
        built,
        trader,
        ClosedCandleFeed(client, ws_root, trader.symbol, strategy.interval, chart=chart),
        ForwardRecord(forward_record_path(forward_dir, trader.symbol, strategy)),
        chart=chart,
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
    paused: Mapping[str, str] | None = None,
    forward_dir: Path = FORWARD_DIR,
) -> Engine:
    """Startup checks (D-05, D-16, D-19, D-26, D-27) and the strategies' warm-up (D-43);
    returns the engine ready to run, with the user's pauses (D-50) in place."""
    account = await build_account(client, trading, store, symbols.values())
    for trader in account.traders:
        await trader.prepare()
    daily = await account.guard.start()
    runners = {}
    for trader in account.traders:
        strategy = symbols[trader.symbol].strategy
        if strategy is not None:
            runners[trader.symbol] = await warmed_up_runner(
                client, settings.endpoints.ws, trader, strategy, forward_dir
            )
    engine = Engine(
        client,
        settings.endpoints.ws,
        trading,
        account,
        symbols,
        symbol_store,
        testnet=settings.testnet,
        runners=runners,
        paused=paused,
        forward_dir=forward_dir,
    )
    # The startup check just read today's loss: clients see it at once, and the checker
    # doesn't read it again before the stream connects (D-30).
    engine.checker.latest = daily
    return engine


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
            paused = symbol_store.load_paused()
            async with BinanceRestClient(settings) as client:
                engine = await start_engine(
                    client, settings, trading, HaltStore(), symbols, symbol_store, paused=paused
                )
                if engine.symbols:
                    running = "Engine running for " + "; ".join(
                        f"{s} ({c.describe()}{', paused' if s in paused else ''})"
                        for s, c in engine.symbols.items()
                    )
                else:
                    running = "Engine running with no symbols; add them from the GUI"
                if engine.guard.halted:
                    logger.warning("%s; halted: no new orders", running)
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
                logger.info("Engine stopped by a client")
    finally:
        root.removeHandler(logs)


async def _serve_clients(server: ClientServer) -> None:
    try:
        await server.run()
    except Exception:
        # Clients only watch and send commands; losing them must not stop the engine.
        logger.exception("The client server stopped; the engine keeps running without clients")
