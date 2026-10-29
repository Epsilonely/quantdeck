"""User data stream for Binance USDⓈ-M Futures: order and account events for this account.

Behavior verified on testnet is recorded in docs/memory-bank/binanceNotes.md. In short:

- An account has one listenKey, and POST returns the active one again, so every process on
  the account shares it. Deleting it would cut them all off, so this module never does; an
  unused key expires 60 minutes after its last POST or PUT.
- Connecting with an invalid key succeeds and then stays silent, so every connection uses
  a key fetched right before it.
- Events sent while disconnected are lost. Every (re)connect yields `UserStreamConnected`,
  and the consumer must then refresh positions, balances, and open orders from REST.

Only the fields the engine reads are parsed. Each is required, and a missing one turns the
whole event into a `RawEvent` that changes no state, so parsing a field nothing reads would
only add ways to lose an event.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from websockets.exceptions import WebSocketException

from quantdeck_engine.binance.rest import REQUEST_ERRORS, BinanceRestClient
from quantdeck_engine.binance.streams import connect_stream

logger = logging.getLogger(__name__)

KEEPALIVE_SECONDS = 30 * 60  # a listenKey expires 60 minutes after its last POST or PUT
RETRY_DELAYS = (1, 2, 5, 10, 30, 60)  # seconds between failed attempts; the last one repeats


@dataclass(frozen=True)
class UserStreamConnected:
    """The stream (re)connected. Anything sent while disconnected is lost: refresh from REST."""


@dataclass(frozen=True)
class OrderUpdate:
    """`ORDER_TRADE_UPDATE`: an order was created or its status changed."""

    symbol: str
    client_order_id: str
    side: str  # BUY, SELL
    order_type: str  # LIMIT, MARKET, STOP_MARKET, ..., LIQUIDATION
    execution_type: str  # NEW, TRADE, CANCELED, EXPIRED, CALCULATED, AMENDMENT
    status: str  # NEW, PARTIALLY_FILLED, FILLED, CANCELED, EXPIRED, EXPIRED_IN_MATCH
    reduce_only: bool
    quantity: Decimal
    average_price: Decimal
    filled_quantity: Decimal  # accumulated


@dataclass(frozen=True)
class BalanceChange:
    asset: str
    wallet_balance: Decimal


@dataclass(frozen=True)
class PositionChange:
    symbol: str
    amount: Decimal  # negative for a short position
    entry_price: Decimal
    position_side: str


@dataclass(frozen=True)
class AccountUpdate:
    """`ACCOUNT_UPDATE`: balances or positions changed."""

    reason: str  # ORDER, FUNDING_FEE, DEPOSIT, ...
    balances: tuple[BalanceChange, ...]
    positions: tuple[PositionChange, ...]  # only symbols whose position changed


@dataclass(frozen=True)
class AlgoUpdate:
    """`ALGO_UPDATE`: an algo (conditional) order such as an exchange-side stop changed.

    Statuses: NEW (waiting for its trigger), CANCELED, TRIGGERING (sent to the matching
    engine), TRIGGERED (placed there), FINISHED (that order filled or was canceled),
    REJECTED, EXPIRED.
    """

    symbol: str
    client_algo_id: str
    order_type: str  # STOP_MARKET, TAKE_PROFIT, ...
    side: str
    status: str
    trigger_price: Decimal
    close_position: bool
    reject_reason: str  # why the algo order failed, if it did


@dataclass(frozen=True)
class RawEvent:
    """An event this module doesn't parse, or failed to; `data` is the payload as received."""

    event_type: str
    data: dict[str, Any]


UserEvent = UserStreamConnected | OrderUpdate | AccountUpdate | AlgoUpdate | RawEvent


def parse_order_update(data: dict[str, Any]) -> OrderUpdate:
    o = data["o"]
    return OrderUpdate(
        symbol=o["s"],
        client_order_id=o["c"],
        side=o["S"],
        order_type=o["o"],
        execution_type=o["x"],
        status=o["X"],
        reduce_only=o["R"],
        quantity=Decimal(o["q"]),
        average_price=Decimal(o["ap"]),
        filled_quantity=Decimal(o["z"]),
    )


def parse_account_update(data: dict[str, Any]) -> AccountUpdate:
    a = data["a"]
    return AccountUpdate(
        reason=a["m"],
        balances=tuple(
            BalanceChange(
                asset=b["a"],
                wallet_balance=Decimal(b["wb"]),
            )
            for b in a.get("B", [])
        ),
        positions=tuple(
            PositionChange(
                symbol=p["s"],
                amount=Decimal(p["pa"]),
                entry_price=Decimal(p["ep"]),
                position_side=p["ps"],
            )
            for p in a.get("P", [])
        ),
    )


def parse_algo_update(data: dict[str, Any]) -> AlgoUpdate:
    o = data["o"]
    return AlgoUpdate(
        symbol=o["s"],
        client_algo_id=o["caid"],
        order_type=o["o"],
        side=o["S"],
        status=o["X"],
        trigger_price=Decimal(o["tp"]),
        close_position=o["cp"],
        reject_reason=o.get("rm", ""),
    )


PARSERS: dict[str, Callable[[dict[str, Any]], UserEvent]] = {
    "ORDER_TRADE_UPDATE": parse_order_update,
    "ACCOUNT_UPDATE": parse_account_update,
    "ALGO_UPDATE": parse_algo_update,
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
            async with connect_stream(f"{ws_root}/private/ws/{listen_key.reveal()}") as ws:
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
        except (*REQUEST_ERRORS, WebSocketException, OSError, ValueError) as e:
            delay = retry_delays[min(failures, len(retry_delays) - 1)]
            failures += 1
            logger.warning("User data stream failed (%s); retrying in %s s", e, delay)
            await asyncio.sleep(delay)


def _parse_or_raw(data: dict[str, Any]) -> UserEvent:
    event_type = data.get("e", "")
    # Payloads as received, for checking them against the docs (binanceNotes.md). Encoded only
    # when DEBUG is on, since every event passes here.
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("User data event: %s", json.dumps(data, separators=(",", ":")))
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
        except REQUEST_ERRORS as e:
            # If the key is gone, Binance sends listenKeyExpired on the connection (verified
            # after DELETE) and the stream reconnects with a new key.
            logger.warning("listenKey keepalive failed: %s", e)
