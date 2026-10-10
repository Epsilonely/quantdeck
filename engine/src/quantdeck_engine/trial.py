"""`quantdeck-engine trial`: run the order flow once on testnet, with no strategy.

Entry -> exchange-side stop -> the stop is cancelled to simulate losing it -> the startup
check finds the unprotected position and places an emergency stop (D-26) -> reduceOnly
close -> the leftover stop is cancelled. Refuses to run against mainnet, and only on a flat
symbol. Don't run it while an engine is trading on the same account (D-14).

Run with ENGINE_LOG_LEVEL=DEBUG to log every user data payload as received.
"""

import asyncio
import contextlib
from decimal import Decimal

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
from quantdeck_engine.trading.balance import BaseBalance
from quantdeck_engine.trading.executor import SymbolTrader
from quantdeck_engine.trading.order_ids import ClientIdFactory
from quantdeck_engine.trading.rules import Side, SymbolRules
from quantdeck_engine.trading.state import (
    AccountState,
    PositionClosed,
    StateChange,
    follow_user_stream,
)

CONNECT_TIMEOUT_SECONDS = 30
EVENT_WAIT_SECONDS = 3  # time for user data events to arrive after each step


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

    async with BinanceRestClient(settings) as client:
        await client.sync_time()
        if (await client.account_config())["dualSidePosition"]:
            raise TrialFailed("the account is in hedge mode; QuantDeck needs one-way mode (D-05)")
        info = await client.exchange_info()
        symbol_info = next((s for s in info["symbols"] if s["symbol"] == symbol), None)
        if symbol_info is None:
            raise TrialFailed(f"{symbol} is not in exchangeInfo")
        rules = SymbolRules.from_exchange_info(symbol_info)
        trader = SymbolTrader(
            client,
            config,
            trading,
            rules,
            BaseBalance(client, rules.margin_asset),
            ClientIdFactory(),
        )

        connected = asyncio.Event()

        def on_event(event: UserEvent) -> None:
            print(f"  [event] {describe(event)}")
            if isinstance(event, UserStreamConnected):
                connected.set()

        async def on_change(change: StateChange) -> None:
            if isinstance(change, PositionClosed):
                await trader.on_position_closed()

        follower = asyncio.create_task(
            follow_user_stream(
                client, settings.endpoints.ws, AccountState([symbol]), on_change, on_event
            )
        )
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT_SECONDS):
                await connected.wait()
            await _steps(client, trader, symbol, side, stop_percent)
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
    symbol: str,
    side: Side,
    stop_percent: Decimal,
) -> None:
    print("1. Startup check (margin type, leverage, leftover orders)")
    await trader.prepare()
    if await trader.position() is not None:
        raise TrialFailed(f"{symbol} already has a position; the trial needs a flat symbol")
    if trader.halted:
        raise TrialFailed(f"trading is halted: {trader.halt_reason}")

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

    if await trader.position() is not None:
        raise TrialFailed("the position is still open")
    left = await client.open_algo_orders(symbol) + await client.open_orders(symbol)
    if left:
        raise TrialFailed(f"{len(left)} order(s) still open on {symbol}")
    print("Trial passed: flat, no open orders.")
