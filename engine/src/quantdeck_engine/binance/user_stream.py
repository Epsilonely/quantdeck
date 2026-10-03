"""User data stream for Binance USDⓈ-M Futures: order and account events for this account.

Behavior verified on testnet is recorded in docs/memory-bank/binanceNotes.md. In short:

- An account has one listenKey, and POST returns the active one again, so every process on
  the account shares it. Deleting it would cut them all off, so this module never does; an
  unused key expires 60 minutes after its last POST or PUT.
- Connecting with an invalid key succeeds and then stays silent, so every connection uses
  a key fetched right before it.
- Events sent while disconnected are lost. Every (re)connect yields `UserStreamConnected`,
  and the consumer must then refresh positions, balances, and open orders from REST.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from quantdeck_engine.binance.rest import BinanceAPIError, BinanceRestClient

logger = logging.getLogger(__name__)

KEEPALIVE_SECONDS = 30 * 60  # a listenKey expires 60 minutes after its last POST or PUT
RETRY_DELAYS = (1, 2, 5, 10, 30, 60)  # seconds between failed attempts; the last one repeats


@dataclass(frozen=True)
class UserStreamConnected:
    """The stream (re)connected. Anything sent while disconnected is lost: refresh from REST."""


@dataclass(frozen=True)
class OrderUpdate:
    """`ORDER_TRADE_UPDATE`: an order was created or its status changed."""

    event_time: int  # ms
    symbol: str
    order_id: int
    client_order_id: str
    side: str  # BUY, SELL
    order_type: str  # LIMIT, MARKET, STOP_MARKET, ..., LIQUIDATION
    execution_type: str  # NEW, TRADE, CANCELED, EXPIRED, CALCULATED, AMENDMENT
    status: str  # NEW, PARTIALLY_FILLED, FILLED, CANCELED, EXPIRED, EXPIRED_IN_MATCH
    position_side: str
    reduce_only: bool
    close_position: bool  # a close-all conditional order
    quantity: Decimal
    price: Decimal
    stop_price: Decimal
    average_price: Decimal
    last_filled_quantity: Decimal
    last_filled_price: Decimal
    filled_quantity: Decimal  # accumulated
    commission: Decimal
    commission_asset: str | None
    realized_profit: Decimal
    trade_id: int
    trade_time: int  # ms


@dataclass(frozen=True)
class BalanceChange:
    asset: str
    wallet_balance: Decimal
    cross_wallet_balance: Decimal
    balance_change: Decimal  # excluding PnL and commission


@dataclass(frozen=True)
class PositionChange:
    symbol: str
    amount: Decimal  # negative for a short position
    entry_price: Decimal
    unrealized_pnl: Decimal
    margin_type: str
    position_side: str


@dataclass(frozen=True)
class AccountUpdate:
    """`ACCOUNT_UPDATE`: balances or positions changed."""

    event_time: int  # ms
    reason: str  # ORDER, FUNDING_FEE, DEPOSIT, ...
    balances: tuple[BalanceChange, ...]
    positions: tuple[PositionChange, ...]  # only symbols whose position changed


@dataclass(frozen=True)
class RawEvent:
    """An event this module doesn't parse, or failed to; `data` is the payload as received."""

    event_type: str
    data: dict[str, Any]


UserEvent = UserStreamConnected | OrderUpdate | AccountUpdate | RawEvent


def parse_order_update(data: dict[str, Any]) -> OrderUpdate:
    o = data["o"]
    return OrderUpdate(
        event_time=data["E"],
        symbol=o["s"],
        order_id=o["i"],
        client_order_id=o["c"],
        side=o["S"],
        order_type=o["o"],
        execution_type=o["x"],
        status=o["X"],
        position_side=o["ps"],
        reduce_only=o["R"],
        close_position=o.get("cp", False),
        quantity=Decimal(o["q"]),
        price=Decimal(o["p"]),
        stop_price=Decimal(o["sp"]),
        average_price=Decimal(o["ap"]),
        last_filled_quantity=Decimal(o["l"]),
        last_filled_price=Decimal(o["L"]),
        filled_quantity=Decimal(o["z"]),
        # Older docs say these two are left out when there is no commission.
        commission=Decimal(o.get("n", "0")),
        commission_asset=o.get("N"),
        realized_profit=Decimal(o["rp"]),
        trade_id=o["t"],
        trade_time=o["T"],
    )


def parse_account_update(data: dict[str, Any]) -> AccountUpdate:
    a = data["a"]
    return AccountUpdate(
        event_time=data["E"],
        reason=a["m"],
        balances=tuple(
            BalanceChange(
                asset=b["a"],
                wallet_balance=Decimal(b["wb"]),
                cross_wallet_balance=Decimal(b["cw"]),
                balance_change=Decimal(b["bc"]),
            )
            for b in a.get("B", [])
        ),
        positions=tuple(
            PositionChange(
                symbol=p["s"],
                amount=Decimal(p["pa"]),
                entry_price=Decimal(p["ep"]),
                unrealized_pnl=Decimal(p["up"]),
                margin_type=p["mt"],
                position_side=p["ps"],
            )
            for p in a.get("P", [])
        ),
    )


PARSERS: dict[str, Callable[[dict[str, Any]], UserEvent]] = {
    "ORDER_TRADE_UPDATE": parse_order_update,
    "ACCOUNT_UPDATE": parse_account_update,
}


def parse_user_event(data: dict[str, Any]) -> UserEvent:
    """Parse one event payload. Raises if a parsed event type is missing fields."""
    event_type = data.get("e", "")
    parser = PARSERS.get(event_type)
    return parser(data) if parser else RawEvent(event_type, data)


async def user_data_stream(
    client: BinanceRestClient,
    ws_root: str,
    *,
    keepalive_seconds: float = KEEPALIVE_SECONDS,
    retry_delays: Sequence[float] = RETRY_DELAYS,
) -> AsyncIterator[UserEvent]:
    """Yield user data events forever, reconnecting with a fresh listenKey whenever needed.

    The first item, and the first after every reconnect, is `UserStreamConnected`. Wrap the
    call in `contextlib.aclosing` so the connection closes as soon as you stop iterating.
    """
    failures = 0
    while True:
        try:
            listen_key = await client.start_user_stream()
            # Never log this URL: it carries the listenKey.
            async with connect(f"{ws_root}/private/ws/{listen_key.reveal()}") as ws:
                failures = 0
                keepalive = asyncio.create_task(_keep_alive(client, keepalive_seconds))
                try:
                    logger.info("User data stream connected")
                    yield UserStreamConnected()
                    async for message in ws:
                        data = json.loads(message)
                        if data.get("e") == "listenKeyExpired":
                            logger.warning("listenKey expired")
                            break
                        yield _parse_or_raw(data)
                finally:
                    keepalive.cancel()
                    with suppress(asyncio.CancelledError):
                        await keepalive
            logger.warning("User data stream closed; reconnecting")
        except (BinanceAPIError, httpx.HTTPError, WebSocketException, OSError, ValueError) as e:
            delay = retry_delays[min(failures, len(retry_delays) - 1)]
            failures += 1
            logger.warning("User data stream failed (%s); retrying in %s s", e, delay)
            await asyncio.sleep(delay)


def _parse_or_raw(data: dict[str, Any]) -> UserEvent:
    event_type = data.get("e", "")
    if event_type == "MARGIN_CALL":
        logger.warning("MARGIN_CALL: %s", data)
    try:
        return parse_user_event(data)
    except (KeyError, TypeError, ValueError, InvalidOperation):
        logger.exception("Could not parse %s event; passing it on unparsed", event_type)
        return RawEvent(event_type, data)


async def _keep_alive(client: BinanceRestClient, interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await client.keepalive_user_stream()
        except (BinanceAPIError, httpx.HTTPError) as e:
            # If the key is gone, Binance sends listenKeyExpired on the connection (verified
            # after DELETE) and the stream reconnects with a new key.
            logger.warning("listenKey keepalive failed: %s", e)
