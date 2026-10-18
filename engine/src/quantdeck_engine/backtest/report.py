"""Backtest results as the numbers D-39 compares strategies on.

Expectancy is the mean net result per trade (after fees, slippage, and funding), in USDT and
in R, where 1 R is the trade's planned loss at its stop (D-22). Drawdown is measured on the
equity at each candle's close, open positions valued at the close.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from quantdeck_engine.backtest.simulator import Result, Trade


@dataclass(frozen=True)
class Summary:
    trades: int
    wins: int
    net: Decimal
    fees: Decimal
    funding: Decimal
    expectancy: Decimal | None  # USDT per trade
    expectancy_r: Decimal | None
    average_win_r: Decimal | None
    average_loss_r: Decimal | None
    return_percent: Decimal
    max_drawdown_percent: Decimal
    longest_losing_streak: int
    kill_days: int

    @property
    def win_rate(self) -> Decimal | None:
        return None if not self.trades else Decimal(self.wins) / self.trades


def summarize(result: Result) -> Summary:
    trades = result.trades
    wins = [t for t in trades if t.net > 0]
    losses = [t for t in trades if t.net <= 0]
    return Summary(
        trades=len(trades),
        wins=len(wins),
        net=sum((t.net for t in trades), Decimal(0)),
        fees=sum((t.fees for t in trades), Decimal(0)),
        funding=sum((t.funding for t in trades), Decimal(0)),
        expectancy=_mean([t.net for t in trades]),
        expectancy_r=_mean([t.r_multiple for t in trades]),
        average_win_r=_mean([t.r_multiple for t in wins]),
        average_loss_r=_mean([t.r_multiple for t in losses]),
        return_percent=(result.final_balance / result.initial_balance - 1) * 100,
        max_drawdown_percent=max_drawdown_percent(result.equity),
        longest_losing_streak=longest_losing_streak(trades),
        kill_days=len(result.kill_days),
    )


def max_drawdown_percent(equity: list[tuple[int, Decimal]]) -> Decimal:
    peak = Decimal(0)
    worst = Decimal(0)
    for _, value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst * 100


def longest_losing_streak(trades: list[Trade]) -> int:
    longest = current = 0
    for t in trades:
        current = current + 1 if t.net <= 0 else 0
        longest = max(longest, current)
    return longest


def by_year(result: Result) -> dict[int, tuple[int, Decimal, Decimal]]:
    """Per calendar year (UTC) of each trade's exit: trades, net USDT, and return in percent
    of the equity at the year's first candle."""
    start_equity: dict[int, Decimal] = {}
    for time, value in result.equity:
        start_equity.setdefault(_year(time), value)
    counts: dict[int, int] = defaultdict(int)
    nets: dict[int, Decimal] = defaultdict(Decimal)
    for t in result.trades:
        counts[_year(t.exit_time)] += 1
        nets[_year(t.exit_time)] += t.net
    return {
        year: (counts[year], nets[year], nets[year] / start_equity[year] * 100)
        for year in sorted(start_equity)
    }


def format_report(title: str, result: Result) -> str:
    s = summarize(result)
    lines = [
        title,
        f"  Balance           {result.initial_balance:.2f} -> {result.final_balance:.2f} USDT "
        f"({s.return_percent:+.1f}%)",
        f"  Trades            {s.trades}, win rate {_percent(s.win_rate)}",
        f"  Expectancy        {_amount(s.expectancy)} USDT, {_amount(s.expectancy_r, 3)} R "
        f"per trade",
        f"  Average win/loss  {_amount(s.average_win_r, 2)} R / {_amount(s.average_loss_r, 2)} R",
        f"  Max drawdown      {s.max_drawdown_percent:.1f}%",
        f"  Losing streak     {s.longest_losing_streak} trades",
        f"  Daily limit hit   {s.kill_days} days",
        f"  Fees, funding     {s.fees:.2f} USDT paid, {s.funding:+.2f} USDT",
    ]
    exits: dict[str, int] = defaultdict(int)
    for t in result.trades:
        exits[t.exit_reason] += 1
    lines.append("  Exits             " + ", ".join(f"{k} {v}" for k, v in sorted(exits.items())))
    if result.skipped:
        skipped = ", ".join(f"{reason} {n}" for reason, n in result.skipped.most_common())
        lines.append(f"  Entries skipped   {skipped}")
    lines.append("  By year           trades       net USDT    return")
    for year, (count, net, percent) in by_year(result).items():
        lines.append(f"    {year}            {count:6d}  {net:13.2f}  {percent:+7.1f}%")
    return "\n".join(lines)


def _mean(values: list[Decimal]) -> Decimal | None:
    return sum(values, Decimal(0)) / len(values) if values else None


def _year(ms: int) -> int:
    return datetime.fromtimestamp(ms / 1000, UTC).year


def _percent(value: Decimal | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _amount(value: Decimal | None, places: int = 2) -> str:
    return "-" if value is None else f"{value:+.{places}f}"
