"""Order placement and protection for one symbol.

- Startup: margin type and leverage (D-16), leftover orders (D-18), and a stop for any
  position found without one (D-19, D-26).
- Entries are sized from the stop (D-22, D-25), filled at market (D-17), and protected by
  an exchange-side `closePosition` stop right after the fill (D-18, D-19).
- An order whose outcome is unknown is looked up by its client ID, never blindly resent
  (D-20). Client IDs encode the order's role (D-21).
- Each entry's risk is capped by an account-wide gate: the room left under the daily loss
  limit once every open position's stop is counted (D-24). Entries on all symbols take
  turns through the gate's entry slot. The kill switch flattens each symbol through
  `flatten` (D-27).
- A client's settings change holds the symbol's lock while it is flat (`flat`), so no
  entry runs under the old settings once the new ones are saved (D-47).
- The user can pause a symbol (D-50): like a halt, it refuses entries only.

Every decision that protects a position re-reads the exchange first (D-06). A symbol whose
trading is halted or paused still closes and protects positions; it only refuses new
entries.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from decimal import Decimal
from typing import Any, Protocol

from quantdeck_engine.binance.rest import (
    NO_NEED_TO_CHANGE_MARGIN_TYPE,
    NO_SUCH_ORDER,
    REQUEST_ERRORS,
    STOP_WOULD_TRIGGER_CODES,
    BinanceAPIError,
    BinanceRestClient,
    outcome_unknown,
)
from quantdeck_engine.config import SymbolConfig, TradingConfig
from quantdeck_engine.trading.balance import BaseBalance
from quantdeck_engine.trading.order_ids import ClientIdFactory, Role
from quantdeck_engine.trading.position import ExchangePosition
from quantdeck_engine.trading.rules import Side, SymbolRules
from quantdeck_engine.trading.sizing import (
    Bracket,
    EntryPlan,
    EntryRejected,
    emergency_stop_price,
    risk_amount,
    size_entry,
)

logger = logging.getLogger(__name__)

MARGIN_TYPE = "ISOLATED"  # D-16
STOP_ATTEMPTS = 3  # D-19: "a few times within a few seconds"
STOP_RETRY_DELAY = 1.0  # seconds
LOOKUP_DELAY = 1.0  # D-20: wait briefly before looking an order up
FINAL_ORDER_STATUSES = {"FILLED", "CANCELED", "EXPIRED", "EXPIRED_IN_MATCH", "REJECTED"}


class TradingHalted(Exception):
    """Entries are refused: this symbol was stopped (D-16, D-19, D-20) or paused by the user
    (D-50), or the account was halted by the kill switch (D-27)."""


class SymbolPaused(TradingHalted):
    """Entries are refused because the user paused this symbol (D-50)."""


class OrderOutcomeUnclear(Exception):
    """An order's outcome stayed unknown after a lookup and one resend (D-20)."""


class StopNotPlaced(Exception):
    """An exchange-side stop could not be placed or confirmed (D-19)."""


class PositionOpen(Exception):
    """The symbol has an open position, so its settings can't change (D-47)."""


class EntryGate(Protocol):
    def entry_slot(self) -> AbstractAsyncContextManager[None]:
        """Held by one entry at a time across all symbols, from the gate until the entry's
        stop is placed, so the next entry's gate sees it as an open position (D-24)."""
        ...

    async def entry_risk(self, per_trade: Decimal) -> Decimal:
        """The risk amount an entry may take, at most `per_trade` (D-24).

        Called while the trader holds its lock and the entry slot, so it must not close or
        cancel anything. Raises `TradingHalted` when no entry may be made, and
        `EntryRejected` when this one must be skipped.
        """
        ...


def is_closing_stop(order: dict[str, Any], side: Side) -> bool:
    """An open algo order that is a closePosition stop for a position on `side` (D-18)."""
    return (
        order["orderType"] == "STOP_MARKET"
        and order["closePosition"]
        and order["side"] == side.close_order_side
    )


class SymbolTrader:
    def __init__(
        self,
        client: BinanceRestClient,
        config: SymbolConfig,
        trading: TradingConfig,
        rules: SymbolRules,
        base_balance: BaseBalance,
        gate: EntryGate,
        ids: ClientIdFactory,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.symbol = config.symbol
        self._client = client
        self._config = config
        self._trading = trading
        self._rules = rules
        self._base = base_balance
        self._gate = gate
        self._ids = ids
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._brackets: list[Bracket] = []
        self._recovering = False
        self.halt_reason: str | None = None  # the engine's own halt (D-16, D-19, D-20)
        self.paused_at: str | None = None  # the user's pause (D-50), ISO 8601 UTC

    @property
    def halted(self) -> bool:
        return self.halt_reason is not None

    @property
    def rules(self) -> SymbolRules:
        return self._rules

    def halt(self, reason: str) -> None:
        if self.halt_reason is None:
            self.halt_reason = reason
            logger.error("%s: trading halted: %s", self.symbol, reason)

    def pause(self, since: str) -> None:
        """The user's pause (D-50): refuse entries from now on. Exits, stops, and the kill
        switch carry on. An entry already holding the lock finishes; `idle` waits for it."""
        self.paused_at = since

    def resume(self) -> None:
        """Lift the user's pause. The engine's own halt, if any, stays."""
        self.paused_at = None

    # Public actions. Each holds the lock, so they never interleave on one symbol.

    async def prepare(self) -> None:
        """Bring the symbol to a known state at startup (D-06, D-16, D-18, D-19, D-26)."""
        async with self._lock:
            await self._load_brackets()
            position = await self.position()
            current = await self._client.symbol_config(self.symbol)
            if position is None:
                await self._cancel_leftovers()
                await self._apply_account_settings(current)
                return
            if (
                current["marginType"] != MARGIN_TYPE
                or int(current["leverage"]) != self._config.leverage
            ):
                self.halt(
                    f"position open under {current['marginType']} {current['leverage']}x; "
                    f"configured {MARGIN_TYPE} {self._config.leverage}x"
                )
            await self._ensure_stop(position)

    async def open_position(self, side: Side, stop_price: Decimal) -> EntryPlan | None:
        """Enter at market and protect the fill with an exchange-side stop.

        Returns the plan once the position is protected, or None if the entry was skipped,
        didn't fill, or had to be closed because its stop couldn't be placed.
        """
        async with self._lock:
            if self.halted:
                raise TradingHalted(f"{self.symbol}: {self.halt_reason}")
            if self.paused_at is not None:
                raise SymbolPaused(f"{self.symbol}: paused by the user since {self.paused_at}")
            if not self._brackets:
                await self._load_brackets()
            if await self.position() is not None:
                # One entry per position; adding to a position is out of scope.
                logger.warning("%s: entry skipped: a position is already open", self.symbol)
                return None
            async with self._gate.entry_slot():
                return await self._enter(side, stop_price)

    async def _enter(self, side: Side, stop_price: Decimal) -> EntryPlan | None:
        base = await self._base.get()
        per_trade = risk_amount(base, self._trading.risk_per_trade_percent)
        try:
            risk = await self._gate.entry_risk(per_trade)
            if risk < per_trade:
                logger.info(
                    "%s: entry risk capped at %s %s by the room left under the daily loss limit",
                    self.symbol,
                    risk.quantize(Decimal("0.01")),
                    self._base.asset,
                )
            plan = size_entry(
                side=side,
                reference_price=await self._client.mark_price(self.symbol),
                stop_price=stop_price,
                risk_amount=risk,
                base_balance=base,
                max_notional_multiple=self._trading.max_notional_multiple,
                available_balance=await self._available_balance(),
                leverage=self._config.leverage,
                rules=self._rules,
                brackets=self._brackets,
            )
        except EntryRejected as e:
            logger.warning("%s: entry skipped: %s", self.symbol, e)
            return None

        logger.info(
            "%s: entering %s %s at ~%s, stop %s, risk %s %s, est. liquidation %.2f%s",
            self.symbol,
            side,
            plan.quantity,
            plan.reference_price,
            plan.stop_price,
            plan.risk.quantize(Decimal("0.01")),
            self._base.asset,
            plan.liquidation_price,
            f" (trimmed by {plan.trimmed_by})" if plan.trimmed_by else "",
        )
        client_id = self._ids.new(Role.ENTRY, self.symbol)
        try:
            result = await self._submit(
                lambda: self._client.market_order(
                    self.symbol,
                    side.entry_order_side,
                    plan.quantity,
                    client_id,
                    reduce_only=False,
                ),
                client_id,
                "entry",
            )
        except BinanceAPIError as e:
            # A definite rejection (4xx): nothing was placed.
            logger.warning("%s: entry %s rejected: %s", self.symbol, client_id, e)
            return None
        filled = Decimal(result["executedQty"])
        if filled == 0:
            if result["status"] not in FINAL_ORDER_STATUSES:
                # Found by lookup while still working: it may fill after we look away.
                self.halt(f"entry {client_id} still {result['status']} after lookup")
                await self._protect_any_position()
                return None
            logger.warning(
                "%s: entry %s did not fill (status %s)",
                self.symbol,
                client_id,
                result["status"],
            )
            return None
        if result["status"] != "FILLED":
            logger.warning(
                "%s: entry %s %s, filled %s of %s",
                self.symbol,
                client_id,
                result["status"],
                filled,
                plan.quantity,
            )
        # closePosition covers whatever filled, partial or not.
        if not await self._protect(side, plan.stop_price, liquidation=None):
            return None
        return plan

    async def close_position(self) -> ExchangePosition | None:
        """Close the whole position with reduceOnly market orders, then cancel its stop.
        Returns the position it closed, or None if there was none."""
        async with self._lock:
            return await self._close(Role.EXIT)

    async def on_position_closed(self) -> None:
        """A user data event says the position closed: cancel its leftover stop (D-18)."""
        async with self._lock:
            if await self.position() is None:
                await self._cancel_leftovers()

    async def idle(self) -> None:
        """Return once nothing holds this symbol's lock, so an entry that started before a
        pause has finished (D-50)."""
        async with self._lock:
            pass

    # Settings changes (D-47). The methods after `flat` are called inside it.

    @asynccontextmanager
    async def flat(self) -> AsyncIterator[None]:
        """Hold this symbol's lock while the exchange shows no position, so no entry can
        start until the block ends. Raises `PositionOpen` if there is a position."""
        async with self._lock:
            position = await self.position()
            if position is not None:
                raise PositionOpen(
                    f"{self.symbol} has an open {position.side} position {position.quantity}"
                )
            yield

    async def set_leverage(self, leverage: int) -> None:
        """Change the leverage on the exchange; `reconfigure` makes entries use it."""
        await self._client.change_leverage(self.symbol, leverage)
        logger.info("%s: leverage set to %dx", self.symbol, leverage)

    def reconfigure(self, config: SymbolConfig) -> None:
        if config.symbol != self.symbol:
            raise ValueError(f"{config.symbol} settings given to the {self.symbol} trader")
        self._config = config

    async def clear_leftovers(self) -> None:
        """Cancel any order left on the symbol, as at startup."""
        await self._cancel_leftovers()

    async def flatten(self) -> bool:
        """Kill switch (D-27): close the position, then cancel every order on the symbol.

        Orders are cancelled only once the position is gone, so its stop keeps protecting it
        until then. Returns True when the symbol is flat with no open orders.
        """
        async with self._lock:
            await self._close(Role.KILL)
            if await self.position() is not None:
                return False
            await self._client.cancel_all_open_orders(self.symbol)
            await self._client.cancel_all_algo_open_orders(self.symbol)
            left = await self._client.open_orders(self.symbol)
            left += await self._client.open_algo_orders(self.symbol)
            return not left

    # Exchange reads

    async def position(self) -> ExchangePosition | None:
        """This symbol's open position from REST, or None when it is flat."""
        for p in await self._client.positions(self.symbol):
            if (position := ExchangePosition.from_position_risk(p)) is not None:
                return position
        return None

    async def open_stops(self, side: Side) -> list[dict[str, Any]]:
        """Open closePosition stops that would close a position on `side`."""
        return [
            a for a in await self._client.open_algo_orders(self.symbol) if is_closing_stop(a, side)
        ]

    async def _available_balance(self) -> Decimal:
        for b in await self._client.balances():
            if b["asset"] == self._base.asset:
                return Decimal(b["availableBalance"])
        return Decimal(0)

    async def _load_brackets(self) -> None:
        raw = await self._client.leverage_brackets(self.symbol)
        self._brackets = [Bracket.from_api(b) for b in raw]

    # Startup helpers

    async def _apply_account_settings(self, current: dict[str, Any]) -> None:
        if current["marginType"] != MARGIN_TYPE:
            try:
                await self._client.change_margin_type(self.symbol, MARGIN_TYPE)
                logger.info("%s: margin type set to %s", self.symbol, MARGIN_TYPE)
            except BinanceAPIError as e:
                if e.code != NO_NEED_TO_CHANGE_MARGIN_TYPE:
                    raise
        if int(current["leverage"]) != self._config.leverage:
            await self.set_leverage(self._config.leverage)

    async def _cancel_leftovers(self) -> None:
        """With no position, any open order on the symbol is a leftover (D-14, D-17, D-18)."""
        for a in await self._client.open_algo_orders(self.symbol):
            await self._client.cancel_algo_order(a["clientAlgoId"])
            logger.warning(
                "%s: cancelled leftover %s %s %s",
                self.symbol,
                a["orderType"],
                a["side"],
                a["clientAlgoId"],
            )
        for o in await self._client.open_orders(self.symbol):
            await self._client.cancel_order(self.symbol, o["clientOrderId"])
            logger.warning(
                "%s: cancelled leftover %s %s %s",
                self.symbol,
                o["type"],
                o["side"],
                o["clientOrderId"],
            )

    # Protection (D-19, D-26)

    async def _ensure_stop(self, position: ExchangePosition) -> None:
        side = position.side
        stops = await self.open_stops(side)
        if stops:
            logger.info(
                "%s: adopted %s position %s with stop %s at %s",
                self.symbol,
                side,
                position.quantity,
                stops[0]["clientAlgoId"],
                stops[0]["triggerPrice"],
            )
            return
        risk = risk_amount(await self._base.get(), self._trading.risk_per_trade_percent)
        stop = emergency_stop_price(
            side, position.entry_price, position.quantity, risk, self._rules
        )
        logger.error(
            "%s: %s position %s has no exchange-side stop; placing an emergency stop at %s",
            self.symbol,
            side,
            position.quantity,
            stop,
        )
        await self._protect(side, stop, liquidation=position.liquidation_price)

    async def _protect(self, side: Side, stop: Decimal, *, liquidation: Decimal | None) -> bool:
        """Place the stop, or close the position if it can't be (D-19). True if protected."""
        try:
            mark: Decimal | None = await self._client.mark_price(self.symbol)
        except REQUEST_ERRORS as e:
            # Don't let a failed price read leave the position bare: place the stop anyway;
            # the exchange rejects one that would trigger immediately.
            logger.warning("%s: mark price unavailable (%s); placing the stop", self.symbol, e)
            mark = None
        problem = None
        if mark is not None and (mark - stop) * side.sign <= 0:
            problem = f"stop {stop} is already crossed (mark price {mark})"
        elif liquidation is not None and (liquidation - stop) * side.sign >= 0:
            problem = f"stop {stop} is beyond the liquidation price {liquidation}"
        else:
            try:
                await self._place_stop(side, stop)
                return True
            except StopNotPlaced as e:
                problem = str(e)
        logger.error("%s: %s; closing the position", self.symbol, problem)
        self.halt(problem)
        try:
            await self._close(Role.EXIT)
        except (*REQUEST_ERRORS, OrderOutcomeUnclear):
            logger.critical(
                "%s: COULD NOT CLOSE AN UNPROTECTED POSITION; close it manually",
                self.symbol,
                exc_info=True,
            )
        return False

    async def _place_stop(self, side: Side, trigger: Decimal) -> None:
        client_algo_id = self._ids.new(Role.STOP, self.symbol)
        last_error: Exception | None = None
        for attempt in range(1, STOP_ATTEMPTS + 1):
            if attempt > 1:
                await self._sleep(STOP_RETRY_DELAY)
                # The failed request may have gone through anyway.
                if await self._stop_is_open(client_algo_id):
                    return
            try:
                await self._client.stop_market_close_position(
                    self.symbol, side.close_order_side, trigger, client_algo_id
                )
                logger.info("%s: stop %s placed at %s", self.symbol, client_algo_id, trigger)
                return
            except REQUEST_ERRORS as e:
                if isinstance(e, BinanceAPIError) and e.code in STOP_WOULD_TRIGGER_CODES:
                    raise StopNotPlaced(f"stop {trigger} would trigger immediately ({e})") from e
                last_error = e
                logger.warning(
                    "%s: stop attempt %d/%d failed: %s", self.symbol, attempt, STOP_ATTEMPTS, e
                )
        if await self._stop_is_open(client_algo_id):
            return
        raise StopNotPlaced(
            f"stop {trigger} not confirmed after {STOP_ATTEMPTS} attempts: {last_error}"
        )

    async def _stop_is_open(self, client_algo_id: str) -> bool:
        try:
            orders = await self._client.open_algo_orders(self.symbol)
        except REQUEST_ERRORS as e:
            logger.warning("%s: could not check open algo orders: %s", self.symbol, e)
            return False
        if any(a["clientAlgoId"] == client_algo_id for a in orders):
            logger.info("%s: stop %s found open after all", self.symbol, client_algo_id)
            return True
        return False

    # Closing

    async def _close(self, role: Role) -> ExchangePosition | None:
        position = await self.position()
        if position is None:
            return None
        side = position.side
        remaining = position.quantity
        while remaining > 0:
            quantity = min(remaining, self._rules.max_qty)
            client_id = self._ids.new(role, self.symbol)
            try:
                result = await self._submit(
                    lambda q=quantity, c=client_id: self._client.market_order(
                        self.symbol, side.close_order_side, q, c, reduce_only=True
                    ),
                    client_id,
                    "close",
                )
            except BinanceAPIError:
                # A stop may have closed the position first; reduceOnly then rejects.
                if await self.position() is None:
                    break
                raise
            filled = Decimal(result["executedQty"])
            if filled == 0:
                raise OrderOutcomeUnclear(f"close {client_id} did not fill ({result['status']})")
            remaining -= filled
        logger.info("%s: closed %s position %s", self.symbol, side, position.quantity)
        if await self.position() is None:
            await self._cancel_leftovers()
        return position

    # Unknown outcomes (D-20)

    async def _submit(
        self,
        send: Callable[[], Awaitable[dict[str, Any]]],
        client_id: str,
        what: str,
    ) -> dict[str, Any]:
        reason = f"{what} {client_id} outcome still unknown after a resend"
        for attempt in (1, 2):
            try:
                return await send()
            except REQUEST_ERRORS as e:
                if not outcome_unknown(e):
                    raise
                logger.warning(
                    "%s: %s %s outcome unknown (%s); looking it up",
                    self.symbol,
                    what,
                    client_id,
                    e,
                )
            await self._sleep(LOOKUP_DELAY)
            try:
                found = await self._client.query_order(self.symbol, client_id)
            except REQUEST_ERRORS as e:
                if not (isinstance(e, BinanceAPIError) and e.code == NO_SUCH_ORDER):
                    reason = f"{what} {client_id} outcome unknown and its lookup failed: {e}"
                    break
                found = None
            if found is not None:
                logger.info("%s: %s %s found: %s", self.symbol, what, client_id, found["status"])
                return found
            if attempt == 1:
                logger.warning(
                    "%s: %s %s not found; resending once with the same ID",
                    self.symbol,
                    what,
                    client_id,
                )
        # Still unclear: stop trading the symbol and resync from the exchange, which
        # protects any position left without a stop.
        self.halt(reason)
        await self._protect_any_position()
        raise OrderOutcomeUnclear(reason)

    async def _protect_any_position(self) -> None:
        """After an unclear outcome, make sure whatever position exists has a stop."""
        # A close inside this recovery can hit another unclear outcome; don't recurse.
        if self._recovering:
            return
        self._recovering = True
        try:
            position = await self.position()
            if position is not None:
                await self._ensure_stop(position)
        except REQUEST_ERRORS:
            logger.critical(
                "%s: could not check the position after an unclear order; check it manually",
                self.symbol,
                exc_info=True,
            )
        finally:
            self._recovering = False
