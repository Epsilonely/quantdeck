from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pytest
from fake_exchange import BRACKETS

from quantdeck_engine.backtest.data import FundingRate
from quantdeck_engine.backtest.report import (
    by_year,
    format_report,
    longest_losing_streak,
    max_drawdown_percent,
    summarize,
)
from quantdeck_engine.backtest.simulator import Costs, LimitEntries, Market, Simulator
from quantdeck_engine.binance.streams import Kline
from quantdeck_engine.config import TradingConfig
from quantdeck_engine.strategy.base import Action, Enter, Exit, Position, Strategy
from quantdeck_engine.strategy.bollinger import BollingerReversion
from quantdeck_engine.strategy.donchian import DonchianBreakout
from quantdeck_engine.times import DAY_MS, day_ms
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
)
FREE = Costs(Decimal(0), Decimal(0), Decimal(0), Decimal(0))
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
    costs = Costs(Decimal("0.001"), Decimal("0.01"), Decimal(0), Decimal(0))
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
    costs = Costs(Decimal(0), Decimal(0), Decimal("0.01"), Decimal(0))
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
    assert simulator._result.skipped == {"stop too far for the leverage": 1}


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


# Market entries are unchanged by the limit entry model (D-45)


def waves(count: int) -> tuple[list[Kline], list[Kline]]:
    """Trending and swinging candles, with mark prices a little off the last price."""
    candles, marks = [], []
    price = Decimal(100)
    for i in range(count):
        drift = Decimal((i // 40) % 3 - 1) * Decimal("0.35")
        swing = Decimal((i * 7) % 11 - 5) / 10
        o, c = price, price + drift + swing
        h, low = max(o, c) + Decimal("0.4"), min(o, c) - Decimal("0.4")
        shift = Decimal((i % 5) - 2) / 20
        candles.append(candle(i, str(o), str(h), str(low), str(c)))
        marks.append(candle(i, str(o + shift), str(h + shift), str(low + shift), str(c + shift)))
        price = c
    return candles, marks


@pytest.mark.parametrize(
    ("strategy", "trades", "final_balance", "fees", "funding", "kill_days"),
    [
        (DonchianBreakout("1h", 20, 10), 10, "13229.7446896029850", "32.9276996570150",
         "-1.336249790", 0),
        (BollingerReversion("1h"), 18, "8231.1716507306000", "272.9256734894000",
         "-1.815915400", 5),
    ],
)  # fmt: skip
def test_market_entry_results_match_the_model_before_limit_entries(
    strategy: Strategy, trades: int, final_balance: str, fees: str, funding: str, kill_days: int
) -> None:
    # The expected values were computed with the simulator before limit entries existed.
    candles, marks = waves(600)
    rates = [FundingRate(T0 + i * 8 * HOUR_MS, Decimal("0.0001")) for i in range(75)]
    costs = Costs(Decimal("0.0005"), Decimal("0.0002"), Decimal("0.0005"), Decimal("0.0002"))
    result = Simulator(strategy, MARKET, TRADING, costs, BALANCE).run(candles, marks, rates)

    assert len(result.trades) == trades
    assert result.final_balance == Decimal(final_balance)
    assert sum(t.fees for t in result.trades) == Decimal(fees)
    assert sum(t.funding for t in result.trades) == Decimal(funding)
    assert len(result.kill_days) == kill_days
    assert not any(t.maker for t in result.trades)
    assert result.limit_entries == LimitEntries()
    assert "Limit entries" not in format_report("test", result)


# Limit entries (D-45). The long examples buy at 98 with the stop at 93.

STOP = Decimal(93)
LIMIT = Decimal(98)
MAKER = Costs(Decimal("0.0005"), Decimal(0), Decimal(0), Decimal("0.0002"))


def test_a_limit_entry_traded_through_by_a_tick_fills_at_the_limit_with_the_maker_fee() -> None:
    candles = [flat(0), candle(1, "100", "100", "97.9", "99"), flat(2, "99")]
    strategy, simulator = simulate({0: Enter(Side.LONG, STOP, LIMIT)}, candles, costs=MAKER)
    result = simulator._result

    (trade,) = result.trades
    assert trade.maker
    assert (trade.entry_time, trade.entry_price) == (T0 + HOUR_MS, LIMIT)
    assert trade.quantity == Decimal(20)  # sized at the limit: 100 / (98 - 93)
    assert trade.risk == Decimal(100)
    # The maker fee in, the taker fee out at the end; no slippage on the fill.
    entry_fee = Decimal(20) * LIMIT * Decimal("0.0002")
    assert trade.fees == entry_fee + Decimal(20) * 99 * Decimal("0.0005")
    assert strategy.seen[1] == Position(Side.LONG, Decimal(20), LIMIT)
    assert result.limit_entries == LimitEntries(placed=1, filled=1)


def test_a_short_limit_fills_when_the_high_trades_through_it() -> None:
    candles = [flat(0), candle(1, "100", "102.1", "100", "101"), flat(2, "101")]
    _, simulator = simulate({0: Enter(Side.SHORT, Decimal(107), Decimal(102))}, candles)

    (trade,) = simulator._result.trades
    assert (trade.side, trade.entry_price, trade.quantity) == (
        Side.SHORT,
        Decimal(102),
        Decimal(20),
    )


@pytest.mark.parametrize(
    ("side", "stop", "limit", "low", "high"),
    [
        (Side.LONG, "93", "98", "98", "100"),  # the low only touches the limit
        (Side.LONG, "93", "98", "98.5", "100"),  # never reaches it
        (Side.SHORT, "107", "102", "100", "102"),
    ],
)
def test_a_limit_entry_not_traded_through_is_cancelled_and_the_strategy_decides_again(
    side: Side, stop: str, limit: str, low: str, high: str
) -> None:
    entry = Enter(side, Decimal(stop), Decimal(limit))
    candles = [flat(0), candle(1, "100", high, low, "100"), flat(2)]
    strategy, simulator = simulate({0: entry}, candles)
    result = simulator._result

    assert result.trades == []
    assert strategy.seen[1] is None
    assert result.limit_entries == LimitEntries(placed=1, unfilled=1)


def test_an_unfilled_limit_entry_can_be_placed_again_at_the_next_candle() -> None:
    entry = Enter(Side.LONG, STOP, LIMIT)
    candles = [flat(0), candle(1, "100", "100", "98", "99"), candle(2, "99", "99", "97", "98")]
    _, simulator = simulate({0: entry, 1: entry}, candles)
    result = simulator._result

    (trade,) = result.trades
    assert trade.entry_time == T0 + 2 * HOUR_MS
    assert result.limit_entries == LimitEntries(placed=2, filled=1, unfilled=1)


@pytest.mark.parametrize(
    ("side", "stop", "limit"),
    [
        (Side.LONG, "93", "100"),  # a buy at the open
        (Side.LONG, "93", "100.5"),  # a buy above it
        (Side.SHORT, "107", "100"),  # a sell at the open
        (Side.SHORT, "107", "99.5"),  # a sell below it
    ],
)
def test_a_limit_that_would_trade_at_the_open_is_rejected_as_post_only(
    side: Side, stop: str, limit: str
) -> None:
    entry = Enter(side, Decimal(stop), Decimal(limit))
    candles = [flat(0), candle(1, "100", "110", "90", "100"), flat(2)]
    _, simulator = simulate({0: entry}, candles)
    result = simulator._result

    assert result.trades == []
    assert result.limit_entries == LimitEntries(placed=1, rejected=1)


def test_a_stop_reached_in_the_fill_candle_fires_after_the_fill() -> None:
    costs = Costs(Decimal(0), Decimal(0), Decimal("0.01"), Decimal(0))
    candles = [flat(0), candle(1, "100", "100", "92", "95"), flat(2, "95")]
    _, simulator = simulate({0: Enter(Side.LONG, STOP, LIMIT)}, candles, costs=costs)

    (trade,) = simulator._result.trades
    assert trade.entry_time == trade.exit_time == T0 + HOUR_MS
    assert (trade.entry_price, trade.exit_reason) == (LIMIT, "stop")
    assert trade.exit_price == Decimal("92.07")  # the stop, less the stop slippage
    assert trade.net == Decimal(20) * (Decimal("92.07") - LIMIT)


def test_a_stop_in_the_fill_candle_fills_at_the_stop_even_if_the_mark_opened_past_it() -> None:
    # A market entry's stop would fill at the candle's open here; a limit entry filled after
    # the open, so its stop can't have filled there.
    candles = [flat(0), candle(1, "100", "100", "92", "95"), flat(2, "95")]
    marks = [flat(0), candle(1, "92.5", "100", "92", "95"), flat(2, "95")]
    _, simulator = simulate({0: Enter(Side.LONG, STOP, LIMIT)}, candles, marks=marks)

    (trade,) = simulator._result.trades
    assert (trade.exit_reason, trade.exit_price) == ("stop", STOP)


def test_a_limit_is_rounded_away_from_the_market_and_sized_there() -> None:
    long_candles = [flat(0), candle(1, "100", "100", "97", "99"), flat(2, "99")]
    _, simulator = simulate({0: Enter(Side.LONG, STOP, Decimal("98.07"))}, long_candles)
    (trade,) = simulator._result.trades
    assert (trade.entry_price, trade.quantity) == (LIMIT, Decimal(20))

    short_candles = [flat(0), candle(1, "100", "103", "100", "101"), flat(2, "101")]
    _, simulator = simulate({0: Enter(Side.SHORT, Decimal(107), Decimal("101.93"))}, short_candles)
    (trade,) = simulator._result.trades
    assert (trade.entry_price, trade.quantity) == (Decimal(102), Decimal(20))


def test_a_limit_entry_the_sizing_rejects_is_skipped_not_placed() -> None:
    candles = [flat(0), candle(1, "100", "100", "90", "95")]
    _, simulator = simulate({0: Enter(Side.LONG, Decimal(99), LIMIT)}, candles)
    result = simulator._result

    assert result.trades == []
    assert result.skipped == {"stop beyond the limit price": 1}
    assert result.limit_entries == LimitEntries()


def test_the_report_counts_limit_entries() -> None:
    entry = Enter(Side.LONG, STOP, LIMIT)
    candles = [
        flat(0),
        candle(1, "100", "100", "98", "100"),  # unfilled
        candle(2, "98", "100", "97", "99"),  # 98 at the open: rejected
        candle(3, "100", "100", "97", "99"),  # filled
        flat(4, "99"),
    ]
    _, simulator = simulate({0: entry, 1: entry, 2: entry}, candles)

    report = format_report("test", simulator._result)
    assert "Limit entries     3 placed: 1 filled, 1 rejected as crossing, 1 unfilled" in report
