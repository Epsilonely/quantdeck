"""The WebSocket server for the GUI and, later, the Discord bot (D-32, D-33).

- Listens on 127.0.0.1 on a port the OS assigns. While it runs, `engine/state/connection.json`
  holds the port and a token made new at every start; clients read it to connect.
- A client's first message must be `{"type": "hello", "token": ...}`; anything else closes
  the connection. Connections with an `Origin` header (browsers) are refused at the handshake.
- After the hello the client gets `welcome`, the recent log records, and the whole state.
  The state is sent again whenever it changes, and log records as they happen.
- Commands are `{"type": "command", "id": ..., "name": ...}`; the reply is a `result` with
  the same `id`. Names: `emergency_close` (the kill switch, D-27) and `lift_halt`.

Message shapes are recorded in `docs/memory-bank/techContext.md`.
"""

import asyncio
import hmac
import json
import logging
import os
import secrets
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from quantdeck_engine.config import Secret
from quantdeck_engine.trading.executor import REQUEST_ERRORS, OrderOutcomeUnclear
from quantdeck_engine.trading.risk import LiftRefused

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
HOST = "127.0.0.1"
AUTH_TIMEOUT = 5.0  # seconds for a client to send its hello
PUBLISH_INTERVAL = 0.25  # seconds between checks for a changed state
LOG_BACKLOG = 200  # records sent to a client when it connects
OUTBOX_LIMIT = 1000  # messages waiting for one client before it is dropped as too slow

# engine/src/quantdeck_engine/server.py -> engine/state, wherever the engine is started from
DEFAULT_CONNECTION_FILE = Path(__file__).resolve().parents[2] / "state" / "connection.json"


class EngineControl(Protocol):
    def snapshot(self) -> dict[str, Any]:
        """The whole state as JSON-ready values; amounts as strings (D-33)."""
        ...

    async def emergency_close(self) -> bool:
        """Fire the kill switch; True once every symbol is flat with no open orders."""
        ...

    async def lift_halt(self) -> bool:
        """Lift the kill switch's halt; False if there was none."""
        ...


class ConnectionFile:
    """Where clients find the server: the port and the token, present only while serving."""

    def __init__(self, path: Path = DEFAULT_CONNECTION_FILE) -> None:
        self.path = path

    def write(self, port: int, token: Secret) -> None:
        """Write atomically, like the halt record (D-29). Raises OSError on failure."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        data = {"protocol": PROTOCOL_VERSION, "port": port, "token": token.reveal()}
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError as e:
            # Harmless: the token is useless once this engine stops (D-32).
            logger.warning("Could not delete the connection file %s: %s", self.path, e)


class LogBuffer(logging.Handler):
    """Keeps the most recent INFO-and-above records and hands each new one to a listener."""

    def __init__(self, capacity: int = LOG_BACKLOG) -> None:
        super().__init__(logging.INFO)
        self.records: deque[dict[str, str]] = deque(maxlen=capacity)
        self.listener: Callable[[dict[str, str]], None] | None = None
        self._formatter = logging.Formatter()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info:
                message += "\n" + self._formatter.formatException(record.exc_info)
            entry = {
                "time": datetime.fromtimestamp(record.created, UTC).isoformat(
                    timespec="milliseconds"
                ),
                "level": record.levelname,
                "logger": record.name,
                "message": message,
            }
            self.records.append(entry)
            if self.listener is not None:
                self.listener(entry)
        except Exception:
            self.handleError(record)


@dataclass(eq=False)
class _Client:
    conn: ServerConnection
    outbox: asyncio.Queue[str] = field(default_factory=lambda: asyncio.Queue(OUTBOX_LIMIT))


class ClientServer:
    def __init__(
        self,
        engine: EngineControl,
        logs: LogBuffer,
        *,
        connection_file: ConnectionFile | None = None,
        publish_interval: float = PUBLISH_INTERVAL,
        auth_timeout: float = AUTH_TIMEOUT,
    ) -> None:
        self._engine = engine
        self._logs = logs
        self._file = connection_file or ConnectionFile()
        self._publish_interval = publish_interval
        self._auth_timeout = auth_timeout
        self._token = Secret(secrets.token_urlsafe(32))
        self._clients: set[_Client] = set()
        self._last_state: str | None = None
        self.port: int | None = None
        self.listening = asyncio.Event()

    async def run(self) -> None:
        """Serve clients until cancelled. The connection file exists only meanwhile."""
        loop = asyncio.get_running_loop()
        async with serve(self._handle, HOST, 0, origins=[None]) as server:
            self.port = server.sockets[0].getsockname()[1]
            self._file.write(self.port, self._token)
            self._logs.listener = lambda entry: _call_soon(loop, self._post_log, entry)
            logger.info("Client server listening on %s:%d (D-32)", HOST, self.port)
            self.listening.set()
            try:
                await self._publish_forever()
            finally:
                self._logs.listener = None
                self._file.clear()

    # Connections

    async def _handle(self, conn: ServerConnection) -> None:
        if not await self._authenticate(conn):
            return
        client = _Client(conn)
        # Registered and given its first messages without an await in between, so nothing
        # broadcast in the meantime lands ahead of them.
        self._clients.add(client)
        client.outbox.put_nowait(_dumps({"type": "welcome", "protocol": PROTOCOL_VERSION}))
        client.outbox.put_nowait(_dumps({"type": "logs", "records": list(self._logs.records)}))
        client.outbox.put_nowait(self._state_message())
        writer = asyncio.create_task(self._write(client))
        logger.info("Client connected (%d connected)", len(self._clients))
        try:
            async for message in conn:
                await self._on_message(client, message)
        except ConnectionClosed:
            pass
        finally:
            self._clients.discard(client)
            writer.cancel()
            with suppress(asyncio.CancelledError):
                await writer
            logger.info("Client disconnected (%d connected)", len(self._clients))

    async def _authenticate(self, conn: ServerConnection) -> bool:
        try:
            async with asyncio.timeout(self._auth_timeout):
                data = json.loads(await conn.recv())
            token = data.get("token") if data.get("type") == "hello" else None
            accepted = isinstance(token, str) and hmac.compare_digest(
                token.encode(), self._token.reveal().encode()
            )
        except (TimeoutError, ValueError, AttributeError, ConnectionClosed):
            accepted = False
        if not accepted:
            logger.warning("Client refused: no hello with the right token (D-32)")
            await conn.close(code=1008, reason="unauthorized")
        return accepted

    async def _write(self, client: _Client) -> None:
        with suppress(ConnectionClosed):
            while True:
                await client.conn.send(await client.outbox.get())

    # Commands

    async def _on_message(self, client: _Client, message: str | bytes) -> None:
        command_id: Any = None
        try:
            data = json.loads(message)
            if not isinstance(data, dict) or data.get("type") != "command":
                raise ValueError("expected a command")
            command_id = data.get("id")
            ok, text = await self._run_command(data.get("name"))
        except ValueError as e:
            ok, text = False, f"bad message: {e}"
        result = {"type": "result", "id": command_id, "ok": ok, "message": text}
        _offer(client, _dumps(result))

    async def _run_command(self, name: object) -> tuple[bool, str]:
        commands: dict[object, Callable[[], Awaitable[tuple[bool, str]]]] = {
            "emergency_close": self._emergency_close,
            "lift_halt": self._lift_halt,
        }
        command = commands.get(name)
        if command is None:
            return False, f"unknown command {name!r}"
        try:
            return await command()
        except LiftRefused as e:
            return False, str(e)
        except (*REQUEST_ERRORS, OrderOutcomeUnclear, OSError) as e:
            logger.error("Command %s failed: %s", name, e)
            return False, f"{name} failed: {e}"

    async def _emergency_close(self) -> tuple[bool, str]:
        logger.warning("Emergency close requested by a client (D-33)")
        if await self._engine.emergency_close():
            return True, "every symbol is flat with no open orders; trading is halted"
        return False, "the kill switch could not flatten every symbol; see the log"

    async def _lift_halt(self) -> tuple[bool, str]:
        if await self._engine.lift_halt():
            return True, "halt lifted"
        return True, "not halted"

    # Broadcasting

    async def _publish_forever(self) -> None:
        while True:
            try:
                message = self._state_message()
            except Exception:
                # A display bug must never stop the engine.
                logger.exception("Could not build the state for clients")
            else:
                if message != self._last_state:
                    self._last_state = message
                    self._post(message)
            await asyncio.sleep(self._publish_interval)

    def _state_message(self) -> str:
        return _dumps({"type": "state", "state": self._engine.snapshot()})

    def _post_log(self, entry: dict[str, str]) -> None:
        self._post(_dumps({"type": "logs", "records": [entry]}))

    def _post(self, message: str) -> None:
        for client in list(self._clients):
            _offer(client, message)


def _offer(client: _Client, message: str) -> None:
    try:
        client.outbox.put_nowait(message)
    except asyncio.QueueFull:
        # Too slow to keep up; never log here, since logging posts to clients.
        client.conn.transport.abort()


def _call_soon(loop: asyncio.AbstractEventLoop, callback: Callable[..., None], *args: Any) -> None:
    """Run `callback` on the loop, whichever thread logged; skip once the loop is closed."""
    with suppress(RuntimeError):
        loop.call_soon_threadsafe(callback, *args)


def _dumps(message: dict[str, Any]) -> str:
    return json.dumps(message, separators=(",", ":"))
