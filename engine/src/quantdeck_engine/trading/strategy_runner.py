"""One symbol's strategy in `run` (D-36, D-43).

At startup the runner loads the strategy's warm-up candles from REST and feeds them in. The
latest of them is decided on like any later candle, so an exit signal from a candle that
closed while the engine was stopped is still carried out. After that, for every closed
candle from the feed (D-42), it reads the position from REST (D-06), asks the strategy, and
carries out its entry or exit through the trader (D-17, D-18, D-22).

- A halted account or symbol refuses entries, never exits.
- A failed request or an unclear order outcome (D-20) costs the candle it happened on.
- Any other error stops this symbol's strategy, never the engine, so the kill switch,
  monitoring, and exchange-side stops carry on. So does a limit entry, which only the
  backtest can fill so far (D-45).

Every decision is appended to a CSV record, which a backtest replay of the same candles is
checked against (D-36, D-38). A restart inside a candle decides on that candle again, so it
can appear twice. Each strategy setting has its own file, named after the strategy and
every parameter, so a replay never mixes two settings (D-47).
"""

import asyncio
import csv
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from quantdeck_engine.binance.rest import REQUEST_ERRORS
from quantdeck_engine.binance.streams import Kline
from quantdeck_engine.config import StrategyConfig
from quantdeck_engine.strategy.base import Action, Enter, Exit, Strategy
from quantdeck_engine.times import iso_utc, utc_text
from quantdeck_engine.trading.executor import (
    OrderOutcomeUnclear,
    SymbolPaused,
    SymbolTrader,
    TradingHalted,
)
from quantdeck_engine.trading.position import Position
from quantdeck_engine.trading.sizing import round_stop

logger = logging.getLogger(__name__)

RETRY_DELAY = 5.0  # seconds between position reads while REST fails, as the feed waits (D-42)


class UnsupportedAction(Exception):
    """The strategy asked for something only the backtest can carry out."""


class CandleSource(Protocol):
    """What the runner reads candles from: `ClosedCandleFeed`."""

    async def warm_up(self, count: int) -> list[Kline]: ...

    def candles(self) -> AsyncGenerator[Kline, None]: ...


def action_name(action: Action) -> str:
    """`enter_long`, `enter_short`, or `exit`: the action as clients and the record show it."""
    if isinstance(action, Exit):
        return "exit"
    return f"enter_{action.side.lower()}"


def forward_record_path(directory: Path, symbol: str, strategy: StrategyConfig) -> Path:
    """E.g. `BTCUSDT_4h_donchian_entry_period=55_exit_period=20.csv` (D-47)."""
    params = "".join(f"_{key}={value}" for key, value in strategy.params_text().items())
    return directory / f"{symbol}_{strategy.interval}_{strategy.name}{params}.csv"


class SignalResult(StrEnum):
    """What came of a strategy action, for clients to show without reading `outcome`."""

    ENTERED = "entered"
    CLOSED = "closed"
    SKIPPED_PAUSED = "skipped_paused"  # the user paused the symbol (D-50)
    SKIPPED_HALTED = "skipped_halted"  # the engine halted the symbol or the account
    IGNORED = "ignored"  # an exit with no position, or an entry with one open
    NOT_ENTERED = "not_entered"  # sized out, not filled, or closed for want of a stop
    FAILED = "failed"  # a request error or an unclear order outcome (D-20)


@dataclass(frozen=True)
class Signal:
    """A strategy action and what came of it."""

    candle_open_time: int  # ms
    action: str  # see `action_name`
    stop_price: Decimal | None  # an entry's stop as the trader places it, on the tick
    result: SignalResult
    outcome: str  # text for the user, as in the record


class ForwardRecord:
    """One CSV row per candle the strategy decided on (D-43). Never stops trading: a failed
    write is logged and the row is lost."""

    COLUMNS = (
        "open_time",
        "open_time_utc",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "position_side",
        "position_quantity",
        "position_entry_price",
        "action",
        "stop_price",
        "outcome",
        "decided_at_utc",
    )

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self._clock = clock

    def append(
        self,
        candle: Kline,
        position: Position | None,
        action: Action | None,
        outcome: str,
    ) -> None:
        row = [
            candle.open_time,
            utc_text(candle.open_time),
            _text(candle.open),
            _text(candle.high),
            _text(candle.low),
            _text(candle.close),
            _text(candle.volume),
            "" if position is None else position.side,
            "" if position is None else _text(position.quantity),
            "" if position is None else _text(position.entry_price),
            "" if action is None else action_name(action),
            _text(action.stop_price) if isinstance(action, Enter) else "",
            outcome,
            iso_utc(self._clock()),
        ]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            new = not self.path.is_file() or self.path.stat().st_size == 0
            with self.path.open("a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if new:
                    writer.writerow(self.COLUMNS)
                writer.writerow(row)
        except OSError as e:
            # On Windows the append fails while another program (a spreadsheet) has it open.
            logger.warning("Forward test record %s not written: %s", self.path, e)


class StrategyRunner:
    def __init__(
        self,
        strategy: Strategy,
        trader: SymbolTrader,
        feed: CandleSource,
        record: ForwardRecord | None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        retry_delay: float = RETRY_DELAY,
    ) -> None:
        self.symbol = trader.symbol
        self.strategy = strategy
        self._trader = trader
        self._feed = feed
        self._record = record
        self._sleep = sleep
        self._retry_delay = retry_delay
        self._pending: Kline | None = None  # the latest warm-up candle, decided on in `run`
        # What clients see (D-43)
        self.last_candle: Kline | None = None  # the last candle decided on
        self.last_signal: Signal | None = None  # the last action and its outcome
        self.stopped: str | None = None  # why the strategy stopped, if it did

    async def warm_up(self) -> None:
        """Load the warm-up candles and feed in all but the latest, which `run` decides on.

        Runs at startup, so request errors propagate and the engine doesn't start (D-31).
        """
        candles = await self._feed.warm_up(self.strategy.warmup)
        # Their actions are discarded, and the strategy's state rests on candles alone (D-36).
        for candle in candles[:-1]:
            self.strategy.on_candle(candle, None)
        if candles:
            self._pending = candles[-1]
            logger.info(
                "%s: %d warm-up candles loaded, through %s UTC",
                self.symbol,
                len(candles),
                utc_text(candles[-1].open_time),
            )

    async def run(self) -> None:
        """Decide on every closed candle until cancelled. An unexpected error stops this
        symbol's strategy only."""
        try:
            if self._pending is not None:
                candle, self._pending = self._pending, None
                await self._decide(candle)
            async with aclosing(self._feed.candles()) as candles:
                async for candle in candles:
                    await self._decide(candle)
        except Exception as e:
            self.stopped = f"{type(e).__name__}: {e}"
            logger.critical(
                "%s: the strategy stopped on an unexpected error; the engine keeps running "
                "and exchange-side stops still protect positions, but nothing enters or exits "
                "on signals until the engine restarts",
                self.symbol,
                exc_info=True,
            )

    async def _decide(self, candle: Kline) -> None:
        position = await self._read_position()
        action = self.strategy.on_candle(candle, position)
        if isinstance(action, Enter) and action.limit_price is not None:
            # The config check refuses such a strategy at startup; this is the backstop. It
            # stops this symbol's strategy, never the engine (D-43).
            raise UnsupportedAction(
                f"limit entry at {_text(action.limit_price)}: the engine places market entries only"
            )
        self.last_candle = candle
        logger.info(
            "%s %s candle %s UTC closed at %s: %s",
            self.symbol,
            candle.interval,
            utc_text(candle.open_time),
            _text(candle.close),
            "no signal" if action is None else _describe(action),
        )
        outcome = ""
        if action is not None:
            result, outcome = await self._carry_out(action, position)
            self.last_signal = Signal(
                candle_open_time=candle.open_time,
                action=action_name(action),
                # The record keeps the strategy's own stop for the replay (D-36); clients see
                # the one the trader places.
                stop_price=round_stop(action.side, action.stop_price, self._trader.rules)
                if isinstance(action, Enter)
                else None,
                result=result,
                outcome=outcome,
            )
        if self._record is not None:
            self._record.append(candle, position, action, outcome)

    async def _read_position(self) -> Position | None:
        """The exchange's position (D-06). Retries while REST fails: deciding without it
        could miss an exit, and skipping the candle would leave a hole in the strategy's
        window (D-42)."""
        attempt = 0
        while True:
            try:
                return await self._trader.position()
            except REQUEST_ERRORS as e:
                attempt += 1
                logger.warning(
                    "%s: position not read from REST (attempt %d: %s); the strategy waits, "
                    "retrying in %.0f s",
                    self.symbol,
                    attempt,
                    e,
                    self._retry_delay,
                )
                await self._sleep(self._retry_delay)

    async def _carry_out(
        self, action: Action, position: Position | None
    ) -> tuple[SignalResult, str]:
        """Carry out the action; returns what came of it, with its outcome for clients and
        the record."""
        try:
            if isinstance(action, Exit):
                if position is None:
                    return SignalResult.IGNORED, "ignored: no position"
                await self._trader.close_position()
                return SignalResult.CLOSED, "closed"
            if position is not None:
                # One entry per position (D-22)
                return SignalResult.IGNORED, "ignored: a position is already open"
            try:
                plan = await self._trader.open_position(action.side, action.stop_price)
            except TradingHalted as e:
                logger.warning("%s: entry signal skipped, trading is halted: %s", self.symbol, e)
                result = (
                    SignalResult.SKIPPED_PAUSED
                    if isinstance(e, SymbolPaused)
                    else SignalResult.SKIPPED_HALTED
                )
                return result, f"skipped, halted: {e}"
            if plan is None:
                return SignalResult.NOT_ENTERED, "not entered; see the log"
            return (
                SignalResult.ENTERED,
                f"entered {_text(plan.quantity)} at ~{_text(plan.reference_price)}",
            )
        except (*REQUEST_ERRORS, OrderOutcomeUnclear) as e:
            logger.error(
                "%s: %s failed: %s; the strategy goes on with the next candle",
                self.symbol,
                action_name(action),
                e,
            )
            return SignalResult.FAILED, f"failed: {e}"


def _describe(action: Action) -> str:
    if isinstance(action, Exit):
        return "exit"
    return f"enter {action.side}, stop {_text(action.stop_price)}"


def _text(amount: Decimal) -> str:
    """Fixed-point, never exponent notation."""
    return format(amount, "f")
