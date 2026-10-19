import csv
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fake_exchange import Failure, FakeExchange, make_trader, no_sleep, open_long

from quantdeck_engine.binance.streams import Kline
from quantdeck_engine.strategy.base import Action, Enter, Exit
from quantdeck_engine.trading.executor import SymbolTrader, TradingHalted
from quantdeck_engine.trading.position import Position
from quantdeck_engine.trading.rules import Side
from quantdeck_engine.trading.strategy_runner import ForwardRecord, Signal, StrategyRunner

D = Decimal
STEP = 4 * 60 * 60_000
T0 = 1_791_936_000_000  # a 4h boundary

ORDER = ("POST", "/fapi/v1/order")
POSITION_RISK = ("GET", "/fapi/v3/positionRisk")


def candle(i: int) -> Kline:
    price = D(60000)
    return Kline("BTCUSDT", "4h", T0 + i * STEP, T0 + (i + 1) * STEP - 1, *[price] * 4, D(1), True)


@dataclass
class Scripted:
    """Returns the action scripted for each candle index, or raises the error scripted."""

    script: dict[int, Action | Exception] = field(default_factory=dict)
    interval: str = "4h"
    warmup: int = 3
    seen: list[tuple[int, Position | None]] = field(default_factory=list)

    def on_candle(self, candle: Kline, position: Position | None) -> Action | None:
        i = (candle.open_time - T0) // STEP
        self.seen.append((i, position))
        step = self.script.get(i)
        if isinstance(step, Exception):
            raise step
        return step

    @property
    def indexes(self) -> list[int]:
        return [i for i, _ in self.seen]


class FakeFeed:
    """Warm-up candles 0 to 2, then `live` from the stream, then the end."""

    def __init__(self, live: list[int]) -> None:
        self.warm = [candle(i) for i in range(3)]
        self.live = [candle(i) for i in live]
        self.requested: int | None = None

    async def warm_up(self, count: int) -> list[Kline]:
        self.requested = count
        return self.warm[-count:]

    async def candles(self) -> AsyncGenerator[Kline, None]:
        for k in self.live:
            yield k


class HaltedGate:
    """The entry gate while the kill switch has halted the account (D-27)."""

    async def entry_risk(self, per_trade: Decimal) -> Decimal:
        raise TradingHalted("kill switch: test halt")


async def run(
    exchange: FakeExchange,
    strategy: Scripted,
    live: list[int],
    *,
    trader: SymbolTrader | None = None,
    record: ForwardRecord | None = None,
    sleeps: list[float] | None = None,
) -> StrategyRunner:
    """Warm up, then run until the fake feed ends."""

    async def sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    runner = StrategyRunner(
        strategy, trader or make_trader(exchange), FakeFeed(live), record, sleep=sleep
    )
    await runner.warm_up()
    await runner.run()
    return runner


# Warm-up (D-43)


async def test_warm_up_feeds_all_but_the_latest_candle_which_run_decides_on() -> None:
    exchange = FakeExchange()
    strategy = Scripted()
    feed = FakeFeed([3])
    runner = StrategyRunner(strategy, make_trader(exchange), feed, None, sleep=no_sleep)

    await runner.warm_up()
    assert feed.requested == 3
    assert strategy.seen == [(0, None), (1, None)]
    assert runner.last_candle is None

    await runner.run()
    assert strategy.indexes == [0, 1, 2, 3]
    assert runner.last_candle == candle(3)
    assert runner.stopped is None


async def test_an_exit_on_the_latest_warm_up_candle_is_carried_out() -> None:
    exchange = FakeExchange()
    open_long(exchange)
    exchange.add_stop("SELL", "59400")
    strategy = Scripted({2: Exit()})

    runner = await run(exchange, strategy, [])

    _, seen = strategy.seen[-1]
    assert seen is not None and seen.side is Side.LONG and seen.quantity == D("0.083")
    assert exchange.position_amount == 0
    assert exchange.algo_orders == []
    assert runner.last_signal == Signal(T0 + 2 * STEP, "exit", None, "closed")


# Entries and exits


async def test_an_entry_signal_opens_a_protected_position() -> None:
    exchange = FakeExchange()
    strategy = Scripted({3: Enter(Side.LONG, D(59400))})

    runner = await run(exchange, strategy, [3])

    assert exchange.position_amount == D("0.083")
    assert [a["triggerPrice"] for a in exchange.algo_orders] == ["59400"]
    assert runner.last_signal == Signal(
        T0 + 3 * STEP, "enter_long", D(59400), "entered 0.083 at ~60000"
    )


async def test_an_entry_signal_with_a_position_open_is_ignored() -> None:
    exchange = FakeExchange()
    open_long(exchange)
    exchange.add_stop("SELL", "59400")

    runner = await run(exchange, Scripted({3: Enter(Side.SHORT, D(60600))}), [3])

    assert exchange.calls(*ORDER) == []
    assert runner.last_signal is not None
    assert runner.last_signal.outcome == "ignored: a position is already open"


@pytest.mark.parametrize("halt", ["symbol", "kill switch"])
async def test_a_halted_engine_skips_entry_signals(halt: str) -> None:
    exchange = FakeExchange()
    if halt == "symbol":
        trader = make_trader(exchange)
        trader.halt("test halt")  # as D-16, D-19, or D-20 would
    else:
        trader = make_trader(exchange, HaltedGate())

    runner = await run(exchange, Scripted({3: Enter(Side.LONG, D(59400))}), [3], trader=trader)

    assert exchange.calls(*ORDER) == []
    assert runner.last_signal is not None
    assert runner.last_signal.outcome.startswith("skipped, halted: ")
    assert runner.stopped is None


async def test_exits_still_run_while_halted() -> None:
    exchange = FakeExchange()
    open_long(exchange)
    exchange.add_stop("SELL", "59400")
    trader = make_trader(exchange)
    trader.halt("test halt")

    runner = await run(exchange, Scripted({3: Exit()}), [3], trader=trader)

    assert exchange.position_amount == 0
    assert runner.last_signal is not None and runner.last_signal.outcome == "closed"


# Failures (D-43)


async def test_a_failed_request_costs_only_its_candle(caplog: pytest.LogCaptureFixture) -> None:
    exchange = FakeExchange()
    exchange.fail("GET", "/fapi/v1/premiumIndex", Failure(httpx.ConnectError("down")))
    entry = Enter(Side.LONG, D(59400))

    with caplog.at_level(logging.ERROR):
        runner = await run(exchange, Scripted({3: entry, 4: entry}), [3, 4])

    assert "enter_long failed: down" in caplog.text
    assert exchange.position_amount == D("0.083")  # entered on the next candle
    assert runner.last_signal is not None
    assert runner.last_signal.candle_open_time == T0 + 4 * STEP
    assert runner.stopped is None


async def test_position_reads_are_retried_while_rest_fails() -> None:
    exchange = FakeExchange()
    down = Failure(httpx.ConnectError("down"))
    exchange.fail(*POSITION_RISK, down, down)
    sleeps: list[float] = []
    strategy = Scripted()

    await run(exchange, strategy, [3], sleeps=sleeps)

    assert sleeps == [5.0, 5.0]
    assert strategy.indexes == [0, 1, 2, 3]  # no candle skipped


async def test_an_unexpected_error_stops_only_this_strategy(
    caplog: pytest.LogCaptureFixture,
) -> None:
    exchange = FakeExchange()
    strategy = Scripted({3: RuntimeError("a bug")})

    with caplog.at_level(logging.CRITICAL):
        runner = await run(exchange, strategy, [3, 4])  # returns instead of raising

    assert runner.stopped == "RuntimeError: a bug"
    assert strategy.indexes == [0, 1, 2, 3]
    assert "the strategy stopped on an unexpected error" in caplog.text


# The forward test record (D-43)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


async def test_every_decision_is_recorded(tmp_path: Path) -> None:
    path = tmp_path / "forward" / "BTCUSDT_4h.csv"
    record = ForwardRecord(path, clock=lambda: 1_792_000_000.0)
    exchange = FakeExchange()

    await run(exchange, Scripted({3: Enter(Side.LONG, D(59400))}), [3, 4], record=record)

    with path.open(encoding="utf-8") as f:
        assert f.readline().strip() == ",".join(ForwardRecord.COLUMNS)
    rows = read_csv(path)
    assert [r["open_time"] for r in rows] == [str(T0 + i * STEP) for i in (2, 3, 4)]
    assert rows[0]["open_time_utc"] == "2026-10-14 08:00"
    assert (rows[0]["close"], rows[0]["action"], rows[0]["outcome"]) == ("60000", "", "")
    assert rows[1]["position_side"] == ""
    assert (rows[1]["action"], rows[1]["stop_price"]) == ("enter_long", "59400")
    assert rows[1]["outcome"] == "entered 0.083 at ~60000"
    assert (rows[2]["position_side"], rows[2]["position_quantity"]) == ("LONG", "0.083")
    assert rows[2]["position_entry_price"] == "60000"
    assert rows[2]["decided_at_utc"] == "2026-10-14T17:46:40+00:00"


async def test_a_record_continues_after_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "BTCUSDT_4h.csv"
    for _ in range(2):
        await run(FakeExchange(), Scripted(), [], record=ForwardRecord(path))
    rows = read_csv(path)
    assert len(rows) == 2  # one header, and the latest warm-up candle once per start


def test_a_record_that_cannot_be_written_only_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    record = ForwardRecord(tmp_path)  # a directory, so the append fails
    with caplog.at_level(logging.WARNING):
        record.append(candle(0), None, None, "")
    assert "not written" in caplog.text
