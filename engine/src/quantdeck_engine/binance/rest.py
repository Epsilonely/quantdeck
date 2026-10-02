"""Async REST client for Binance USDⓈ-M Futures.

Covers only the endpoints the engine uses. Every call goes through `_get`, which signs
USER_DATA calls, records rate-limit usage, and turns error responses into `BinanceAPIError`.
Endpoint details are recorded in docs/memory-bank/binanceNotes.md.
"""

import hashlib
import hmac
import time
from collections.abc import Callable, Mapping
from typing import Any, Self
from urllib.parse import urlencode

import httpx

from quantdeck_engine.config import Settings

RECV_WINDOW_MS = 5000
TIMEOUT_SECONDS = 10.0


class BinanceAPIError(Exception):
    """Binance answered with an error status."""

    def __init__(self, status: int, code: int | None, msg: str, path: str) -> None:
        super().__init__(f"{path}: HTTP {status}, code {code}: {msg}")
        self.status = status
        self.code = code
        self.msg = msg


def sign(query: str, secret: str) -> str:
    """HMAC SHA256 signature over the exact query string that will be sent."""
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


class BinanceRestClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._http = httpx.AsyncClient(
            base_url=settings.endpoints.rest, timeout=TIMEOUT_SECONDS, transport=transport
        )
        self._time_offset_ms = 0
        self.used_weight_1m: int | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # Public endpoints

    async def server_time(self) -> int:
        data = await self._get("/fapi/v1/time")
        return int(data["serverTime"])

    async def sync_time(self) -> int:
        """Measure the local clock against the server's and correct signed timestamps.

        Returns the offset in ms; positive means the local clock is behind.
        """
        sent = self._local_ms()
        server = await self.server_time()
        received = self._local_ms()
        self._time_offset_ms = server - (sent + received) // 2
        return self._time_offset_ms

    async def exchange_info(self) -> dict[str, Any]:
        return await self._get("/fapi/v1/exchangeInfo")

    # Signed (USER_DATA) endpoints

    async def account_config(self) -> dict[str, Any]:
        return await self._get("/fapi/v1/accountConfig", signed=True)

    async def balances(self) -> list[dict[str, Any]]:
        return await self._get("/fapi/v3/balance", signed=True)

    async def positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Only symbols with a position or open orders are returned."""
        params = {"symbol": symbol} if symbol else {}
        return await self._get("/fapi/v3/positionRisk", params, signed=True)

    async def _get(
        self,
        path: str,
        params: Mapping[str, str | int] | None = None,
        *,
        signed: bool = False,
    ) -> Any:
        query_params: dict[str, str | int] = dict(params or {})
        headers: dict[str, str] = {}
        if signed:
            query_params["recvWindow"] = RECV_WINDOW_MS
            query_params["timestamp"] = self._local_ms() + self._time_offset_ms
        query = urlencode(query_params)
        if signed:
            # The signature must cover the query string exactly as sent.
            query = f"{query}&signature={sign(query, self._settings.api_secret.reveal())}"
            headers["X-MBX-APIKEY"] = self._settings.api_key.reveal()

        response = await self._http.get(f"{path}?{query}" if query else path, headers=headers)
        self._record_weight(response)
        if response.is_error:
            raise _api_error(response, path)
        return response.json()

    def _local_ms(self) -> int:
        return int(self._clock() * 1000)

    def _record_weight(self, response: httpx.Response) -> None:
        used = response.headers.get("X-MBX-USED-WEIGHT-1M")
        if used is not None and used.isdigit():
            self.used_weight_1m = int(used)


def _api_error(response: httpx.Response, path: str) -> BinanceAPIError:
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        return BinanceAPIError(response.status_code, body.get("code"), body.get("msg", ""), path)
    # WAF blocks (403), IP bans (418), and gateway errors may not be JSON.
    return BinanceAPIError(response.status_code, None, response.text[:200], path)
