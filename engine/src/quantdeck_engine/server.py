"""The WebSocket server for the GUI and, later, the Discord bot (D-32, D-33).

- Listens on 127.0.0.1 on a port the OS assigns. While it runs, `engine/state/connection.json`
  holds the port and a token made new at every start; clients read it to connect.
- A client's first message must be `{"type": "hello", "token": ...}`; anything else closes
  the connection. Connections with an `Origin` header (browsers) are refused at the handshake.
- After the hello the client gets `welcome` (with the catalog of what `set_symbol` accepts,
  D-48), the recent log records, the whole state, the last 7 days' trades (D-51) once the
  engine has read them, and one `candles` message per symbol with a strategy, which also
  carries the strategy's lines (D-54, D-56). The
  state, the trades, and each symbol's candles are sent again whenever they change, and log
  records as they happen.
- Commands are `{"type": "command", "id": ..., "name": ..., "args": {...}}`; the reply is a
  `result` with the same `id`. Names: `emergency_close` (the kill switch, D-27),
  `lift_halt`, `stop_engine` (D-34), `set_symbol` and `remove_symbol` (D-47), and
  `pause_symbol`, `close_symbol`, and `resume_symbol` (D-50). Only the symbol commands
  take `args`.

Message shapes are recorded in `docs/memory-bank/techContext.md`.
"""

import asyncio
import hmac
import json
import logging
import secrets
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from quantdeck_engine.binance.rest import REQUEST_ERRORS
from quantdeck_engine.config import ConfigError, Secret
from quantdeck_engine.paths import CONNECTION_FILE, write_atomic
from quantdeck_engine.symbols import PauseNotSaved, SymbolChangeRefused, catalog
from quantdeck_engine.trading.executor import OrderOutcomeUnclear
from quantdeck_engine.trading.risk import LiftRefused

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
HOST = "127.0.0.1"
SYMBOL_COMMANDS = frozenset(
    {"set_symbol", "remove_symbol", "pause_symbol", "close_symbol", "resume_symbol"}
)
AUTH_TIMEOUT = 5.0  # seconds for a client to send its hello
PUBLISH_INTERVAL = 0.25  # seconds between checks for a changed state
LOG_BACKLOG = 200  # records sent to a client when it connects
OUTBOX_LIMIT = 1000  # messages waiting for one client before it is dropped as too slow
FLUSH_TIMEOUT = 2.0  # seconds to wait for queued messages before the engine stops


class EngineControl(Protocol):
    def snapshot(self) -> dict[str, Any]:
        """The whole state as JSON-ready values; amounts as strings (D-33)."""
        ...

    def trades(self) -> dict[str, Any] | None:
        """The last 7 days' trades as JSON-ready values (D-51), or None until first read.
        The same object until the list changes."""
        ...

    def candles(self) -> dict[str, dict[str, Any]]:
        """Each strategy's closed candles for a chart (D-54), by symbol. The same object per
        symbol until its list changes."""
        ...

    async def emergency_close(self) -> bool:
        """Fire the kill switch; True once every symbol is flat with no open orders."""
        ...

    async def lift_halt(self) -> bool:
        """Lift the kill switch's halt; False if there was none."""
        ...

    async def stop(self) -> None:
        """Ask the engine to stop once any kill switch in progress is done (D-34)."""
        ...

    async def set_symbol(self, args: Mapping[str, Any]) -> str:
        """Add a symbol or change its settings (D-47); returns what was done."""
        ...

    async def remove_symbol(self, args: Mapping[str, Any]) -> str:
        """Remove a flat symbol (D-47); returns what was done."""
        ...

    async def pause_symbol(self, args: Mapping[str, Any]) -> str:
        """Stop a symbol's new entries until resumed (D-50); returns what was done."""
        ...

    async def close_symbol(self, args: Mapping[str, Any]) -> str:
        """Pause a symbol and close its position (D-50); returns what was done."""
        ...

    async def resume_symbol(self, args: Mapping[str, Any]) -> str:
        """Lift the user's pause of a symbol (D-50); returns what was done."""
        ...


class ConnectionFile:
    """Where clients find the server: the port and the token, present only while serving."""

    def __init__(self, path: Path = CONNECTION_FILE) -> None:
        self.path = path

    def write(self, port: int, token: Secret) -> None:
        """Write atomically, like the halt record (D-29). Raises OSError on failure."""
        data = {"protocol": PROTOCOL_VERSION, "port": port, "token": token.reveal()}
        write_atomic(self.path, json.dumps(data, indent=2) + "\n")

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
    # The last state, trades, and candles queued for this client. Kept per client: one that
    # connects before a publish round has seen a change gets it on connect, and not again from
    # that round.
    state: str | None = None
    trades: str | None = None
    candles: dict[str, str] = field(default_factory=dict)  # by symbol


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
        # The catalog comes from code, so it is the same for every client of this engine.
        self._welcome = _dumps(
            {"type": "welcome", "protocol": PROTOCOL_VERSION, "catalog": catalog()}
        )
        self._clients: set[_Client] = set()
        self._trades: tuple[object, str] | None = None  # the engine's trades and their message
        self._candles: dict[str, tuple[object, str]] = {}  # likewise, by symbol
        self.port: int | None = None
        self.listening = asyncio.Event()

    async def run(self) -> None:
        """Serve clients until cancelled. The connection file exists only meanwhile."""
        loop = asyncio.get_running_loop()
        async with serve(self._handle, HOST, 0, origins=[None]) as server:
            self.port = server.sockets[0].getsockname()[1]
            self._file.write(self.port, self._token)
            self._logs.listener = lambda entry: _call_soon(loop, self._post_log, entry)
            logger.info("Client server listening on %s:%d", HOST, self.port)
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
        client.outbox.put_nowait(self._welcome)
        client.outbox.put_nowait(_dumps({"type": "logs", "records": list(self._logs.records)}))
        client.state = self._state_message()
        client.outbox.put_nowait(client.state)
        client.trades = self._trades_message()
        if client.trades is not None:
            client.outbox.put_nowait(client.trades)
        client.candles = self._candles_messages()
        for message in client.candles.values():
            client.outbox.put_nowait(message)
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
            logger.warning("Client refused: no hello with the right token")
            await conn.close(code=1008, reason="unauthorized")
        return accepted

    async def _write(self, client: _Client) -> None:
        with suppress(ConnectionClosed):
            while True:
                await client.conn.send(await client.outbox.get())
                client.outbox.task_done()

    async def flush(self) -> None:
        """Wait until every connected client's queued messages are sent, or FLUSH_TIMEOUT."""
        with suppress(TimeoutError):
            async with asyncio.timeout(FLUSH_TIMEOUT):
                for client in list(self._clients):
                    await client.outbox.join()

    # Commands

    async def _on_message(self, client: _Client, message: str | bytes) -> None:
        command_id: Any = None
        try:
            data = json.loads(message)
            if not isinstance(data, dict) or data.get("type") != "command":
                raise ValueError("expected a command")
            command_id = data.get("id")
            args = data.get("args", {})
            if not isinstance(args, dict):
                raise ValueError("args must be an object")
            ok, text = await self._run_command(data.get("name"), args)
        except ValueError as e:
            ok, text = False, f"bad message: {e}"
        result = {"type": "result", "id": command_id, "ok": ok, "message": text}
        _offer(client, _dumps(result))

    async def _run_command(self, name: object, args: dict[str, Any]) -> tuple[bool, str]:
        commands: dict[object, Callable[[dict[str, Any]], Awaitable[tuple[bool, str]]]] = {
            "emergency_close": lambda _: self._emergency_close(),
            "lift_halt": lambda _: self._lift_halt(),
            "stop_engine": lambda _: self._stop_engine(),
            "set_symbol": self._symbol_command("change", self._engine.set_symbol),
            "remove_symbol": self._symbol_command("removal", self._engine.remove_symbol),
            "pause_symbol": self._symbol_command("pause", self._engine.pause_symbol),
            "close_symbol": self._symbol_command("close and pause", self._engine.close_symbol),
            "resume_symbol": self._symbol_command("resume", self._engine.resume_symbol),
        }
        command = commands.get(name)
        if command is None:
            return False, f"unknown command {name!r}"
        try:
            return await command(args)
        except (LiftRefused, SymbolChangeRefused, ConfigError) as e:
            if name in SYMBOL_COMMANDS:
                logger.warning("%s refused: %s", name, e)
            return False, str(e)
        except PauseNotSaved as e:
            return False, str(e)
        except (*REQUEST_ERRORS, OrderOutcomeUnclear, OSError) as e:
            logger.error("Command %s failed: %s", name, e)
            return False, f"{name} failed: {e}"

    async def _emergency_close(self) -> tuple[bool, str]:
        logger.warning("Emergency close requested by a client")
        if await self._engine.emergency_close():
            return True, "every symbol is flat with no open orders; trading is halted"
        return False, "the kill switch could not flatten every symbol; see the log"

    async def _lift_halt(self) -> tuple[bool, str]:
        if await self._engine.lift_halt():
            return True, "halt lifted"
        return True, "not halted"

    async def _stop_engine(self) -> tuple[bool, str]:
        logger.warning("Engine stop requested by a client")
        await self._engine.stop()
        return True, "engine stopping; exchange-side stops keep protecting open positions"

    def _symbol_command(
        self, what: str, run: Callable[[dict[str, Any]], Awaitable[str]]
    ) -> Callable[[dict[str, Any]], Awaitable[tuple[bool, str]]]:
        async def command(args: dict[str, Any]) -> tuple[bool, str]:
            logger.info("Symbol %s requested by a client: %s", what, json.dumps(args))
            return True, await run(args)

        return command

    # Broadcasting

    async def _publish_forever(self) -> None:
        while True:
            try:
                state = self._state_message()
            except Exception:
                # A display bug must never stop the engine.
                logger.exception("Could not build the state for clients")
            else:
                for client in list(self._clients):
                    if client.state != state:
                        client.state = state
                        _offer(client, state)
            try:
                trades = self._trades_message()
            except Exception:
                logger.exception("Could not build the trades for clients")
            else:
                for client in list(self._clients):
                    if trades is not None and client.trades is not trades:
                        client.trades = trades
                        _offer(client, trades)
            try:
                candles = self._candles_messages()
            except Exception:
                logger.exception("Could not build the candles for clients")
            else:
                for client in list(self._clients):
                    for symbol, message in candles.items():
                        if client.candles.get(symbol) is not message:
                            _offer(client, message)
                    client.candles = candles
            await asyncio.sleep(self._publish_interval)

    def _state_message(self) -> str:
        return _dumps({"type": "state", "state": self._engine.snapshot()})

    def _trades_message(self) -> str | None:
        """Encoded once per list: the engine hands out the same object until it changes."""
        trades = self._engine.trades()
        if trades is None:
            return None
        if self._trades is None or self._trades[0] is not trades:
            self._trades = (trades, _dumps({"type": "trades", **trades}))
        return self._trades[1]

    def _candles_messages(self) -> dict[str, str]:
        """By symbol, each encoded once per list as the trades are. A symbol that loses its
        strategy drops out; clients then stop showing its chart."""
        encoded = {}
        for symbol, candles in self._engine.candles().items():
            cached = self._candles.get(symbol)
            if cached is None or cached[0] is not candles:
                cached = (candles, _dumps({"type": "candles", **candles}))
            encoded[symbol] = cached
        self._candles = encoded
        return {symbol: message for symbol, (_, message) in encoded.items()}

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
