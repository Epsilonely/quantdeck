"""`quantdeck-engine check`: a read-only connection check against Binance.

Places no orders and changes no account settings. The user data stream part reuses the
account's listenKey (or creates one, which expires on its own 60 minutes later).
"""

import asyncio
import json
from contextlib import aclosing
from decimal import Decimal

from quantdeck_engine.binance.rest import BinanceRestClient
from quantdeck_engine.binance.streams import kline_stream
from quantdeck_engine.binance.user_stream import UserStreamConnected, user_data_stream
from quantdeck_engine.config import Settings

STREAM_TIMEOUT_SECONDS = 30
SHOWN_FILTERS = ("PRICE_FILTER", "LOT_SIZE", "MIN_NOTIONAL")


class CheckFailed(Exception):
    """The check ran but found a problem."""


async def run_check(settings: Settings, symbol: str, interval: str, count: int) -> None:
    network = "testnet" if settings.testnet else "MAINNET"
    print(f"Network: {network} ({settings.endpoints.rest})")

    async with BinanceRestClient(settings) as client:
        offset = await client.sync_time()
        print(f"Server time OK; local clock offset {offset:+d} ms")

        info = await client.exchange_info()
        symbol_info = next((s for s in info["symbols"] if s["symbol"] == symbol), None)
        if symbol_info is None:
            raise CheckFailed(f"{symbol} is not in exchangeInfo")
        print(f"{symbol}: status {symbol_info['status']}")
        for f in symbol_info["filters"]:
            if f["filterType"] in SHOWN_FILTERS:
                fields = {k: v for k, v in f.items() if k != "filterType"}
                print(f"  {f['filterType']} {json.dumps(fields)}")

        config = await client.account_config()
        if config["dualSidePosition"]:
            print("Position mode: HEDGE (QuantDeck expects one-way mode, D-05)")
        else:
            print("Position mode: one-way")

        balances = [b for b in await client.balances() if Decimal(b["balance"]) != 0]
        print("Balances:" + ("" if balances else " none"))
        for b in balances:
            print(f"  {b['asset']}: balance {b['balance']}, available {b['availableBalance']}")

        positions = [p for p in await client.positions() if Decimal(p["positionAmt"]) != 0]
        print("Open positions:" + ("" if positions else " none"))
        for p in positions:
            print(
                f"  {p['symbol']} {p['positionAmt']} @ {p['entryPrice']}, "
                f"unrealized PnL {p['unRealizedProfit']}"
            )

        print(f"Request weight used in the last minute: {client.used_weight_1m}")

    print(f"Waiting for {count} {symbol} {interval} kline updates...")
    try:
        async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
            async with aclosing(kline_stream(settings.endpoints.ws, symbol, interval)) as stream:
                received = 0
                async for k in stream:
                    state = "closed" if k.closed else "open"
                    print(
                        f"  {k.symbol} {k.interval} O {k.open} H {k.high} L {k.low} "
                        f"C {k.close} V {k.volume} ({state})"
                    )
                    received += 1
                    if received >= count:
                        break
    except TimeoutError:
        raise CheckFailed(f"No kline updates within {STREAM_TIMEOUT_SECONDS}s") from None

    print("Connecting to the user data stream...")
    async with BinanceRestClient(settings) as client:
        try:
            async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
                async with aclosing(user_data_stream(client, settings.endpoints.ws)) as stream:
                    async for event in stream:
                        if isinstance(event, UserStreamConnected):
                            break
        except TimeoutError:
            raise CheckFailed(
                f"User data stream did not connect within {STREAM_TIMEOUT_SECONDS}s"
            ) from None
        print("  connected")
        await client.keepalive_user_stream()
        print("  listenKey keepalive OK")

    print("Check passed.")
