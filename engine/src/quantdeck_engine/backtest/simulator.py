"""The backtest simulator: a strategy run over historical candles with the live sizing and
risk rules (D-37) and the fill model of D-40.

- A strategy decides at a candle's close. Its entry or exit fills at the next candle's open,
  moved against the trade by the market slippage. Entries are sized at the next candle's
  mark price open, as live entries are sized at the mark price (D-22).
- Stops trigger on the mark price (D-18): when a candle's mark price range reaches the stop.
  They fill at the stop, or at the candle's open if the mark price opened past it, moved
  against the trade by the stop slippage. A fill past the estimated liquidation price is
  cut at that price.
- Every fill pays the taker fee.
- Funding settles while a position is open: quantity × mark price × rate, longs paying a
  positive rate. A funding at a candle's open time settles before that candle's fills, as
  a live order sent a second after the close comes after it. With candles longer than the
  funding interval, a funding inside a candle settles at its open.
- The day's loss (D-23, D-24) is realized PnL, fees, and funding since 00:00 UTC, against
  the wallet balance at 00:00 UTC. When it reaches the limit, the position closes at once
  and no entry is taken until the next UTC day (D-37).
"""

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

from quantdeck_engine.backtest.data import FundingRate
from quantdeck_engine.binance.streams import INTERVAL_MS, Kline
from quantdeck_engine.config import TradingConfig
from quantdeck_engine.strategy.base import Enter, Exit, Position, Strategy
from quantdeck_engine.times import day_start_ms
from quantdeck_engine.trading.rules import Side, SymbolRules
from quantdeck_engine.trading.sizing import (
    Bracket,
    DailyLoss,
    EntryRejected,
    daily_limit,
    risk_amount,
    size_entry,
)


@dataclass(frozen=True)
class Costs:
    taker_fee: Decimal  # fraction of the notional
    market_slippage: Decimal  # fraction of the price, against the trade
    stop_slippage: Decimal


@dataclass(frozen=True)
class Market:
    """What sizing needs to know about the symbol (D-22, D-25)."""

    rules: SymbolRules
    brackets: list[Bracket]
    leverage: int


@dataclass
class Trade:
    side: Side
    quantity: Decimal
    entry_time: int  # ms
    entry_price: Decimal
    stop_price: Decimal
    risk: Decimal  # the planned loss at the stop (D-22)
    liquidation_price: Decimal  # estimated at entry
    exit_time: int = 0
    exit_price: Decimal = Decimal(0)
    exit_reason: str = ""  # stop, signal, kill, liquidation, end
    pnl: Decimal = Decimal(0)  # from the price move
    fees: Decimal = Decimal(0)
    funding: Decimal = Decimal(0)  # received; negative when paid

    @property
    def net(self) -> Decimal:
        return self.pnl - self.fees + self.funding

    @property
    def r_multiple(self) -> Decimal:
        return self.net / self.risk


@dataclass
class Result:
    initial_balance: Decimal
    final_balance: Decimal = Decimal(0)
    trades: list[Trade] = field(default_factory=list)
    skipped: Counter[str] = field(default_factory=Counter)  # entries not taken, by reason
    kill_days: list[int] = field(default_factory=list)  # day starts (ms) the limit was hit
    equity: list[tuple[int, Decimal]] = field(default_factory=list)  # at each candle's close


class Simulator:
    def __init__(
        self,
        strategy: Strategy,
        market: Market,
        trading: TradingConfig,
        costs: Costs,
        initial_balance: Decimal,
    ) -> None:
        self._strategy = strategy
        self._market = market
        self._trading = trading
        self._costs = costs
        self._result = Result(initial_balance=initial_balance)
        self._wallet = initial_balance
        self._day = -1
        self._base = initial_balance
        self._day_pnl = Decimal(0)
        self._halted = False
        self._position: Trade | None = None

    def run(self, candles: list[Kline], marks: list[Kline], funding: list[FundingRate]) -> Result:
        """Run the strategy over `candles` (last price) once. `marks` are the mark price
        candles for the same times; a candle without one uses its last price candle."""
        step = INTERVAL_MS[self._strategy.interval]
        if candles and candles[0].interval != self._strategy.interval:
            raise ValueError(
                f"{candles[0].interval} candles for a {self._strategy.interval} strategy"
            )
        mark_at = {m.open_time: m for m in marks}
        rates = iter(funding)
        rate = next(rates, None)
        pending: Enter | Exit | None = None

        for candle in candles:
            mark = mark_at.get(candle.open_time, candle)
            self._new_day_if_needed(candle.open_time)
            while rate is not None and rate.time < candle.open_time + step:
                if rate.time >= candle.open_time:
                    self._settle_funding(rate, candle, mark)
                rate = next(rates, None)
            if pending is not None:
                self._carry_out(pending, candle, mark)
                pending = None
            if self._position is not None:
                self._check_stop(candle, mark)
            pending = self._strategy.on_candle(candle, self._view())
            self._result.equity.append((candle.close_time, self._equity(candle.close)))

        if self._position is not None and candles:
            last = candles[-1]
            self._close(self._slipped(last.close, closing=True), last.close_time, "end")
        self._result.final_balance = self._wallet
        return self._result

    # Steps of one candle

    def _new_day_if_needed(self, time: int) -> None:
        day = day_start_ms(time)
        if day != self._day:
            self._day = day
            self._base = self._wallet  # the wallet balance at 00:00 UTC (D-23)
            self._day_pnl = Decimal(0)
            self._halted = False  # the backtest lifts the halt at the day boundary (D-37)

    def _settle_funding(self, rate: FundingRate, candle: Kline, mark: Kline) -> None:
        position = self._position
        if position is None:
            return
        paid = position.quantity * mark.open * rate.rate * position.side.sign
        position.funding -= paid
        self._book(-paid, candle.open, candle.open_time)

    def _carry_out(self, action: Enter | Exit, candle: Kline, mark: Kline) -> None:
        if isinstance(action, Exit):
            if self._position is not None:
                price = self._slipped(candle.open, closing=True)
                self._close(price, candle.open_time, "signal")
            return
        if self._position is not None:
            return  # one entry per position (D-22)
        if self._halted:
            self._result.skipped["daily loss limit reached"] += 1
            return
        self._enter(action, candle, mark)

    def _enter(self, action: Enter, candle: Kline, mark: Kline) -> None:
        per_trade = risk_amount(self._base, self._trading.risk_per_trade_percent)
        risk = min(per_trade, self._daily().room)  # the entry gate (D-24)
        try:
            plan = size_entry(
                side=action.side,
                reference_price=mark.open,
                stop_price=action.stop_price,
                risk_amount=risk,
                base_balance=self._base,
                max_notional_multiple=self._trading.max_notional_multiple,
                available_balance=self._wallet,
                leverage=self._market.leverage,
                rules=self._market.rules,
                brackets=self._market.brackets,
            )
        except EntryRejected as e:
            self._result.skipped[_rejection(e)] += 1
            return
        price = self._slipped(candle.open, closing=False, side=action.side)
        self._position = Trade(
            side=action.side,
            quantity=plan.quantity,
            entry_time=candle.open_time,
            entry_price=price,
            stop_price=plan.stop_price,
            risk=plan.risk,
            liquidation_price=plan.liquidation_price,
        )
        fee = plan.quantity * price * self._costs.taker_fee
        self._position.fees += fee
        self._book(-fee, candle.open, candle.open_time)

    def _check_stop(self, candle: Kline, mark: Kline) -> None:
        position = self._position
        assert position is not None
        stop = position.stop_price
        if position.side is Side.LONG:
            if mark.low > stop:
                return
            touched = candle.open if mark.open <= stop else stop
            price = touched * (1 - self._costs.stop_slippage)
            liquidated = price <= position.liquidation_price
        else:
            if mark.high < stop:
                return
            touched = candle.open if mark.open >= stop else stop
            price = touched * (1 + self._costs.stop_slippage)
            liquidated = price >= position.liquidation_price
        if liquidated:
            self._close(position.liquidation_price, candle.open_time, "liquidation")
        else:
            self._close(price, candle.open_time, "stop")

    # Bookkeeping

    def _close(self, price: Decimal, time: int, reason: str) -> None:
        position = self._position
        assert position is not None
        self._position = None
        position.exit_time = time
        position.exit_price = price
        position.exit_reason = reason
        position.pnl = position.quantity * (price - position.entry_price) * position.side.sign
        fee = position.quantity * price * self._costs.taker_fee
        position.fees += fee
        self._result.trades.append(position)
        self._book(position.pnl - fee, price, time)

    def _book(self, amount: Decimal, price: Decimal, time: int) -> None:
        """A realized cash flow; fires the kill switch at the daily loss limit (D-24, D-27)."""
        self._wallet += amount
        self._day_pnl += amount
        if self._halted or not self._daily().reached:
            return
        self._halted = True
        self._result.kill_days.append(self._day)
        if self._position is not None:
            self._close(self._slipped(price, closing=True), time, "kill")

    def _daily(self) -> DailyLoss:
        return DailyLoss(
            base=self._base,
            loss=-self._day_pnl,
            limit=daily_limit(self._base, self._trading.daily_loss_limit_percent),
        )

    def _slipped(self, price: Decimal, *, closing: bool, side: Side | None = None) -> Decimal:
        """A market fill at `price`, moved against the trade by the slippage."""
        if closing:
            assert self._position is not None
            side = self._position.side
            sign = -side.sign  # closing a long sells, so the price moves down
        else:
            assert side is not None
            sign = side.sign
        return price * (1 + sign * self._costs.market_slippage)

    def _view(self) -> Position | None:
        p = self._position
        return None if p is None else Position(p.side, p.quantity, p.entry_price)

    def _equity(self, price: Decimal) -> Decimal:
        p = self._position
        if p is None:
            return self._wallet
        return self._wallet + p.quantity * (price - p.entry_price) * p.side.sign


def _rejection(error: EntryRejected) -> str:
    """A short reason for the report, from the sizing error's message."""
    text = str(error)
    if "below the minimum" in text:
        return "below the minimum order size"
    if "liquidation" in text:
        return "stop too far for the leverage (D-25)"
    if "losing side" in text:
        return "stop already crossed at the open"
    if "No risk amount" in text:
        return "no room under the daily loss limit"
    return text
