"""Async REST client for Binance USDⓈ-M Futures.

Covers only the endpoints the engine uses. Every call goes through `_request`, which signs
USER_DATA calls, sends the API key header with USER_STREAM calls, records rate-limit usage,
and turns error responses into `BinanceAPIError`. Endpoint details are recorded in
docs/memory-bank/binanceNotes.md.
"""

import hashlib
import hmac
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from decimal import Decimal
from typing import Any, Self
from urllib.parse import urlencode

import httpx

from quantdeck_engine.config import Secret, Settings

RECV_WINDOW_MS = 5000
TIMEOUT_SECONDS = 10.0

# Error codes the docs mark as "execution status unknown" (General Info, Error Codes).
UNKNOWN_OUTCOME_CODES = {-1006, -1007}
NO_SUCH_ORDER = -2013
NO_NEED_TO_CHANGE_MARGIN_TYPE = -4046
# A stop whose trigger price is already crossed. The docs name -2021 only for trailing
# stops; -4142 is the algo-order message. Treat both as "crossed".
STOP_WOULD_TRIGGER_CODES = {-2021, -4142}

logger = logging.getLogger(__name__)


class BinanceAPIError(Exception):
    """Binance answered with an error status."""

    def __init__(self, status: int, code: int | None, msg: str, path: str) -> None:
        super().__init__(f"{path}: HTTP {status}, code {code}: {msg}")
        self.status = status
        self.code = code
        self.msg = msg


# What a request can raise: an error answer, or a transport failure or timeout.
REQUEST_ERRORS = (BinanceAPIError, httpx.HTTPError)


class UnknownSymbol(Exception):
    """The exchange doesn't list the symbol."""


def sign(query: str, secret: str) -> str:
    """HMAC SHA256 signature over the exact query string that will be sent."""
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def decimal_param(value: Decimal) -> str:
    """Format a Decimal for a request parameter: plain digits, never exponent notation."""
    text = f"{value.normalize():f}"
    return "0" if text in ("-0", "0") else text


def outcome_unknown(error: Exception) -> bool:
    """Whether a failed order request may still have been executed (D-20).

    Per General Info: a timeout or lost connection, HTTP 408, and 5xx answers leave the
    outcome unknown (one 503 variant is a definite failure, but looking it up is harmless).
    Other 4xx answers are definite rejections.
    """
    if isinstance(error, httpx.TimeoutException | httpx.TransportError):
        return True
    if isinstance(error, BinanceAPIError):
        return error.status == 408 or error.status >= 500 or error.code in UNKNOWN_OUTCOME_CODES
    return False


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
        data = await self._request("GET", "/fapi/v1/time")
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
        return await self._request("GET", "/fapi/v1/exchangeInfo")

    async def symbol_info(self, symbol: str) -> dict[str, Any]:
        """The symbol's `exchangeInfo` entry: status, margin asset, filters. Raises
        `UnknownSymbol` if the exchange doesn't list it."""
        return (await self.symbol_infos([symbol]))[symbol]

    async def symbol_infos(self, symbols: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Each symbol's `exchangeInfo` entry, from one request: the endpoint lists every
        symbol whatever is asked. Raises `UnknownSymbol` if the exchange doesn't list one."""
        listed = {entry["symbol"]: entry for entry in (await self.exchange_info())["symbols"]}
        found = {}
        for symbol in symbols:
            if symbol not in listed:
                raise UnknownSymbol(f"{symbol} is not in exchangeInfo")
            found[symbol] = listed[symbol]
        return found

    async def klines(
        self,
        symbol: str,
        interval: str,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 500,
    ) -> list[list[Any]]:
        """Candle rows, oldest first; parse them with `streams.kline_from_row`.

        `endTime` is matched against open times. Without times, the most recent candles;
        the last one can still be open. Weight 1 to 10 by `limit` (max 1500).
        """
        params: dict[str, str | int] = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        return await self._request("GET", "/fapi/v1/klines", params)

    # Signed (USER_DATA) endpoints

    async def account_config(self) -> dict[str, Any]:
        return await self._request("GET", "/fapi/v1/accountConfig", signed=True)

    async def balances(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/fapi/v3/balance", signed=True)

    async def positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Only symbols with a position or open orders are returned.

        v3 has no `leverage` or `marginType`; read those with `symbol_config`.
        """
        params = {"symbol": symbol} if symbol else {}
        return await self._request("GET", "/fapi/v3/positionRisk", params, signed=True)

    async def mark_price(self, symbol: str) -> Decimal:
        data = await self._request("GET", "/fapi/v1/premiumIndex", {"symbol": symbol})
        return Decimal(data["markPrice"])

    async def symbol_config(self, symbol: str) -> dict[str, Any]:
        """Margin type (`ISOLATED`/`CROSSED`) and leverage for one symbol. Weight 5."""
        data = await self._request("GET", "/fapi/v1/symbolConfig", {"symbol": symbol}, signed=True)
        return data[0]

    async def leverage_brackets(self, symbol: str) -> list[dict[str, Any]]:
        """Notional brackets. Their numbers are JSON numbers, not strings."""
        data = await self._request(
            "GET", "/fapi/v1/leverageBracket", {"symbol": symbol}, signed=True
        )
        # The docs show an object when `symbol` is sent and an array otherwise.
        entry = data if isinstance(data, dict) else next(d for d in data if d["symbol"] == symbol)
        return entry["brackets"]

    async def income(
        self,
        *,
        start_time: int,
        end_time: int,
        page: int,
        limit: int = 1000,
        income_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Income records (realized PnL, commission, funding, transfers, ...), oldest first;
        every type unless `income_type` names one.

        Weight 30. Only the last three months are kept.
        """
        params: dict[str, str | int] = {
            "startTime": start_time,
            "endTime": end_time,
            "page": page,
            "limit": limit,
        }
        if income_type is not None:
            params["incomeType"] = income_type
        return await self._request("GET", "/fapi/v1/income", params, signed=True)

    async def user_trades(
        self,
        symbol: str,
        *,
        start_time: int,
        end_time: int,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """The account's fills on one symbol: side, price, quantity, realized PnL, commission,
        and the `orderId`, but no client order ID. Weight 5.

        `endTime - startTime` may be at most 7 days, and only the last 3 months are kept.
        """
        params = {"symbol": symbol, "startTime": start_time, "endTime": end_time, "limit": limit}
        return await self._request("GET", "/fapi/v1/userTrades", params, signed=True)

    async def all_orders(
        self, symbol: str, *, order_id: int, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """The symbol's orders of any status from `order_id` on, with their `clientOrderId`.
        Weight 5.

        Orders older than 90 days, and cancelled or expired ones without fills older than 3
        days, are gone.
        """
        params = {"symbol": symbol, "orderId": order_id, "limit": limit}
        return await self._request("GET", "/fapi/v1/allOrders", params, signed=True)

    async def all_algo_orders(
        self, symbol: str, *, start_time: int, end_time: int, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """The symbol's algo (conditional) orders of any status, stops included; `actualOrderId`
        is the order a triggered one placed. Weight 5.

        The period must be under 7 days. Kept as long as `all_orders` keeps orders.
        """
        params = {"symbol": symbol, "startTime": start_time, "endTime": end_time, "limit": limit}
        return await self._request("GET", "/fapi/v1/allAlgoOrders", params, signed=True)

    async def open_orders(self, symbol: str) -> list[dict[str, Any]]:
        return await self._request("GET", "/fapi/v1/openOrders", {"symbol": symbol}, signed=True)

    async def open_algo_orders(self, symbol: str) -> list[dict[str, Any]]:
        return await self._request(
            "GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol}, signed=True
        )

    async def query_order(self, symbol: str, client_order_id: str) -> dict[str, Any]:
        """Raises `BinanceAPIError` with code -2013 if the order doesn't exist."""
        params = {"symbol": symbol, "origClientOrderId": client_order_id}
        return await self._request("GET", "/fapi/v1/order", params, signed=True)

    # Signed (TRADE) endpoints. Every safety-relevant parameter is spelled out here (D-10).

    async def market_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        client_order_id: str,
        *,
        reduce_only: bool,
    ) -> dict[str, Any]:
        """A MARKET order. RESULT makes Binance answer with the final fill (FILLED)."""
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": decimal_param(quantity),
            "reduceOnly": "true" if reduce_only else "false",
            "newClientOrderId": client_order_id,
            "newOrderRespType": "RESULT",
        }
        return await self._request("POST", "/fapi/v1/order", params, signed=True)

    async def stop_market_close_position(
        self, symbol: str, side: str, trigger_price: Decimal, client_algo_id: str
    ) -> dict[str, Any]:
        """An exchange-side stop that closes the whole position when it triggers (D-18).

        `side` is the closing side: SELL closes a long, BUY a short. `closePosition` can't be
        combined with `quantity` or `reduceOnly`, so neither is sent.
        """
        params = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": side,
            "type": "STOP_MARKET",
            "triggerPrice": decimal_param(trigger_price),
            "workingType": "MARK_PRICE",
            "closePosition": "true",
            "priceProtect": "false",
            "clientAlgoId": client_algo_id,
        }
        return await self._request("POST", "/fapi/v1/algoOrder", params, signed=True)

    async def cancel_algo_order(self, client_algo_id: str) -> dict[str, Any]:
        params = {"clientAlgoId": client_algo_id}
        return await self._request("DELETE", "/fapi/v1/algoOrder", params, signed=True)

    async def cancel_order(self, symbol: str, client_order_id: str) -> dict[str, Any]:
        params = {"symbol": symbol, "origClientOrderId": client_order_id}
        return await self._request("DELETE", "/fapi/v1/order", params, signed=True)

    async def cancel_all_open_orders(self, symbol: str) -> dict[str, Any]:
        """Cancel every regular open order on the symbol. Weight 1."""
        params = {"symbol": symbol}
        return await self._request("DELETE", "/fapi/v1/allOpenOrders", params, signed=True)

    async def cancel_all_algo_open_orders(self, symbol: str) -> dict[str, Any]:
        """Cancel every open algo (conditional) order on the symbol, stops included. Weight 1."""
        params = {"symbol": symbol}
        return await self._request("DELETE", "/fapi/v1/algoOpenOrders", params, signed=True)

    async def change_margin_type(self, symbol: str, margin_type: str) -> None:
        """Code -4046 means it already is; -4047/-4048: open orders or a position exist."""
        params = {"symbol": symbol, "marginType": margin_type}
        await self._request("POST", "/fapi/v1/marginType", params, signed=True)

    async def change_leverage(self, symbol: str, leverage: int) -> dict[str, Any]:
        params = {"symbol": symbol, "leverage": leverage}
        return await self._request("POST", "/fapi/v1/leverage", params, signed=True)

    # User data stream (USER_STREAM: API key header, no signature)

    async def start_user_stream(self) -> Secret:
        """Return the account's listenKey, creating one if none is active.

        An account has one listenKey: while it is active, this returns it again and extends
        it by 60 minutes.
        """
        data = await self._request("POST", "/fapi/v1/listenKey", api_key=True)
        return Secret(data["listenKey"])

    async def keepalive_user_stream(self) -> None:
        """Extend the active listenKey by 60 minutes. Code -1125: no listenKey is active."""
        await self._request("PUT", "/fapi/v1/listenKey", api_key=True)

    async def _request(
        self,
        method: str,
        path: str,
        params: Mapping[str, str | int] | None = None,
        *,
        signed: bool = False,
        api_key: bool = False,
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
        if signed or api_key:
            headers["X-MBX-APIKEY"] = self._settings.api_key.reveal()

        started = time.perf_counter()
        url = f"{path}?{query}" if query else path
        response = await self._http.request(method, url, headers=headers)
        elapsed_ms = (time.perf_counter() - started) * 1000
        self._record_weight(response)
        # Path only: a signed request's query string carries the signature.
        logger.debug(
            "%s %s -> HTTP %d in %.0f ms, weight 1m: %s",
            method,
            path,
            response.status_code,
            elapsed_ms,
            self.used_weight_1m,
        )
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
