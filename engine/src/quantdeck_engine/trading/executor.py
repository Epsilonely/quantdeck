"""Order placement and protection for one symbol.

- Startup: margin type and leverage (D-16), leftover orders (D-18), and a stop for any
  position found without one (D-19, D-26).
- Entries are sized from the stop (D-22, D-25), filled at market (D-17), and protected by
  an exchange-side `closePosition` stop right after the fill (D-18, D-19).
- An order whose outcome is unknown is looked up by its client ID, never blindly resent
  (D-20). Client IDs encode the order's role (D-21).

Every decision that protects a position re-reads the exchange first (D-06). A symbol whose
trading is halted still closes and protects positions; it only refuses new entries.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import httpx

from quantdeck_engine.binance.rest import (
    NO_NEED_TO_CHANGE_MARGIN_TYPE,
    NO_SUCH_ORDER,
    STOP_WOULD_TRIGGER_CODES,
    BinanceAPIError,
    BinanceRestClient,
    outcome_unknown,
)
from quantdeck_engine.config import SymbolConfig, TradingConfig
from quantdeck_engine.trading.balance import BaseBalance
from quantdeck_engine.trading.order_ids import ClientIdFactory, Role
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

REQUEST_ERRORS = (BinanceAPIError, httpx.HTTPError)


class TradingHalted(Exception):
    """Trading on this symbol was stopped (D-19, D-20, D-16); entries are refused."""


class OrderOutcomeUnclear(Exception):
    """An order's outcome stayed unknown after a lookup and one resend (D-20)."""


class StopNotPlaced(Exception):
    """An exchange-side stop could not be placed or confirmed (D-19)."""


class SymbolTrader:
    def __init__(
        self,
        client: BinanceRestClient,
        config: SymbolConfig,
        trading: TradingConfig,
        rules: SymbolRules,
        base_balance: BaseBalance,
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
        self._ids = ids
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._brackets: list[Bracket] = []
        self._recovering = False
        self.halt_reason: str | None = None

    @property
    def halted(self) -> bool:
        return self.halt_reason is not None

    def halt(self, reason: str) -> None:
        if self.halt_reason is None:
            self.halt_reason = reason
            logger.error("%s: trading halted: %s", self.symbol, reason)

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
                    f"configured {MARGIN_TYPE} {self._config.leverage}x (D-16)"
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
            if not self._brackets:
                await self._load_brackets()
            if await self.position() is not None:
                # One entry per position; adding to a position is out of scope.
                logger.warning("%s: entry skipped: a position is already open", self.symbol)
                return None

            base = await self._base.get()
            try:
                plan = size_entry(
                    side=side,
                    reference_price=await self._client.mark_price(self.symbol),
                    stop_price=stop_price,
                    risk_amount=risk_amount(base, self._trading.risk_per_trade_percent),
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

    async def close_position(self, role: Role = Role.EXIT) -> None:
        """Close the whole position with reduceOnly market orders, then cancel its stop."""
        async with self._lock:
            await self._close(role)

    async def on_position_closed(self) -> None:
        """A user data event says the position closed: cancel its leftover stop (D-18)."""
        async with self._lock:
            if await self.position() is None:
                await self._cancel_leftovers()

    # Exchange reads

    async def position(self) -> dict[str, Any] | None:
        """This symbol's open position from REST (one-way mode: positionSide BOTH)."""
        for p in await self._client.positions(self.symbol):
            if p["positionSide"] == "BOTH" and Decimal(p["positionAmt"]) != 0:
                return p
        return None

    async def open_stops(self, side: Side) -> list[dict[str, Any]]:
        """Open closePosition stops that would close a position on `side`."""
        return [
            a
            for a in await self._client.open_algo_orders(self.symbol)
            if a["orderType"] == "STOP_MARKET"
            and a["closePosition"]
            and a["side"] == side.close_order_side
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
            await self._client.change_leverage(self.symbol, self._config.leverage)
            logger.info("%s: leverage set to %dx", self.symbol, self._config.leverage)

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

    async def _ensure_stop(self, position: dict[str, Any]) -> None:
        amount = Decimal(position["positionAmt"])
        side = Side.LONG if amount > 0 else Side.SHORT
        stops = await self.open_stops(side)
        if stops:
            logger.info(
                "%s: adopted %s position %s with stop %s at %s",
                self.symbol,
                side,
                amount,
                stops[0]["clientAlgoId"],
                stops[0]["triggerPrice"],
            )
            return
        risk = risk_amount(await self._base.get(), self._trading.risk_per_trade_percent)
        stop = emergency_stop_price(
            side, Decimal(position["entryPrice"]), amount, risk, self._rules
        )
        logger.error(
            "%s: %s position %s has no exchange-side stop; placing an emergency stop at %s (D-26)",
            self.symbol,
            side,
            amount,
            stop,
        )
        liquidation = Decimal(position["liquidationPrice"])
        await self._protect(side, stop, liquidation=liquidation or None)

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
        logger.error("%s: %s; closing the position (D-19)", self.symbol, problem)
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

    async def _close(self, role: Role) -> None:
        position = await self.position()
        if position is None:
            return
        amount = Decimal(position["positionAmt"])
        side = Side.LONG if amount > 0 else Side.SHORT
        remaining = abs(amount)
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
        logger.info("%s: closed %s position %s", self.symbol, side, amount)
        if await self.position() is None:
            await self._cancel_leftovers()

    # Unknown outcomes (D-20)

    async def _submit(
        self,
        send: Callable[[], Awaitable[dict[str, Any]]],
        client_id: str,
        what: str,
    ) -> dict[str, Any]:
        reason = f"{what} {client_id} outcome still unknown after a resend (D-20)"
        for attempt in (1, 2):
            try:
                return await send()
            except REQUEST_ERRORS as e:
                if not outcome_unknown(e):
                    raise
                logger.warning(
                    "%s: %s %s outcome unknown (%s); looking it up (D-20)",
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
