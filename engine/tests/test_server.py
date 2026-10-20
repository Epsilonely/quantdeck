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
from quantdeck_engine.symbols import SymbolChangeRefused, catalog
from quantdeck_engine.trading.risk import LiftRefused


class FakeEngine:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {"halted": False}
        self.closes = 0
        self.lift_error: Exception | None = None
        self.stopped = asyncio.Event()
        self.symbol_args: list[dict[str, Any]] = []

    def snapshot(self) -> dict[str, Any]:
        return dict(self.state)

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


async def test_args_must_be_an_object(served: Served) -> None:
    async with await hello(served.file) as ws:
        result = await command(ws, "set_symbol", 7, ["ETHUSDT"])
    assert (result["id"], result["ok"]) == (7, False)
    assert "args must be an object" in result["message"]
    assert served.engine.symbol_args == []
