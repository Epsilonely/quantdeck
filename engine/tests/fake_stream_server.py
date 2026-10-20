"""A local WebSocket server that answers the close handshake but keeps the TCP connection
open, as Binance's testnet does for 5-9.5 s (binanceNotes.md).

A client that waits for the server to drop the connection waits until its own close
timeout, so a test can measure what a stream's close costs against Binance.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from websockets.http11 import Request
from websockets.server import ServerProtocol


@dataclass
class LingeringServer:
    url: str = ""  # the ws root: any path is accepted
    messages: tuple[str, ...] = ()  # sent on every connection once it opens
    transports: list[asyncio.Transport] = field(default_factory=list)


class _Connection(asyncio.Protocol):
    def __init__(self, server: LingeringServer) -> None:
        self._server = server
        self._protocol = ServerProtocol()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        assert isinstance(transport, asyncio.Transport)
        self._transport = transport
        self._server.transports.append(transport)

    def data_received(self, data: bytes) -> None:
        self._protocol.receive_data(data)
        for event in self._protocol.events_received():
            if isinstance(event, Request):
                self._protocol.send_response(self._protocol.accept(event))
                for message in self._server.messages:
                    self._protocol.send_text(message.encode())
        # The protocol answers a close frame by itself. An empty chunk asks to drop the
        # connection, which is the step left out.
        for chunk in self._protocol.data_to_send():
            if chunk:
                self._transport.write(chunk)

    def eof_received(self) -> bool:
        return True  # keep the connection open after a client's half-close too


async def time_to_cancel(task: asyncio.Task[None]) -> float:
    """Seconds from cancelling `task` until it has finished."""
    loop = asyncio.get_running_loop()
    started = loop.time()
    task.cancel()
    await asyncio.wait({task})
    return loop.time() - started


@asynccontextmanager
async def lingering_server(*messages: str) -> AsyncIterator[LingeringServer]:
    server = LingeringServer(messages=messages)
    loop = asyncio.get_running_loop()
    listener = await loop.create_server(lambda: _Connection(server), "127.0.0.1", 0)
    server.url = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
    try:
        yield server
    finally:
        for transport in server.transports:
            transport.abort()
        listener.close()
        await listener.wait_closed()
