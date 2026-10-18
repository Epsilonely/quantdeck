"""`quantdeck-engine trial`: run the order flow once on testnet, with no strategy.

Entry -> exchange-side stop -> the stop is cancelled to simulate losing it -> the startup
check finds the unprotected position and places an emergency stop (D-26) -> reduceOnly
close -> the leftover stop is cancelled. Then the kill switch: a second entry, the kill
switch fired by hand, how soon income history shows its close (D-30), and a restart that
must come up halted (D-27). The trial's halt record goes to a temporary directory, never
`engine/state/`.

Refuses to run against mainnet, while the engine is halted or running (D-31), and on a
symbol that isn't flat. Don't run it while an engine on another machine trades the same
account (D-14).

Run with ENGINE_LOG_LEVEL=DEBUG to log every user data payload as received.
"""

import asyncio
import contextlib
import tempfile
import time
from decimal import Decimal
from pathlib import Path

from quantdeck_engine.binance.rest import BinanceRestClient
from quantdeck_engine.binance.user_stream import (
    AccountUpdate,
    AlgoUpdate,
    OrderUpdate,
    RawEvent,
    UserEvent,
    UserStreamConnected,
)
from quantdeck_engine.config import Settings, TradingConfig
from quantdeck_engine.lock import InstanceLock
from quantdeck_engine.startup import build_account
from quantdeck_engine.trading.balance import BaseBalance, fetch_income
from quantdeck_engine.trading.executor import SymbolTrader, TradingHalted
from quantdeck_engine.trading.halt import HaltStore
from quantdeck_engine.trading.risk import RiskGuard
from quantdeck_engine.trading.rules import Side
from quantdeck_engine.trading.state import (
    AccountState,
    PositionClosed,
    StateChange,
    follow_user_stream,
)

CONNECT_TIMEOUT_SECONDS = 30
EVENT_WAIT_SECONDS = 3  # time for user data events to arrive after each step
INCOME_POLLS = 10  # once a second; each read costs weight 30


class TrialFailed(Exception):
    """The trial could not run or ended in an unexpected state."""


def describe(event: UserEvent) -> str:
    match event:
        case UserStreamConnected():
            return "user data stream connected"
        case OrderUpdate():
            return (
                f"ORDER_TRADE_UPDATE {event.client_order_id} {event.order_type} {event.side} "
                f"{event.execution_type}/{event.status} filled {event.filled_quantity} "
                f"@ {event.average_price}" + (" reduceOnly" if event.reduce_only else "")
            )
        case AlgoUpdate():
            return (
                f"ALGO_UPDATE {event.client_algo_id} {event.order_type} {event.side} "
                f"{event.status} trigger {event.trigger_price}"
                + (" closePosition" if event.close_position else "")
                + (f" ({event.reject_reason})" if event.reject_reason else "")
            )
        case AccountUpdate():
            positions = ", ".join(
                f"{p.symbol} {p.amount} @ {p.entry_price}" for p in event.positions
            )
            return f"ACCOUNT_UPDATE {event.reason}: {positions or 'no position change'}"
        case RawEvent():
            return f"{event.event_type or 'unknown event'} (not parsed)"
    return repr(event)


async def run_trial(
    settings: Settings,
    trading: TradingConfig,
    symbol: str,
    side: Side,
    stop_percent: Decimal,
) -> None:
    if not settings.testnet:
        raise TrialFailed("trial places orders and runs on testnet only")
    config = trading.symbols.get(symbol)
    if config is None:
        raise TrialFailed(f"{symbol} is not in config.toml [symbols]")
    if (record := HaltStore().load()) is not None:
        raise TrialFailed(f"the engine is halted by the kill switch ({record.reason})")

    # The same lock as `run`, so the trial never trades beside a running engine (D-14, D-31).
    with InstanceLock(), tempfile.TemporaryDirectory() as state_dir:
        store = HaltStore(Path(state_dir) / "halt.json")
        await _run(settings, trading, config.symbol, side, stop_percent, store)


async def _run(
    settings: Settings,
    trading: TradingConfig,
    symbol: str,
    side: Side,
    stop_percent: Decimal,
    store: HaltStore,
) -> None:
    async with BinanceRestClient(settings) as client:
        account = await build_account(client, trading, store, symbols=[symbol])
        (trader,), guard, base = account.traders, account.guard, account.base

        connected = asyncio.Event()

        def on_event(event: UserEvent) -> None:
            print(f"  [event] {describe(event)}")
            if isinstance(event, UserStreamConnected):
                connected.set()

        async def on_change(change: StateChange) -> None:
            if isinstance(change, PositionClosed):
                await trader.on_position_closed()
                await guard.check()

        follower = asyncio.create_task(
            follow_user_stream(
                client, settings.endpoints.ws, AccountState([symbol]), on_change, on_event
            )
        )
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT_SECONDS):
                await connected.wait()
            await _steps(client, trader, guard, symbol, side, stop_percent)
            await _kill_switch_steps(
                client, trading, trader, guard, base, store, side, stop_percent
            )
        finally:
            # Never leave a testnet position behind, whatever went wrong.
            with contextlib.suppress(Exception):
                if await trader.position() is not None:
                    print("Closing the position left open by the failed trial...")
                    await trader.close_position()
            follower.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await follower


async def _steps(
    client: BinanceRestClient,
    trader: SymbolTrader,
    guard: RiskGuard,
    symbol: str,
    side: Side,
    stop_percent: Decimal,
) -> None:
    print("1. Startup check (margin type, leverage, leftover orders, today's loss)")
    await trader.prepare()
    if await trader.position() is not None:
        raise TrialFailed(f"{symbol} already has a position; the trial needs a flat symbol")
    if trader.halted:
        raise TrialFailed(f"trading is halted: {trader.halt_reason}")
    await guard.start()
    if guard.halted:
        raise TrialFailed(f"the kill switch fired at startup: {guard.halt_reason}")

    mark = await client.mark_price(symbol)
    stop = mark * (1 - side.sign * stop_percent / 100)
    print(f"2. Entering {side} at market (mark {mark}), stop {stop_percent}% away")
    plan = await trader.open_position(side, stop)
    if plan is None:
        raise TrialFailed("the entry was skipped or closed; see the log")
    print(
        f"   quantity {plan.quantity}, stop {plan.stop_price}, risk {plan.risk:.2f}, "
        f"est. liquidation {plan.liquidation_price:.2f}"
        + (f", trimmed by {plan.trimmed_by}" if plan.trimmed_by else "")
    )
    await asyncio.sleep(EVENT_WAIT_SECONDS)
    stops = await trader.open_stops(side)
    if len(stops) != 1:
        raise TrialFailed(f"expected one open stop, found {len(stops)}")

    print("3. Cancelling the stop to simulate a position that lost it")
    await client.cancel_algo_order(stops[0]["clientAlgoId"])
    await asyncio.sleep(EVENT_WAIT_SECONDS)

    print("4. Startup check again: expect an ERROR and an emergency stop (D-19, D-26)")
    await trader.prepare()
    await asyncio.sleep(EVENT_WAIT_SECONDS)
    stops = await trader.open_stops(side)
    if len(stops) != 1:
        raise TrialFailed(f"expected one emergency stop, found {len(stops)}")
    print(f"   emergency stop {stops[0]['clientAlgoId']} at {stops[0]['triggerPrice']}")

    print("5. Closing with a reduceOnly market order; the stop should be cancelled")
    await trader.close_position()
    await asyncio.sleep(EVENT_WAIT_SECONDS)
    await _expect_flat(client, trader)


async def _kill_switch_steps(
    client: BinanceRestClient,
    trading: TradingConfig,
    trader: SymbolTrader,
    guard: RiskGuard,
    base: BaseBalance,
    store: HaltStore,
    side: Side,
    stop_percent: Decimal,
) -> None:
    symbol = trader.symbol
    mark = await client.mark_price(symbol)
    stop = mark * (1 - side.sign * stop_percent / 100)
    print(f"6. Entering {side} again (mark {mark}), then firing the kill switch by hand (D-27)")
    if await trader.open_position(side, stop) is None:
        raise TrialFailed("the second entry was skipped or closed; see the log")
    await asyncio.sleep(EVENT_WAIT_SECONDS)
    fired_ms = int(time.time() * 1000)
    if not await guard.trip("trial: kill switch fired by hand"):
        raise TrialFailed("the kill switch could not flatten; see the log")
    await _report_income_delay(client, symbol, fired_ms)
    await asyncio.sleep(EVENT_WAIT_SECONDS)
    await _expect_flat(client, trader)
    if store.load() is None:
        raise TrialFailed("the kill switch wrote no halt record")
    try:
        await trader.open_position(side, stop)
    except TradingHalted:
        print("   halted: an entry was refused")
    else:
        raise TrialFailed("an entry went through while halted")

    print("7. Restart: a new guard reading the same halt record must start halted")
    restarted = RiskGuard(client, trading, base, store)
    restarted.attach([trader])
    await restarted.start()
    if not restarted.halted:
        raise TrialFailed("the restarted guard is not halted")
    store.clear()
    if store.load() is not None:
        raise TrialFailed("the halt record is still there after lifting")
    print("   halted after the restart; the trial's halt record is lifted")

    print("8. Today's loss from income history (D-24, D-30)")
    daily = await guard.daily_loss()
    print(f"   {daily.describe(base.asset)}")
    print("Trial passed: flat, no open orders, kill switch and halt record work.")


async def _report_income_delay(client: BinanceRestClient, symbol: str, fired_ms: int) -> None:
    """How soon income history shows the kill-switch close (D-30)."""
    flattened = time.monotonic()
    # Income record times are whole seconds (testnet 2026-10-16): a start time in the middle
    # of the close's second would leave its records out.
    since = fired_ms - fired_ms % 1000
    for poll in range(1, INCOME_POLLS + 1):
        incomes = await fetch_income(client, since, int(time.time() * 1000))
        if any(i["incomeType"] == "REALIZED_PNL" and i["symbol"] == symbol for i in incomes):
            print(
                f"   income history showed the close {time.monotonic() - flattened:.1f} s "
                f"after the kill switch finished (read {poll} time(s))"
            )
            return
        await asyncio.sleep(1)
    print(f"   no REALIZED_PNL record within {INCOME_POLLS} reads: income history lags")


async def _expect_flat(client: BinanceRestClient, trader: SymbolTrader) -> None:
    if await trader.position() is not None:
        raise TrialFailed("the position is still open")
    left = await client.open_algo_orders(trader.symbol) + await client.open_orders(trader.symbol)
    if left:
        raise TrialFailed(f"{len(left)} order(s) still open on {trader.symbol}")
