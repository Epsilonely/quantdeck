"""Recent trades for clients (D-51): every order filled in the last 7 days, one row per order.

Display only: nothing here places orders or feeds the risk guard. The rows are rebuilt from
the exchange (D-06), never accumulated from stream events, so a restart or a stream gap
loses nothing:

- `userTrades` gives each symbol's fills: side, price, quantity, realized PnL, commission,
  and the `orderId`, but no client order ID.
- The order's client ID, read once per order from `allOrders`, gives its role (D-21).
- A stop fills through the order the exchange places when it triggers, whose client ID the
  engine didn't choose. The algo order whose `actualOrderId` is that order gives the stop's
  `clientAlgoId`. Liquidations and ADL show in the client ID (`autoclose-`, `adl_autoclose`).

The symbols read are the engine's own, and any other symbol traded in the window (one removed
since, D-47), found from income history at startup or from a fill on the user data stream.
A fill asks for its symbol to be read again a few seconds later, as a closed position asks
for the risk check (D-30); every symbol is read again after a stream reconnect and every few
minutes.

The window's funding payments go with the list, from income history: they aren't orders, so
they are a list of their own. They are read with every symbol, and a few seconds after the
user data stream reports a funding.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from quantdeck_engine.amounts import fixed_point
from quantdeck_engine.binance.rest import REQUEST_ERRORS, BinanceRestClient
from quantdeck_engine.times import DAY_MS, iso_utc
from quantdeck_engine.trading.balance import fetch_income
from quantdeck_engine.trading.order_ids import Role, parse_client_id
from quantdeck_engine.trading.risk import FUNDING_INCOME_TYPES

logger = logging.getLogger(__name__)

WINDOW_DAYS = 7
WINDOW_MS = WINDOW_DAYS * DAY_MS
# `allOrders` and `allAlgoOrders` take periods under 7 days, `userTrades` up to 7.
QUERY_SPAN_MS = WINDOW_MS - 1
# The exchange keeps orders for 90 days, so an older stop can't be found.
ALGO_LOOKBACK_MS = 90 * DAY_MS
PAGE_SIZE = 1000
REFRESH_INTERVAL = 300.0  # seconds between reads of every symbol
# Income records appeared 1.1 s after a fill on testnet (D-30); fills are taken to lag alike.
REFRESH_DELAY = 3.0  # seconds


class TradeRole(StrEnum):
    ENTRY = "entry"
    EXIT = "exit"  # strategy exits, the user's close (D-50), closes after a failed stop
    STOP = "stop"
    KILL = "kill"
    LIQUIDATION = "liquidation"
    ADL = "adl"
    OTHER = "other"  # not the engine's order, or its role couldn't be found


_ROLES = {
    Role.ENTRY: TradeRole.ENTRY,
    Role.EXIT: TradeRole.EXIT,
    Role.STOP: TradeRole.STOP,
    Role.KILL: TradeRole.KILL,
}


def role_from_client_id(client_id: str) -> TradeRole | None:
    """The role a client order ID shows, or None if it shows none: a stop's triggered order
    may carry an ID the exchange made."""
    parsed = parse_client_id(client_id)
    if parsed is not None:
        return _ROLES[parsed.role]
    if client_id.startswith("autoclose-"):
        return TradeRole.LIQUIDATION
    if client_id.startswith("adl_autoclose"):
        return TradeRole.ADL
    return None


@dataclass(frozen=True)
class OrderRole:
    role: TradeRole
    client_id: str | None  # the ID that showed it: the order's own, or its stop's


@dataclass(frozen=True)
class Fill:
    """One `userTrades` row."""

    symbol: str
    trade_id: int
    order_id: int
    side: str  # BUY, SELL
    price: Decimal
    quantity: Decimal
    realized_pnl: Decimal
    commission: Decimal
    commission_asset: str
    time_ms: int

    @classmethod
    def from_user_trade(cls, row: Mapping[str, Any]) -> "Fill":
        return cls(
            symbol=row["symbol"],
            trade_id=int(row["id"]),
            order_id=int(row["orderId"]),
            side=row["side"],
            price=Decimal(row["price"]),
            quantity=Decimal(row["qty"]),
            realized_pnl=Decimal(row["realizedPnl"]),
            commission=Decimal(row["commission"]),
            commission_asset=row["commissionAsset"],
            time_ms=int(row["time"]),
        )


@dataclass(frozen=True)
class OrderTrade:
    """One order's fills together: a row of the list."""

    symbol: str
    order_id: int
    side: str
    role: TradeRole
    client_id: str | None
    time_ms: int  # the first fill
    quantity: Decimal
    price: Decimal  # the average fill price
    realized_pnl: Decimal
    fees: dict[str, Decimal]  # by commission asset
    fills: int

    def to_json(self) -> dict[str, Any]:
        return {
            "time": iso_utc(self.time_ms / 1000),
            "symbol": self.symbol,
            "order_id": self.order_id,
            "client_id": self.client_id,
            "role": str(self.role),
            "side": self.side,
            "quantity": fixed_point(self.quantity),
            "price": fixed_point(self.price),
            "realized_pnl": fixed_point(self.realized_pnl),
            "fees": {asset: fixed_point(amount) for asset, amount in sorted(self.fees.items())},
            "fills": self.fills,
        }


@dataclass(frozen=True)
class FundingPayment:
    """One funding settlement on one symbol, an income history record: received if positive."""

    symbol: str
    amount: Decimal
    asset: str
    time_ms: int
    tran_id: int

    @classmethod
    def from_income(cls, row: Mapping[str, Any]) -> "FundingPayment":
        return cls(
            symbol=row["symbol"],
            amount=Decimal(row["income"]),
            asset=row["asset"],
            time_ms=int(row["time"]),
            tran_id=int(row["tranId"]),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "time": iso_utc(self.time_ms / 1000),
            "symbol": self.symbol,
            "asset": self.asset,
            "amount": fixed_point(self.amount),
        }


def order_trades(
    fills: Iterable[Fill], roles: Mapping[tuple[str, int], OrderRole]
) -> list[OrderTrade]:
    """One row per order, newest first. An order whose role isn't known yet shows as other."""
    orders: dict[tuple[str, int], list[Fill]] = {}
    for fill in fills:
        orders.setdefault((fill.symbol, fill.order_id), []).append(fill)
    rows = [
        _order_trade(group, roles.get(key, OrderRole(TradeRole.OTHER, None)))
        for key, group in orders.items()
    ]
    rows.sort(key=lambda r: (r.time_ms, r.symbol, r.order_id), reverse=True)
    return rows


def _order_trade(fills: list[Fill], role: OrderRole) -> OrderTrade:
    first = min(fills, key=lambda f: (f.time_ms, f.trade_id))
    quantity = sum((f.quantity for f in fills), Decimal(0))
    notional = sum((f.price * f.quantity for f in fills), Decimal(0))
    # The average to the finest tick any fill was priced at.
    exponent = min(int(f.price.as_tuple().exponent) for f in fills)
    price = (notional / quantity).quantize(Decimal(1).scaleb(exponent)) if quantity else first.price
    fees: dict[str, Decimal] = {}
    for fill in fills:
        fees[fill.commission_asset] = fees.get(fill.commission_asset, Decimal(0)) + fill.commission
    return OrderTrade(
        symbol=first.symbol,
        order_id=first.order_id,
        side=first.side,
        role=role.role,
        client_id=role.client_id,
        time_ms=first.time_ms,
        quantity=quantity,
        price=price,
        realized_pnl=sum((f.realized_pnl for f in fills), Decimal(0)),
        fees=fees,
        fills=len(fills),
    )


def trades_payload(
    rows: list[OrderTrade], asset: str, funding: list[FundingPayment] | None = None
) -> dict[str, Any]:
    """What clients get in a `trades` message, totals included: the GUI never adds amounts
    (D-33). Realized PnL is in the margin asset; fees in whatever asset paid them, positive
    when paid; funding as received, newest first, or None while it hasn't been read.

    The net is realized PnL less fees plus funding, while all of them are in the margin asset.
    """
    fees: dict[str, Decimal] = {}
    for row in rows:
        for fee_asset, amount in row.fees.items():
            fees[fee_asset] = fees.get(fee_asset, Decimal(0)) + amount
    realized = sum((r.realized_pnl for r in rows), Decimal(0))
    funded: dict[str, Decimal] | None = None
    net: Decimal | None = None
    if funding is not None:
        funded = {}
        for payment in funding:
            funded[payment.asset] = funded.get(payment.asset, Decimal(0)) + payment.amount
        if {a for a, v in [*fees.items(), *funded.items()] if v} <= {asset}:
            net = realized - fees.get(asset, Decimal(0)) + funded.get(asset, Decimal(0))
    return {
        "window_days": WINDOW_DAYS,
        "asset": asset,
        "totals": {
            "realized_pnl": fixed_point(realized),
            "fees": {fee_asset: fixed_point(amount) for fee_asset, amount in sorted(fees.items())},
            "funding": None
            if funded is None
            else {a: fixed_point(amount) for a, amount in sorted(funded.items())},
            "net": None if net is None else fixed_point(net),
        },
        "trades": [row.to_json() for row in rows],
        "funding": None if funding is None else [payment.to_json() for payment in funding],
    }


class TradeHistory:
    def __init__(
        self,
        client: BinanceRestClient,
        symbols: Callable[[], Iterable[str]],
        asset: str,
        *,
        clock: Callable[[], float] = time.time,
        interval: float = REFRESH_INTERVAL,
        delay: float = REFRESH_DELAY,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._symbols = symbols  # the engine's symbols, asked at every read
        self._asset = asset
        self._clock = clock
        self._interval = interval
        self._delay = delay
        self._sleep = sleep
        self._fills: dict[str, list[Fill]] = {}  # each symbol's fills at its last read
        self._roles: dict[tuple[str, int], OrderRole] = {}  # found once per order
        self._others: set[str] = set()  # symbols to read that the engine doesn't have
        self._funding: list[FundingPayment] | None = None  # at the last read, newest first
        self._discovered = False
        self._complete = False  # every symbol has been read once
        self._requested: set[str] = set()
        self._requested_all = False
        self._requested_funding = False
        self._wake = asyncio.Event()
        # What clients get, None until every symbol has been read once; the same object until
        # the list changes, so the client server sends it only then.
        self.payload: dict[str, Any] | None = None

    def request(self, symbol: str | None = None) -> None:
        """Read `symbol` again soon (a fill on it), or every symbol (a stream reconnect)."""
        if symbol is None:
            self._requested_all = True
        else:
            self._requested.add(symbol)
        self._wake.set()

    def request_funding(self) -> None:
        """Read the funding payments again soon: a funding was settled."""
        self._requested_funding = True
        self._wake.set()

    async def run(self) -> None:
        """Read every symbol at once and every interval, and a requested one a few seconds
        after the request. Failures are logged; the next read tries again."""
        symbols: set[str] | None = None  # None: every symbol
        funding: bool | None = None  # None: with every symbol
        while True:
            self._wake.clear()
            try:
                await self.refresh(symbols, funding=funding)
            except Exception:
                # Display only: a bug here must never stop the engine.
                logger.exception("Recent trades could not be read; trying again at the next read")
            try:
                async with asyncio.timeout(self._interval):
                    await self._wake.wait()
            except TimeoutError:
                symbols, funding = None, None
                continue
            await self._sleep(self._delay)
            symbols = None if self._requested_all else set(self._requested)
            funding = self._requested_all or self._requested_funding
            self._requested.clear()
            self._requested_all = False
            self._requested_funding = False

    async def refresh(
        self, symbols: Iterable[str] | None = None, *, funding: bool | None = None
    ) -> None:
        """Read `symbols` (every symbol if None) from the exchange and rebuild the payload,
        reading the funding payments too if `funding`, which defaults to reading every symbol.
        """
        now = int(self._clock() * 1000)
        own = set(self._symbols())
        if not self._discovered:
            await self._discover(now, own)
        everything = symbols is None
        targets = (own | self._others | set(self._fills)) if symbols is None else set(symbols)
        failed = False  # one of the engine's symbols wasn't read
        for symbol in sorted(targets):
            try:
                fills = await self._read_fills(symbol, now - QUERY_SPAN_MS, now)
                await self._find_roles(symbol, fills, now)
            except REQUEST_ERRORS as e:
                # Another symbol, which may since have been delisted, never holds the list back.
                failed = failed or symbol in own
                logger.warning(
                    "%s: recent trades not read (%s); trying again at the next read", symbol, e
                )
                continue
            if fills or symbol in own:
                self._fills[symbol] = fills
                if symbol not in own:
                    self._others.add(symbol)
            else:
                self._fills.pop(symbol, None)
                self._others.discard(symbol)
        if funding is None:
            funding = everything
        if funding:
            await self._read_funding(now)
        if everything and not failed:
            self._complete = True
        if self._complete:
            self._publish(now)

    def _publish(self, now: int) -> None:
        since = now - WINDOW_MS
        fills = [f for fills in self._fills.values() for f in fills if f.time_ms >= since]
        # An order whose fills have all left the window won't be shown again.
        shown = {(f.symbol, f.order_id) for f in fills}
        self._roles = {key: role for key, role in self._roles.items() if key in shown}
        funding = (
            None
            if self._funding is None
            else [payment for payment in self._funding if payment.time_ms >= since]
        )
        payload = trades_payload(order_trades(fills, self._roles), self._asset, funding)
        if payload != self.payload:
            self.payload = payload

    async def _read_funding(self, now: int) -> None:
        """The window's funding payments on every symbol. A failed read keeps the last ones,
        and never holds the list back: they are shown as unread until a read succeeds."""
        try:
            records = [
                record
                for income_type in sorted(FUNDING_INCOME_TYPES)
                for record in await fetch_income(self._client, now - WINDOW_MS, now, income_type)
            ]
        except REQUEST_ERRORS as e:
            logger.warning("Funding payments not read (%s); trying again at the next read", e)
            return
        payments = [FundingPayment.from_income(r) for r in records]
        payments.sort(key=lambda p: (p.time_ms, p.symbol, p.tran_id), reverse=True)
        self._funding = payments

    async def _discover(self, now: int, own: set[str]) -> None:
        """Other symbols traded in the window, such as one removed since (D-47): every fill
        pays a commission, which income history records with its symbol."""
        try:
            records = await fetch_income(self._client, now - WINDOW_MS, now, "COMMISSION")
        except REQUEST_ERRORS as e:
            logger.warning("Symbols traded in the last %d days not read (%s)", WINDOW_DAYS, e)
            return
        self._others |= {r["symbol"] for r in records if r.get("symbol")} - own
        self._discovered = True

    async def _read_fills(self, symbol: str, start: int, end: int) -> list[Fill]:
        """Every fill on `symbol` from `start` to `end`, paging by time past a full page."""
        fills: dict[int, Fill] = {}
        while True:
            rows = await self._client.user_trades(
                symbol, start_time=start, end_time=end, limit=PAGE_SIZE
            )
            fills.update((int(r["id"]), Fill.from_user_trade(r)) for r in rows)
            if len(rows) < PAGE_SIZE:
                break
            last = max(int(r["time"]) for r in rows)
            if last <= start:
                logger.warning(
                    "%s: a full page of fills in one millisecond; some not shown", symbol
                )
                break
            start = last  # that millisecond again; the trade ID drops what was already read
        return sorted(fills.values(), key=lambda f: f.trade_id)

    async def _find_roles(self, symbol: str, fills: list[Fill], now: int) -> None:
        """The role of each order among `fills` not seen before (D-21)."""
        missing = {f.order_id for f in fills if (symbol, f.order_id) not in self._roles}
        if not missing:
            return
        orders = await self._read_orders(symbol, missing)
        stops: dict[int, str] = {}
        unclear = {
            order_id
            for order_id in missing
            if order_id in orders and role_from_client_id(orders[order_id]) is None
        }
        if unclear:
            stops = await self._find_stops(symbol, unclear, now)
        for order_id in missing:
            client_id = orders.get(order_id)
            if client_id is None:
                continue  # not listed yet: shown as other, and looked up again next time
            role = role_from_client_id(client_id)
            if role is None and order_id in stops:
                client_id = stops[order_id]
                role = role_from_client_id(client_id)
            self._roles[(symbol, order_id)] = OrderRole(role or TradeRole.OTHER, client_id)

    async def _read_orders(self, symbol: str, order_ids: set[int]) -> dict[int, str]:
        """The client order IDs of `order_ids`, from the oldest of them on."""
        found: dict[int, str] = {}
        next_id = min(order_ids)
        while True:
            rows = await self._client.all_orders(symbol, order_id=next_id, limit=PAGE_SIZE)
            found.update((int(r["orderId"]), r["clientOrderId"]) for r in rows)
            if len(rows) < PAGE_SIZE or order_ids.issubset(found):
                return found
            next_id = max(int(r["orderId"]) for r in rows) + 1

    async def _find_stops(self, symbol: str, order_ids: set[int], now: int) -> dict[int, str]:
        """For each of `order_ids` an algo order placed, its `clientAlgoId`. Looks back a week
        at a time, since a stop can rest for weeks before it triggers, up to the 90 days the
        exchange keeps."""
        found: dict[int, str] = {}
        end = now
        oldest = now - ALGO_LOOKBACK_MS
        while end > oldest and not order_ids.issubset(found):
            start = max(end - QUERY_SPAN_MS, oldest)
            rows = await self._client.all_algo_orders(
                symbol, start_time=start, end_time=end, limit=PAGE_SIZE
            )
            if len(rows) == PAGE_SIZE:
                logger.warning(
                    "%s: a full page of algo orders in one week; some stop fills may show as other",
                    symbol,
                )
            for row in rows:
                actual = str(row.get("actualOrderId") or "")
                if actual.isdigit() and int(actual) in order_ids:
                    found[int(actual)] = row["clientAlgoId"]
            end = start - 1
        return found
