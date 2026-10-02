from urllib.parse import parse_qsl

import httpx
import pytest

from quantdeck_engine.binance.rest import BinanceAPIError, BinanceRestClient, sign
from quantdeck_engine.config import parse_settings

SETTINGS = parse_settings({"BINANCE_API_KEY": "test-key", "BINANCE_API_SECRET": "test-secret"})
LOCAL_NOW = 1_700_000_000.0  # seconds


def make_client(handler) -> BinanceRestClient:
    return BinanceRestClient(
        SETTINGS, transport=httpx.MockTransport(handler), clock=lambda: LOCAL_NOW
    )


def test_sign_matches_official_example() -> None:
    # HMAC SHA256 worked example from the USDⓈ-M Futures General Info docs.
    secret = "2b5eb11e18796d12d88f13dc27dbbd02c2cc51ff7059765ed9821957d82bb4d9"
    query = (
        "symbol=BTCUSDT&side=BUY&type=LIMIT&quantity=1&price=9000"
        "&timeInForce=GTC&recvWindow=5000&timestamp=1591702613943"
    )
    expected = "3c661234138461fcc7a7d8746c6558c9842d4e10870d2ecbedf7777cad694af9"
    assert sign(query, secret) == expected


async def test_signed_request_sends_key_and_valid_signature() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[], headers={"X-MBX-USED-WEIGHT-1M": "7"})

    async with make_client(handler) as client:
        await client.balances()

    request = seen[0]
    assert request.url.host == "demo-fapi.binance.com"
    assert request.url.path == "/fapi/v3/balance"
    assert request.headers["X-MBX-APIKEY"] == "test-key"
    unsigned, signature = request.url.query.decode().rsplit("&signature=", 1)
    assert signature == sign(unsigned, "test-secret")
    params = dict(parse_qsl(unsigned))
    assert params["recvWindow"] == "5000"
    assert params["timestamp"] == "1700000000000"
    assert client.used_weight_1m == 7


async def test_public_request_is_not_signed() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"serverTime": 123})

    async with make_client(handler) as client:
        assert await client.server_time() == 123

    assert "X-MBX-APIKEY" not in seen[0].headers
    assert b"signature" not in seen[0].url.query


async def test_sync_time_corrects_signed_timestamps() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fapi/v1/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_002_000})  # 2 s ahead
        seen.append(request)
        return httpx.Response(200, json=[])

    async with make_client(handler) as client:
        assert await client.sync_time() == 2000
        await client.positions()

    params = dict(parse_qsl(seen[0].url.query.decode()))
    assert params["timestamp"] == "1700000002000"


async def test_positions_passes_symbol() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    async with make_client(handler) as client:
        await client.positions("BTCUSDT")

    assert dict(parse_qsl(seen[0].url.query.decode()))["symbol"] == "BTCUSDT"


async def test_error_response_raises_with_binance_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."})

    async with make_client(handler) as client:
        with pytest.raises(BinanceAPIError) as exc_info:
            await client.positions("NOPE")

    assert exc_info.value.status == 400
    assert exc_info.value.code == -1121
    assert exc_info.value.msg == "Invalid symbol."


async def test_non_json_error_still_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="<html>blocked</html>")

    async with make_client(handler) as client:
        with pytest.raises(BinanceAPIError) as exc_info:
            await client.exchange_info()

    assert exc_info.value.status == 403
    assert exc_info.value.code is None


async def test_errors_never_contain_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": -2015, "msg": "Invalid API-key."})

    async with make_client(handler) as client:
        with pytest.raises(BinanceAPIError) as exc_info:
            await client.balances()

    assert "test-key" not in str(exc_info.value)
    assert "test-secret" not in str(exc_info.value)
