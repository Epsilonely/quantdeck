"""Local view of positions, open orders, and balances, kept from user data events.

The exchange is the source of truth (D-06): the stream connects first, then every
`UserStreamConnected` triggers a REST snapshot, and events queued since the connect are
applied on top. Each order's last event decides its final state, so replaying events that
happened before the snapshot ends in the same place (D-13).

The engine re-reads the exchange before acting on anything safety-critical; this state is
for monitoring and for noticing changes, such as a position closing (D-18).
"""

import logging
from collections.abc import Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

from quantdeck_engine.binance.rest import BinanceAPIError, BinanceRestClient
from quantdeck_engine.binance.user_stream import (
    AccountUpdate,
    AlgoUpdate,
    OrderUpdate,
    UserEvent,
    UserStreamConnected,
    user_data_stream,
)

logger = logging.getLogger(__name__)

OPEN_ORDER_STATUSES = {"NEW", "PARTIALLY_FILLED"}
OPEN_ALGO_STATUSES = {"NEW"}


@dataclass(frozen=True)
class OpenOrder:
    client_id: str
    order_id: int
    side: str
    order_type: str
    quantity: Decimal
    reduce_only: bool


@dataclass(frozen=True)
class OpenAlgoOrder:
    client_algo_id: str
    algo_id: int
    side: str
    order_type: str
    trigger_price: Decimal
    close_position: bool


@dataclass
class SymbolState:
    symbol: str
    position_amount: Decimal = Decimal(0)  # negative for a short
    entry_price: Decimal = Decimal(0)
    orders: dict[str, OpenOrder] = field(default_factory=dict)  # open only, by client ID
    algo_orders: dict[str, OpenAlgoOrder] = field(default_factory=dict)  # open only


@dataclass(frozen=True)
class PositionClosed:
    symbol: str


@dataclass(frozen=True)
class PositionOpened:
    symbol: str
    amount: Decimal


StateChange = PositionClosed | PositionOpened


class AccountState:
    def __init__(self, symbols: list[str]) -> None:
        self.symbols = {s: SymbolState(s) for s in symbols}
        self.wallet_balances: dict[str, Decimal] = {}

    def apply(self, event: object) -> list[StateChange]:
        """Apply one user data event; return the position changes it caused."""
        if isinstance(event, OrderUpdate):
            self._apply_order(event)
        elif isinstance(event, AlgoUpdate):
            self._apply_algo(event)
        elif isinstance(event, AccountUpdate):
            return self._apply_account(event)
        return []

    def load_snapshot(
        self,
        *,
        balances: list[dict[str, Any]],
        positions: list[dict[str, Any]],
        open_orders: list[dict[str, Any]],
        open_algo_orders: list[dict[str, Any]],
    ) -> list[StateChange]:
        """Replace the state with REST responses; return position changes since before."""
        before = {s: st.position_amount for s, st in self.symbols.items()}
        self.wallet_balances = {b["asset"]: Decimal(b["balance"]) for b in balances}
        for state in self.symbols.values():
            state.position_amount = Decimal(0)
            state.entry_price = Decimal(0)
            state.orders.clear()
            state.algo_orders.clear()
        for p in positions:
            state = self.symbols.get(p["symbol"])
            if state is not None:
                state.position_amount = Decimal(p["positionAmt"])
                state.entry_price = Decimal(p["entryPrice"])
        for o in open_orders:
            state = self.symbols.get(o["symbol"])
            if state is not None:
                state.orders[o["clientOrderId"]] = OpenOrder(
                    client_id=o["clientOrderId"],
                    order_id=o["orderId"],
                    side=o["side"],
                    order_type=o["type"],
                    quantity=Decimal(o["origQty"]),
                    reduce_only=o["reduceOnly"],
                )
        for a in open_algo_orders:
            state = self.symbols.get(a["symbol"])
            if state is not None:
                state.algo_orders[a["clientAlgoId"]] = OpenAlgoOrder(
                    client_algo_id=a["clientAlgoId"],
                    algo_id=a["algoId"],
                    side=a["side"],
                    order_type=a["orderType"],
                    trigger_price=Decimal(a["triggerPrice"]),
                    close_position=a["closePosition"],
                )
        return [
            change
            for symbol, state in self.symbols.items()
            if (change := _position_change(symbol, before[symbol], state.position_amount))
        ]

    def _apply_order(self, event: OrderUpdate) -> None:
        state = self.symbols.get(event.symbol)
        if state is None:
            return
        if event.status in OPEN_ORDER_STATUSES:
            state.orders[event.client_order_id] = OpenOrder(
                client_id=event.client_order_id,
                order_id=event.order_id,
                side=event.side,
                order_type=event.order_type,
                quantity=event.quantity,
                reduce_only=event.reduce_only,
            )
        else:
            state.orders.pop(event.client_order_id, None)

    def _apply_algo(self, event: AlgoUpdate) -> None:
        state = self.symbols.get(event.symbol)
        if state is None:
            return
        if event.status in OPEN_ALGO_STATUSES:
            state.algo_orders[event.client_algo_id] = OpenAlgoOrder(
                client_algo_id=event.client_algo_id,
                algo_id=event.algo_id,
                side=event.side,
                order_type=event.order_type,
                trigger_price=event.trigger_price,
                close_position=event.close_position,
            )
        else:
            state.algo_orders.pop(event.client_algo_id, None)

    def _apply_account(self, event: AccountUpdate) -> list[StateChange]:
        for b in event.balances:
            self.wallet_balances[b.asset] = b.wallet_balance
        changes: list[StateChange] = []
        for p in event.positions:
            state = self.symbols.get(p.symbol)
            # One-way mode reports positionSide BOTH (D-05).
            if state is None or p.position_side != "BOTH":
                continue
            before = state.position_amount
            state.position_amount = p.amount
            state.entry_price = p.entry_price
            if change := _position_change(p.symbol, before, p.amount):
                changes.append(change)
        return changes


def _position_change(symbol: str, before: Decimal, after: Decimal) -> StateChange | None:
    if before != 0 and after == 0:
        return PositionClosed(symbol)
    if before == 0 and after != 0:
        return PositionOpened(symbol, after)
    return None


async def resync(client: BinanceRestClient, state: AccountState) -> list[StateChange]:
    """Replace the state with a REST snapshot of every tracked symbol."""
    balances = await client.balances()
    positions = await client.positions()
    open_orders: list[dict[str, Any]] = []
    open_algo_orders: list[dict[str, Any]] = []
    for symbol in state.symbols:
        # Per symbol: weight 1 each, against 40 for all symbols at once.
        open_orders.extend(await client.open_orders(symbol))
        open_algo_orders.extend(await client.open_algo_orders(symbol))
    return state.load_snapshot(
        balances=balances,
        positions=positions,
        open_orders=open_orders,
        open_algo_orders=open_algo_orders,
    )


async def follow_user_stream(
    client: BinanceRestClient,
    ws_root: str,
    state: AccountState,
    on_change: Callable[[StateChange], Awaitable[None]],
    on_event: Callable[[UserEvent], None] | None = None,
) -> None:
    """Keep `state` current from the user data stream until cancelled.

    Each (re)connect is followed by a REST resync before any queued event is applied.
    """
    async with aclosing(user_data_stream(client, ws_root)) as stream:
        async for event in stream:
            if on_event is not None:
                on_event(event)
            if isinstance(event, UserStreamConnected):
                try:
                    changes = await resync(client, state)
                except (BinanceAPIError, httpx.HTTPError):
                    logger.exception("Resync after connect failed; state may be stale")
                    continue
                logger.info("Account state resynced from REST")
            else:
                changes = state.apply(event)
            for change in changes:
                try:
                    await on_change(change)
                except (BinanceAPIError, httpx.HTTPError):
                    # Keep following: the next resync shows the exchange's state again.
                    logger.exception("Handling %s failed", change)
