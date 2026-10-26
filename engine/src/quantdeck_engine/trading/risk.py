"""Daily loss limit, entry gate, and kill switch (D-24, D-27, D-29, D-30).

One guard covers the whole account. Today's loss is re-read from income history every time
it is needed (D-30), never accumulated from stream events: the exchange stays the only
source (D-06), and nothing is missed while the stream is disconnected. `RiskChecker` runs
the guard's check whenever the loss may have changed (a position closed, a funding fee, a
stream reconnect) and periodically, since income records may appear a little after a fill.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from quantdeck_engine.binance.rest import REQUEST_ERRORS, BinanceRestClient
from quantdeck_engine.config import TradingConfig
from quantdeck_engine.times import day_start_ms
from quantdeck_engine.trading.balance import BaseBalance, fetch_income
from quantdeck_engine.trading.executor import (
    OrderOutcomeUnclear,
    SymbolTrader,
    TradingHalted,
    is_closing_stop,
)
from quantdeck_engine.trading.halt import HaltRecord, HaltStore
from quantdeck_engine.trading.position import ExchangePosition
from quantdeck_engine.trading.rules import Side
from quantdeck_engine.trading.sizing import (
    TAKER_FEE_RATE,
    DailyLoss,
    EntryRejected,
    daily_limit,
)

logger = logging.getLogger(__name__)

# Income types that count toward today's loss (D-24): trading results and their costs.
COUNTED_INCOME_TYPES = frozenset(
    {
        "REALIZED_PNL",
        "COMMISSION",
        "FUNDING_FEE",
        "SPECIAL_FUNDING_FEE",
        "INSURANCE_CLEAR",  # liquidation clearance fee
        "POSITION_LIMIT_INCREASE_FEE",
    }
)
# Every other type the docs list (2026-10-16): transfers, rebates, rewards, other products.
# Rebates are left out so they can't make room for more losses.
UNCOUNTED_INCOME_TYPES = frozenset(
    {
        "TRANSFER",
        "WELCOME_BONUS",
        "REFERRAL_KICKBACK",
        "COMMISSION_REBATE",
        "API_REBATE",
        "CONTEST_REWARD",
        "CROSS_COLLATERAL_TRANSFER",
        "OPTIONS_PREMIUM_FEE",
        "OPTIONS_SETTLE_PROFIT",
        "INTERNAL_TRANSFER",
        "AUTO_EXCHANGE",
        "DELIVERED_SETTELMENT",  # sic
        "COIN_SWAP_DEPOSIT",
        "COIN_SWAP_WITHDRAW",
        "STRATEGY_UMFUTURES_TRANSFER",
        "FEE_RETURN",
        "BFUSD_REWARD",
    }
)

KILL_ATTEMPTS = 3
KILL_RETRY_DELAY = 2.0  # seconds
CHECK_INTERVAL = 60.0  # seconds; D-30: "about once a minute"
# Income records appeared 1.1 s after a fill on testnet (D-30), so a check run the moment a
# position closes would usually miss its loss.
CHECK_DELAY = 3.0  # seconds
# The fee for closing a position its stop closes, counted in its risk at the entry gate (D-24)
CLOSE_FEE_RATE = TAKER_FEE_RATE


class LiftRefused(Exception):
    """The halt can't be lifted now: today's loss is still at the limit (D-27)."""


def loss_from_income(incomes: Iterable[dict[str, Any]], asset: str) -> Decimal:
    """Today's loss: the net of the counted records for `asset`, negated (D-24).

    Gains offset losses, so the result is negative after a net gain.
    """
    return Decimal(0) - sum(
        (
            Decimal(i["income"])
            for i in incomes
            if i["asset"] == asset and i["incomeType"] in COUNTED_INCOME_TYPES
        ),
        Decimal(0),
    )


class RiskGuard:
    def __init__(
        self,
        client: BinanceRestClient,
        trading: TradingConfig,
        base_balance: BaseBalance,
        store: HaltStore,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._trading = trading
        self._base = base_balance
        self._store = store
        self._clock = clock
        self._sleep = sleep
        self._traders: list[SymbolTrader] = []
        self._entry_lock = asyncio.Lock()
        self._kill_lock = asyncio.Lock()
        self._pending_kill: asyncio.Task[bool] | None = None
        self._recorded = False
        self._unknown_types: set[str] = set()
        self.halt_reason: str | None = None
        self.halted_at: str | None = None  # ISO 8601 UTC, when known

    def attach(self, traders: Sequence[SymbolTrader]) -> None:
        """The traders the kill switch flattens. Each trader takes the guard as its gate, so
        they are attached after construction."""
        self._traders = list(traders)

    @property
    def halted(self) -> bool:
        return self.halt_reason is not None

    async def start(self) -> None:
        """Startup, after each trader's `prepare` (D-27).

        A halt record, or a loss already at the limit, starts the engine halted, and every
        symbol is flattened again in case an earlier kill switch couldn't finish.
        """
        record = self._store.load()
        if record is None:
            await self.check()
            return
        self._recorded = True
        self.halt_reason = record.reason
        self.halted_at = record.halted_at or None
        logger.error(
            "Starting halted: the kill switch fired at %s (%s). "
            "Lift it with `quantdeck-engine lift-halt`.",
            record.halted_at or "an unknown time",
            record.reason,
        )
        async with self._kill_lock:
            await self._flatten()

    async def daily_loss(self) -> DailyLoss:
        base = await self._base.get()
        now_ms = int(self._clock() * 1000)
        incomes = await fetch_income(self._client, day_start_ms(now_ms), now_ms)
        self._warn_unknown(incomes)
        return DailyLoss(
            base=base,
            loss=loss_from_income(incomes, self._base.asset),
            limit=daily_limit(base, self._trading.daily_loss_limit_percent),
        )

    async def check(self) -> DailyLoss:
        """Re-read today's loss and fire the kill switch once it reaches the limit (D-24).

        Never call this while holding a trader's lock: the kill switch takes each one.
        """
        daily = await self.daily_loss()
        logger.debug("Risk check: %s", daily.describe(self._base.asset))
        if daily.reached and not self.halted:
            await self.trip(self._limit_reason(daily))
        return daily

    @asynccontextmanager
    async def entry_slot(self) -> AsyncIterator[None]:
        """One entry at a time across all symbols, from the gate until its stop is placed
        (D-24). Two entries at the same candle close would otherwise both count on the same
        room. Taken inside a trader's lock and never the other way round; the kill switch
        doesn't take it."""
        async with self._entry_lock:
            yield

    async def entry_risk(self, per_trade: Decimal) -> Decimal:
        """The entry gate (D-24): the per-trade risk, capped by the room left under the limit
        once every open position's stop is assumed to fire.

        Runs inside a trader's lock and the entry slot, so at the limit it only schedules the
        kill switch, which then waits for that lock, and refuses the entry. Raises
        `EntryRejected` when open positions leave no room, or one has no stop.
        """
        if self.halted:
            raise TradingHalted(f"kill switch: {self.halt_reason}")
        daily = await self.daily_loss()
        if daily.reached:
            reason = self._limit_reason(daily)
            if self._pending_kill is None or self._pending_kill.done():
                self._pending_kill = asyncio.create_task(self.trip(reason))
                self._pending_kill.add_done_callback(_log_kill_failure)
            raise TradingHalted(f"kill switch: {reason}")
        at_risk = await self.open_risk()
        room = daily.room - at_risk
        if at_risk > 0:
            logger.info(
                "Open positions would lose %.2f %s if every stop fired; %.2f %s left under "
                "the daily loss limit",
                at_risk,
                self._base.asset,
                max(room, Decimal(0)),
                self._base.asset,
            )
        if room <= 0:
            raise EntryRejected(
                f"{daily.describe(self._base.asset)}, and open positions would lose "
                f"{at_risk:.2f} {self._base.asset} at their stops: no room left"
            )
        return min(per_trade, room)

    async def open_risk(self) -> Decimal:
        """What every open position on the account loses if its stop fires (D-24): the loss
        to the stop that fires first, plus the fee to close. Read from the exchange (D-06),
        so it covers every symbol, whichever trader opened it (D-14).

        Raises `EntryRejected` for a position without a stop: its loss has no bound until
        its emergency stop is placed (D-19).
        """
        total = Decimal(0)
        for p in await self._client.positions():
            position = ExchangePosition.from_position_risk(p)
            if position is None:
                continue
            symbol, side = p["symbol"], position.side
            triggers = [
                Decimal(a["triggerPrice"])
                for a in await self._client.open_algo_orders(symbol)
                if is_closing_stop(a, side)
            ]
            if not triggers:
                raise EntryRejected(
                    f"the {symbol} {side} position has no exchange-side stop, so its risk is "
                    "unknown"
                )
            # A long's highest stop fires first as the price falls; a short's lowest.
            first = max(triggers) if side is Side.LONG else min(triggers)
            loss = (position.entry_price - first) * position.quantity * side.sign
            total += max(loss, Decimal(0)) + first * position.quantity * CLOSE_FEE_RATE
        return total

    async def trip(self, reason: str) -> bool:
        """Fire the kill switch (D-27): halt, record the halt, then flatten every symbol.

        Returns True once every symbol is flat with no open orders.
        """
        async with self._kill_lock:
            if self.halt_reason is None:
                self.halt_reason = reason
                self.halted_at = datetime.fromtimestamp(self._clock(), UTC).isoformat(
                    timespec="seconds"
                )
            logger.error(
                "KILL SWITCH: %s. Closing every position and cancelling every order; "
                "trading stays halted until `quantdeck-engine lift-halt`.",
                reason,
            )
            self._record()
            return await self._flatten()

    async def wait_idle(self) -> None:
        """Return once no kill switch is running, so stopping the engine never cuts one short
        (D-34)."""
        async with self._kill_lock:
            pass

    async def lift(self) -> bool:
        """Lift the halt at the user's request (D-27, D-33): delete the record and resume.

        Returns False if there was no halt. Raises `LiftRefused` while today's loss is at the
        limit, since the next check would fire the kill switch again at once, and OSError if
        the record can't be deleted (the engine then stays halted).
        """
        async with self._kill_lock:
            if not self.halted:
                return False
            daily = await self.daily_loss()
            if daily.reached:
                raise LiftRefused(
                    f"{daily.describe(self._base.asset)}: the limit is still reached, so the "
                    "halt can be lifted from the next UTC day"
                )
            self._store.clear()
            logger.warning(
                "Kill-switch halt lifted by the user (fired at %s: %s)",
                self.halted_at or "an unknown time",
                self.halt_reason,
            )
            self.halt_reason = None
            self.halted_at = None
            self._recorded = False
            return True

    def _record(self) -> None:
        if self._recorded or self.halt_reason is None:
            return
        try:
            self._store.save(HaltRecord(reason=self.halt_reason, halted_at=self.halted_at or ""))
        except OSError as e:
            # Never let the record block the close (D-29).
            logger.critical(
                "Could not write the halt record %s (%s). This engine stays halted, but a "
                "restart on a later UTC day would trade again.",
                self._store.path,
                e,
            )
            return
        self._recorded = True

    async def _flatten(self) -> bool:
        pending = list(self._traders)
        for attempt in range(1, KILL_ATTEMPTS + 1):
            if attempt > 1:
                await self._sleep(KILL_RETRY_DELAY)
            failed: list[SymbolTrader] = []
            for trader in pending:
                try:
                    if await trader.flatten():
                        continue
                    logger.warning(
                        "%s: kill switch attempt %d/%d left a position or open orders",
                        trader.symbol,
                        attempt,
                        KILL_ATTEMPTS,
                    )
                except (*REQUEST_ERRORS, OrderOutcomeUnclear) as e:
                    logger.warning(
                        "%s: kill switch attempt %d/%d failed: %s",
                        trader.symbol,
                        attempt,
                        KILL_ATTEMPTS,
                        e,
                    )
                failed.append(trader)
            if not failed:
                logger.info("Kill switch: every symbol is flat with no open orders")
                return True
            pending = failed
        logger.critical(
            "KILL SWITCH COULD NOT FLATTEN %s: close the positions and cancel the orders manually",
            ", ".join(t.symbol for t in pending),
        )
        return False

    def _limit_reason(self, daily: DailyLoss) -> str:
        return f"{daily.describe(self._base.asset)}: the daily loss limit is reached"

    def _warn_unknown(self, incomes: Iterable[dict[str, Any]]) -> None:
        types = {i["incomeType"] for i in incomes}
        for income_type in sorted(
            types - COUNTED_INCOME_TYPES - UNCOUNTED_INCOME_TYPES - self._unknown_types
        ):
            self._unknown_types.add(income_type)
            logger.warning(
                "Income type %s is unknown and not counted toward today's loss; check the docs",
                income_type,
            )


def _log_kill_failure(task: asyncio.Task[bool]) -> None:
    if not task.cancelled() and (error := task.exception()) is not None:
        logger.critical("The kill switch failed: close positions manually", exc_info=error)


class RiskChecker:
    """Runs the guard's check (D-30): once at start, a few seconds after each request, and
    every minute regardless. Requests that arrive close together share one check."""

    def __init__(
        self,
        guard: RiskGuard,
        *,
        interval: float = CHECK_INTERVAL,
        delay: float = CHECK_DELAY,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._guard = guard
        self._interval = interval
        self._delay = delay
        self._sleep = sleep
        self._wake = asyncio.Event()
        self.latest: DailyLoss | None = None

    @property
    def requested(self) -> bool:
        return self._wake.is_set()

    def request(self) -> None:
        """Something may have changed today's loss: check soon."""
        self._wake.set()

    async def run(self) -> None:
        while True:
            self._wake.clear()
            try:
                self.latest = await self._guard.check()
            except REQUEST_ERRORS as e:
                logger.warning("Risk check failed (%s); trying again at the next check", e)
            try:
                async with asyncio.timeout(self._interval):
                    await self._wake.wait()
            except TimeoutError:
                continue
            await self._sleep(self._delay)
