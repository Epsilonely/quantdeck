"""The day's base balance (D-23): the margin asset's wallet balance at 00:00 UTC.

Rebuilt from the exchange rather than remembered (D-06): the current wallet balance minus
every income record since 00:00 UTC. Fixed for the rest of that UTC day.
"""

import logging
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from quantdeck_engine.binance.rest import BinanceRestClient
from quantdeck_engine.times import day_start_ms

logger = logging.getLogger(__name__)

INCOME_PAGE_SIZE = 1000


async def fetch_income(
    client: BinanceRestClient, start_ms: int, end_ms: int
) -> list[dict[str, Any]]:
    """Every income record from `start_ms` to `end_ms` (both inclusive), oldest first."""
    incomes: list[dict[str, Any]] = []
    page = 1
    while True:
        batch = await client.income(
            start_time=start_ms, end_time=end_ms, page=page, limit=INCOME_PAGE_SIZE
        )
        incomes.extend(batch)
        if len(batch) < INCOME_PAGE_SIZE:
            return incomes
        page += 1


def base_from_income(
    wallet_balance: Decimal, incomes: Iterable[dict[str, Any]], asset: str
) -> Decimal:
    """Undo today's income records (every type, transfers included) for one asset."""
    return wallet_balance - sum(
        (Decimal(i["income"]) for i in incomes if i["asset"] == asset), Decimal(0)
    )


class BaseBalance:
    def __init__(
        self,
        client: BinanceRestClient,
        asset: str,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client = client
        self.asset = asset
        self._clock = clock
        self._day: int | None = None
        self._value = Decimal(0)

    async def get(self) -> Decimal:
        now_ms = int(self._clock() * 1000)
        day = day_start_ms(now_ms)
        if day != self._day:
            self._value = await self._rebuild(day, now_ms)
            self._day = day
        return self._value

    async def _rebuild(self, day: int, now_ms: int) -> Decimal:
        balances = await self._client.balances()
        wallet = next(
            (Decimal(b["balance"]) for b in balances if b["asset"] == self.asset), Decimal(0)
        )
        incomes = await fetch_income(self._client, day, now_ms)
        base = base_from_income(wallet, incomes, self.asset)
        logger.info(
            "Base balance for %s: %s %s (wallet %s, %d income record(s) since 00:00 UTC)",
            datetime.fromtimestamp(day / 1000, UTC).date(),
            base,
            self.asset,
            wallet,
            len(incomes),
        )
        return base
