from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from fake_exchange import BRACKETS

from quantdeck_engine.backtest.data import DAY_MS, FundingRate, day_ms
from quantdeck_engine.backtest.report import (
    by_year,
    longest_losing_streak,
    max_drawdown_percent,
    summarize,
)
from quantdeck_engine.backtest.simulator import Costs, Market, Simulator
from quantdeck_engine.binance.streams import Kline
from quantdeck_engine.config import TradingConfig
from quantdeck_engine.strategy.base import Action, Enter, Exit, Position
from quantdeck_engine.trading.rules import Side, SymbolRules
from quantdeck_engine.trading.sizing import Bracket

HOUR_MS = 60 * 60 * 1000
T0 = day_ms(date(2026, 9, 1))
RULES = SymbolRules(
    symbol="BTCUSDT",
    margin_asset="USDT",
    tick_size=Decimal("0.1"),
    step_size=Decimal("0.001"),
    min_qty=Decimal("0.001"),
    max_qty=Decimal(1000),
    min_notional=Decimal(5),
)
MARKET = Market(RULES, [Bracket.from_api(b) for b in BRACKETS], leverage=5)
TRADING = TradingConfig(
    risk_per_trade_percent=Decimal(1),
    daily_loss_limit_percent=Decimal(3),
    max_notional_multiple=Decimal(2),
    symbols={},
)
FREE = Costs(Decimal(0), Decimal(0), Decimal(0))
BALANCE = Decimal(10000)


def candle(i: int, o: str, h: str, low: str, c: str) -> Kline:
    return Kline(
        symbol="BTCUSDT",
        interval="1h",
        open_time=T0 + i * HOUR_MS,
        close_time=T0 + (i + 1) * HOUR_MS - 1,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=Decimal(1),
        closed=True,
    )


def flat(i: int, price: str = "100") -> Kline:
    return candle(i, price, price, price, price)


@dataclass
class Scripted:
    """Returns the action scripted for each candle index and records what it saw."""

    actions: dict[int, Action]
    interval: str = "1h"
    warmup: int = 1
    seen: list[Position | None] = field(default_factory=list)

    def on_candle(self, candle: Kline, position: Position | None) -> Action | None:
        self.seen.append(position)
        return self.actions.get((candle.open_time - T0) // HOUR_MS)


def simulate(
    actions: dict[int, Action],
    candles: list[Kline],
    *,
    marks: list[Kline] | None = None,
    funding: list[FundingRate] | None = None,
    costs: Costs = FREE,
    trading: TradingConfig = TRADING,
) -> tuple[Scripted, Simulator]:
    strategy = Scripted(actions)
    simulator = Simulator(strategy, MARKET, trading, costs, BALANCE)
    simulator.run(candles, candles if marks is None else marks, funding or [])
    return strategy, simulator


def test_entry_fills_at_the_next_open_and_the_stop_loses_one_risk_amount() -> None:
    candles = [flat(0), flat(1), candle(2, "100", "100", "89", "90")]
    strategy, simulator = simulate({0: Enter(Side.LONG, Decimal(95))}, candles)
    result = simulator._result

    (trade,) = result.trades
    assert trade.quantity == Decimal(20)  # risk 1% of 10000 = 100, over a distance of 5
    assert trade.entry_time == T0 + HOUR_MS
    assert trade.entry_price == Decimal(100)
    assert (trade.exit_reason, trade.exit_price) == ("stop", Decimal(95))
    assert trade.net == Decimal(-100)
    assert trade.r_multiple == Decimal(-1)
    assert result.final_balance == BALANCE - 100
    assert strategy.seen[1] == Position(Side.LONG, Decimal(20), Decimal(100))
    assert strategy.seen[2] is None


def test_a_short_is_sized_from_its_stop_above() -> None:
    candles = [flat(0), flat(1), flat(2)]
    _, simulator = simulate({0: Enter(Side.SHORT, Decimal(102))}, candles)

    (trade,) = simulator._result.trades
    assert trade.quantity == Decimal(50)  # 100 / 2
    assert trade.risk == Decimal(100)
    assert trade.exit_reason == "end"


def test_signal_exit_pays_slippage_and_fees() -> None:
    costs = Costs(Decimal("0.001"), Decimal("0.01"), Decimal(0))
    candles = [flat(0), flat(1), flat(2, "104"), flat(3, "104")]
    _, simulator = simulate({0: Enter(Side.SHORT, Decimal(105)), 2: Exit()}, candles, costs=costs)

    (trade,) = simulator._result.trades
    assert trade.quantity == Decimal(20)  # sized at the mark price open, 100
    assert trade.entry_price == Decimal("99.00")  # a short sells 1% lower
    assert trade.exit_price == Decimal("105.04")  # buying back 1% higher
    assert trade.exit_reason == "signal"
    assert trade.pnl == Decimal(20) * (Decimal("99.00") - Decimal("105.04"))
    assert trade.fees == Decimal(20) * (Decimal("99.00") + Decimal("105.04")) * Decimal("0.001")


def test_stops_trigger_on_the_mark_price_not_the_last_price() -> None:
    candles = [flat(0), flat(1), candle(2, "100", "100", "90", "100"), flat(3)]
    marks = [flat(0), flat(1), candle(2, "100", "100", "96", "100"), flat(3)]
    _, simulator = simulate({0: Enter(Side.LONG, Decimal(95))}, candles, marks=marks)

    (trade,) = simulator._result.trades
    assert trade.exit_reason == "end"


def test_a_stop_gapped_through_fills_at_the_open() -> None:
    costs = Costs(Decimal(0), Decimal(0), Decimal("0.01"))
    candles = [flat(0), flat(1), candle(2, "90", "91", "88", "89")]
    _, simulator = simulate({0: Enter(Side.LONG, Decimal(95))}, candles, costs=costs)

    (trade,) = simulator._result.trades
    assert (trade.exit_reason, trade.exit_price) == ("stop", Decimal("89.10"))


def test_funding_is_paid_only_while_a_position_is_held_at_the_funding_time() -> None:
    candles = [flat(i) for i in range(4)]
    funding = [
        FundingRate(T0 + HOUR_MS, Decimal("0.001")),  # the entry's own open: not paid
        FundingRate(T0 + 2 * HOUR_MS, Decimal("0.001")),  # held: a long pays
        FundingRate(T0 + 3 * HOUR_MS, Decimal("-0.002")),  # held until this open: received
    ]
    _, simulator = simulate({0: Enter(Side.LONG, Decimal(95)), 2: Exit()}, candles, funding=funding)

    (trade,) = simulator._result.trades
    assert trade.exit_time == T0 + 3 * HOUR_MS
    # 20 BTC x 100: -2 at 02:00, +4 at 03:00.
    assert trade.funding == Decimal(2)


def test_the_daily_limit_halts_entries_until_the_next_utc_day() -> None:
    # Each entry is stopped out for one risk amount (1%); the limit is 3%.
    actions: dict[int, Action] = {}
    candles = []
    for n in range(5):
        signal = 3 * n
        actions[signal] = Enter(Side.LONG, Decimal(95))
        candles += [flat(signal), flat(signal + 1), candle(signal + 2, "100", "100", "90", "90")]
    # The next day: one more entry, which goes through.
    next_day = DAY_MS // HOUR_MS
    actions[next_day] = Enter(Side.LONG, Decimal(95))
    candles += [flat(next_day), flat(next_day + 1), flat(next_day + 2)]

    _, simulator = simulate(actions, candles)
    result = simulator._result

    assert [t.exit_reason for t in result.trades] == ["stop", "stop", "stop", "end"]
    assert result.skipped == {"daily loss limit reached": 2}
    assert result.kill_days == [T0]


def test_the_kill_switch_closes_an_open_position() -> None:
    # A funding payment of 20 x 100 x 0.2 = 400 passes the 300 limit while the long is open.
    candles = [flat(i) for i in range(4)]
    funding = [FundingRate(T0 + 2 * HOUR_MS, Decimal("0.2"))]
    _, simulator = simulate({0: Enter(Side.LONG, Decimal(95))}, candles, funding=funding)
    result = simulator._result

    (trade,) = result.trades
    assert (trade.exit_reason, trade.exit_time) == ("kill", T0 + 2 * HOUR_MS)
    assert result.kill_days == [T0]


def test_an_entry_the_sizing_rejects_is_skipped_and_counted() -> None:
    # A stop 60% away is far beyond half the liquidation distance at 5x (D-25).
    candles = [flat(0), flat(1)]
    _, simulator = simulate({0: Enter(Side.LONG, Decimal(40))}, candles)

    assert simulator._result.trades == []
    assert simulator._result.skipped == {"stop too far for the leverage (D-25)": 1}


def test_report_numbers() -> None:
    candles = [flat(0), flat(1), candle(2, "100", "100", "89", "90")]
    _, simulator = simulate({0: Enter(Side.LONG, Decimal(95))}, candles)
    result = simulator._result

    summary = summarize(result)
    assert summary.trades == 1
    assert summary.win_rate == 0
    assert summary.expectancy == Decimal(-100)
    assert summary.expectancy_r == Decimal(-1)
    assert summary.longest_losing_streak == 1
    assert summary.return_percent == Decimal(-1)
    assert max_drawdown_percent(result.equity) == Decimal(1)
    assert by_year(result) == {2026: (1, Decimal(-100), Decimal(-1))}
    assert longest_losing_streak([]) == 0
