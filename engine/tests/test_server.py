import asyncio
import json
import logging
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from quantdeck_engine.config import ConfigError
from quantdeck_engine.server import PROTOCOL_VERSION, ClientServer, ConnectionFile, LogBuffer
from quantdeck_engine.symbols import PauseNotSaved, SymbolChangeRefused, catalog
from quantdeck_engine.trading.risk import LiftRefused


class FakeEngine:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {"halted": False}
        self.closes = 0
        self.lift_error: Exception | None = None
        self.stopped = asyncio.Event()
        self.symbol_args: list[dict[str, Any]] = []
        self.trade_list: dict[str, Any] | None = None  # replaced, never changed in place
        self.candle_lists: dict[str, dict[str, Any]] = {}  # likewise, each list

    def snapshot(self) -> dict[str, Any]:
        return dict(self.state)

    def trades(self) -> dict[str, Any] | None:
        return self.trade_list

    def candles(self) -> dict[str, dict[str, Any]]:
        return dict(self.candle_lists)

    async def emergency_close(self) -> bool:
        self.closes += 1
        self.state["halted"] = True
        return True

    async def lift_halt(self) -> bool:
        if self.lift_error is not None:
            raise self.lift_error
        was_halted = self.state["halted"]
        self.state["halted"] = False
        return was_halted

    async def stop(self) -> None:
        self.stopped.set()

    async def set_symbol(self, args: Mapping[str, Any]) -> str:
        self.symbol_args.append(dict(args))
        if args.get("leverage") == 11:
            raise ConfigError("set_symbol: leverage must be between 1 and 10, got 11")
        return f"{args['symbol']} added"

    async def remove_symbol(self, args: Mapping[str, Any]) -> str:
        self.symbol_args.append(dict(args))
        raise SymbolChangeRefused(f"{args['symbol']} has an open LONG position")

    async def pause_symbol(self, args: Mapping[str, Any]) -> str:
        self.symbol_args.append(dict(args))
        return f"{args['symbol']} paused"

    async def close_symbol(self, args: Mapping[str, Any]) -> str:
        self.symbol_args.append(dict(args))
        raise PauseNotSaved(f"{args['symbol']} closed LONG 0.083, but the pause could not be saved")

    async def resume_symbol(self, args: Mapping[str, Any]) -> str:
        self.symbol_args.append(dict(args))
        raise SymbolChangeRefused(f"{args['symbol']} is not one of the engine's symbols")


@dataclass
class Served:
    engine: FakeEngine
    logs: LogBuffer
    file: ConnectionFile
    server: ClientServer
    task: asyncio.Task[None]


@pytest.fixture
async def served(tmp_path: Path) -> AsyncIterator[Served]:
    engine, logs = FakeEngine(), LogBuffer()
    file = ConnectionFile(tmp_path / "state" / "connection.json")
    server = ClientServer(
        engine, logs, connection_file=file, publish_interval=0.01, auth_timeout=0.3
    )
    task = asyncio.create_task(server.run())
    await asyncio.wait_for(server.listening.wait(), 2)
    yield Served(engine, logs, file, server, task)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


@pytest.fixture
def test_logger(served: Served) -> Iterator[logging.Logger]:
    log = logging.getLogger("test_server_records")
    log.setLevel(logging.DEBUG)
    log.addHandler(served.logs)
    yield log
    log.removeHandler(served.logs)


def url(file: ConnectionFile) -> str:
    return f"ws://127.0.0.1:{json.loads(file.path.read_text())['port']}"


async def hello(file: ConnectionFile) -> ClientConnection:
    ws = await connect(url(file))
    token = json.loads(file.path.read_text())["token"]
    await ws.send(json.dumps({"type": "hello", "token": token}))
    return ws


async def receive(ws: ClientConnection, kind: str) -> dict[str, Any]:
    """The next message of type `kind`, skipping others."""
    async with asyncio.timeout(2):
        while True:
            message = json.loads(await ws.recv())
            if message["type"] == kind:
                return message


async def command(
    ws: ClientConnection, name: str, command_id: object = 1, args: object = None
) -> dict[str, Any]:
    message = {"type": "command", "id": command_id, "name": name}
    if args is not None:
        message["args"] = args
    await ws.send(json.dumps(message))
    return await receive(ws, "result")


# Connection file and authentication (D-32)


async def test_connection_file_exists_only_while_serving(tmp_path: Path) -> None:
    file = ConnectionFile(tmp_path / "connection.json")
    server = ClientServer(FakeEngine(), LogBuffer(), connection_file=file)
    task = asyncio.create_task(server.run())
    await asyncio.wait_for(server.listening.wait(), 2)
    data = json.loads(file.path.read_text())
    assert data["protocol"] == PROTOCOL_VERSION
    assert data["port"] == server.port
    assert len(data["token"]) >= 32
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    assert not file.path.exists()


async def test_client_gets_welcome_logs_and_state_in_order(
    served: Served, test_logger: logging.Logger
) -> None:
    test_logger.info("before the client connected")
    test_logger.debug("below INFO, never sent")
    async with await hello(served.file) as ws:
        first = [json.loads(await ws.recv()) for _ in range(3)]
    assert [m["type"] for m in first] == ["welcome", "logs", "state"]
    assert first[0]["protocol"] == PROTOCOL_VERSION
    assert first[0]["catalog"] == catalog()  # D-48
    assert [r["message"] for r in first[1]["records"]] == ["before the client connected"]
    assert first[1]["records"][0]["level"] == "INFO"
    assert first[2]["state"] == {"halted": False}


async def test_wrong_token_is_refused(served: Served) -> None:
    async with connect(url(served.file)) as ws:
        await ws.send(json.dumps({"type": "hello", "token": "wrong"}))
        with pytest.raises(ConnectionClosed) as closed:
            await ws.recv()
    assert closed.value.rcvd is not None and closed.value.rcvd.code == 1008


async def test_silent_client_is_refused(served: Served) -> None:
    async with connect(url(served.file)) as ws:
        with pytest.raises(ConnectionClosed) as closed:
            await ws.recv()
    assert closed.value.rcvd is not None and closed.value.rcvd.code == 1008


async def test_browser_origin_is_refused(served: Served) -> None:
    with pytest.raises(InvalidStatus) as refused:
        await connect(url(served.file), origin="https://example.com")  # type: ignore[arg-type]
    assert refused.value.response.status_code == 403


# Updates (D-33)


async def test_changed_state_is_sent_to_every_client(served: Served) -> None:
    async with await hello(served.file) as a, await hello(served.file) as b:
        await receive(a, "state")
        await receive(b, "state")
        served.engine.state["mark"] = "60000"
        assert (await receive(a, "state"))["state"]["mark"] == "60000"
        assert (await receive(b, "state"))["state"]["mark"] == "60000"


async def test_new_log_records_are_sent(served: Served, test_logger: logging.Logger) -> None:
    async with await hello(served.file) as ws:
        await receive(ws, "logs")  # the backlog
        test_logger.warning("something happened")
        records = (await receive(ws, "logs"))["records"]
    assert [(r["level"], r["message"]) for r in records] == [("WARNING", "something happened")]


# Recent trades (D-51)

TRADES = {"window_days": 7, "asset": "USDT", "totals": {}, "trades": []}


async def test_trades_follow_the_state_once_read(served: Served) -> None:
    async with await hello(served.file) as ws:
        await receive(ws, "state")
        served.engine.state["mark"] = "60000"
        await receive(ws, "state")  # no trades message before the engine has read them
        served.engine.trade_list = TRADES
        assert await receive(ws, "trades") == {"type": "trades", **TRADES}

    async with await hello(served.file) as ws:
        first = [json.loads(await ws.recv()) for _ in range(4)]
    assert [m["type"] for m in first] == ["welcome", "logs", "state", "trades"]


async def test_trades_are_sent_again_only_when_they_change(served: Served) -> None:
    served.engine.trade_list = TRADES
    async with await hello(served.file) as ws:
        await receive(ws, "trades")
        served.engine.trade_list = {**TRADES, "trades": [{"order_id": 1}]}
        assert (await receive(ws, "trades"))["trades"] == [{"order_id": 1}]
        served.engine.state["mark"] = "60000"
        await receive(ws, "state")
        await asyncio.sleep(0.05)  # several publish rounds with the same list
        served.engine.state["mark"] = "60001"
        # Only the state follows: the unchanged trades weren't sent again.
        assert json.loads(await ws.recv())["type"] == "state"


async def test_a_list_sent_on_connect_is_not_sent_again_by_the_next_round(tmp_path: Path) -> None:
    engine = FakeEngine()
    file = ConnectionFile(tmp_path / "connection.json")
    server = ClientServer(engine, LogBuffer(), connection_file=file, publish_interval=0.2)
    task = asyncio.create_task(server.run())
    await asyncio.wait_for(server.listening.wait(), 2)
    # Read after the first publish round, which saw no list; the next round is the first to
    # see it, after the client below got it on connect.
    engine.trade_list = TRADES
    async with await hello(file) as ws:
        first = [json.loads(await ws.recv()) for _ in range(4)]
        assert first[-1]["type"] == "trades"
        await asyncio.sleep(0.3)
        engine.state["mark"] = "60000"
        assert json.loads(await asyncio.wait_for(ws.recv(), 2))["type"] == "state"
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


# Chart candles (D-54)


def candles(symbol: str, *closes: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "interval": "15m",
        "candles": [{"open_time": str(i), "close": c} for i, c in enumerate(closes)],
    }


async def test_each_symbols_candles_follow_the_trades_on_connect(served: Served) -> None:
    served.engine.trade_list = TRADES
    served.engine.candle_lists = {"BTCUSDT": candles("BTCUSDT", "1"), "ETHUSDT": candles("ETHUSDT")}
    async with await hello(served.file) as ws:
        first = [json.loads(await ws.recv()) for _ in range(6)]
    assert [m["type"] for m in first] == [
        "welcome",
        "logs",
        "state",
        "trades",
        "candles",
        "candles",
    ]
    assert first[4] == {"type": "candles", **candles("BTCUSDT", "1")}
    assert first[5]["symbol"] == "ETHUSDT"


async def test_only_a_changed_symbols_candles_are_sent_again(served: Served) -> None:
    served.engine.candle_lists = {"BTCUSDT": candles("BTCUSDT", "1"), "ETHUSDT": candles("ETHUSDT")}
    async with await hello(served.file) as ws:
        await receive(ws, "candles")
        await receive(ws, "candles")
        await asyncio.sleep(0.05)  # several publish rounds with the same lists
        served.engine.candle_lists = {
            **served.engine.candle_lists,
            "ETHUSDT": candles("ETHUSDT", "2"),
        }
        changed = await receive(ws, "candles")
        assert (changed["symbol"], changed["candles"][0]["close"]) == ("ETHUSDT", "2")
        served.engine.state["mark"] = "60000"
        # Only the state follows: BTCUSDT's unchanged candles weren't sent again.
        assert json.loads(await ws.recv())["type"] == "state"


async def test_candles_of_a_symbol_added_back_are_sent_again(served: Served) -> None:
    btc = candles("BTCUSDT", "1")
    served.engine.candle_lists = {"BTCUSDT": btc}
    async with await hello(served.file) as ws:
        await receive(ws, "candles")
        served.engine.candle_lists = {}  # the strategy was removed
        await asyncio.sleep(0.05)
        served.engine.candle_lists = {"BTCUSDT": btc}
        assert (await receive(ws, "candles"))["symbol"] == "BTCUSDT"


# Commands (D-33)


async def test_emergency_close_fires_the_kill_switch(served: Served) -> None:
    async with await hello(served.file) as ws:
        result = await command(ws, "emergency_close", "abc")
    assert result["id"] == "abc"
    assert result["ok"] is True
    assert served.engine.closes == 1


async def test_lift_halt(served: Served) -> None:
    served.engine.state["halted"] = True
    async with await hello(served.file) as ws:
        assert (await command(ws, "lift_halt"))["message"] == "halt lifted"
        assert (await command(ws, "lift_halt"))["message"] == "not halted"


async def test_lift_halt_refused_at_the_limit(served: Served) -> None:
    served.engine.lift_error = LiftRefused("today's loss is at the limit")
    async with await hello(served.file) as ws:
        result = await command(ws, "lift_halt", 7)
    assert (result["id"], result["ok"]) == (7, False)
    assert "at the limit" in result["message"]


async def test_stop_result_reaches_the_client_before_the_server_stops(served: Served) -> None:
    async with await hello(served.file) as ws:
        await ws.send(json.dumps({"type": "command", "id": 3, "name": "stop_engine"}))
        # What run_engine does once the engine is asked to stop (D-34).
        await asyncio.wait_for(served.engine.stopped.wait(), 2)
        await served.server.flush()
        served.task.cancel()
        result = await receive(ws, "result")
    assert (result["id"], result["ok"]) == (3, True)
    assert "stopping" in result["message"]


async def test_unknown_command_and_bad_message(served: Served) -> None:
    async with await hello(served.file) as ws:
        result = await command(ws, "open_position")
        assert result["ok"] is False
        assert "unknown command" in result["message"]
        await ws.send("not json")
        result = await receive(ws, "result")
    assert (result["id"], result["ok"]) == (None, False)
    assert served.engine.closes == 0


# Symbol commands (D-47)


async def test_set_symbol_passes_its_arguments(served: Served) -> None:
    args = {"symbol": "ETHUSDT", "leverage": 2, "strategy": None}
    async with await hello(served.file) as ws:
        result = await command(ws, "set_symbol", 4, args)
    assert (result["id"], result["ok"], result["message"]) == (4, True, "ETHUSDT added")
    assert served.engine.symbol_args == [args]


async def test_refused_symbol_changes_report_why(served: Served) -> None:
    async with await hello(served.file) as ws:
        out_of_bounds = await command(ws, "set_symbol", 5, {"symbol": "ETHUSDT", "leverage": 11})
        open_position = await command(ws, "remove_symbol", 6, {"symbol": "BTCUSDT"})
    assert out_of_bounds["ok"] is False
    assert "leverage must be between 1 and 10" in out_of_bounds["message"]
    assert open_position["ok"] is False
    assert "open LONG position" in open_position["message"]


async def test_pause_close_and_resume_pass_their_arguments(
    served: Served, caplog: pytest.LogCaptureFixture
) -> None:
    # D-50
    async with await hello(served.file) as ws:
        paused = await command(ws, "pause_symbol", 8, {"symbol": "ETHUSDT"})
        with caplog.at_level(logging.WARNING):
            not_saved = await command(ws, "close_symbol", 9, {"symbol": "ETHUSDT"})
            unknown = await command(ws, "resume_symbol", 10, {"symbol": "XRPUSDT"})
    assert (paused["id"], paused["ok"], paused["message"]) == (8, True, "ETHUSDT paused")
    # The close went through, but the result says the pause won't survive a restart.
    assert (not_saved["ok"], not_saved["message"]) == (
        False,
        "ETHUSDT closed LONG 0.083, but the pause could not be saved",
    )
    assert (unknown["ok"], unknown["message"]) == (
        False,
        "XRPUSDT is not one of the engine's symbols",
    )
    assert "resume_symbol refused" in caplog.text
    assert served.engine.symbol_args == [
        {"symbol": "ETHUSDT"},
        {"symbol": "ETHUSDT"},
        {"symbol": "XRPUSDT"},
    ]


async def test_args_must_be_an_object(served: Served) -> None:
    async with await hello(served.file) as ws:
        result = await command(ws, "set_symbol", 7, ["ETHUSDT"])
    assert (result["id"], result["ok"]) == (7, False)
    assert "args must be an object" in result["message"]
    assert served.engine.symbol_args == []
