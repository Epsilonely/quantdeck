"""An in-memory stand-in for the Binance endpoints the executor uses.

Market orders fill at the mark price. Failures can be queued per endpoint: an exception or
response is returned instead of handling the request, or, with `after=True`, the request is
handled first and its response then lost (an executed order with an unknown outcome).
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl

import httpx

from quantdeck_engine.binance.rest import BinanceRestClient
from quantdeck_engine.config import SymbolConfig, TradingConfig, parse_settings
from quantdeck_engine.trading.balance import BaseBalance
from quantdeck_engine.trading.executor import EntryGate, SymbolTrader
from quantdeck_engine.trading.order_ids import ClientIdFactory
from quantdeck_engine.trading.rules import SymbolRules

SETTINGS = parse_settings({"BINANCE_API_KEY": "test-key", "BINANCE_API_SECRET": "test-secret"})

# The rules `FakeExchange.exchange_info` answers with.
RULES = SymbolRules(
    symbol="BTCUSDT",
    margin_asset="USDT",
    tick_size=Decimal("0.1"),
    step_size=Decimal("0.001"),
    min_qty=Decimal("0.001"),
    max_qty=Decimal(120),
    min_notional=Decimal(100),
)
TRADING = TradingConfig(
    risk_per_trade_percent=Decimal(1),
    daily_loss_limit_percent=Decimal(3),
    max_notional_multiple=Decimal(2),
)
BTC = SymbolConfig("BTCUSDT", 5)
SYMBOLS = {"BTCUSDT": BTC}

BRACKETS = [
    {
        "bracket": 1,
        "initialLeverage": 125,
        "notionalCap": 50000,
        "notionalFloor": 0,
        "maintMarginRatio": 0.004,
        "cum": 0.0,
    },
    {
        "bracket": 2,
        "initialLeverage": 100,
        "notionalCap": 600000,
        "notionalFloor": 50000,
        "maintMarginRatio": 0.005,
        "cum": 50.0,
    },
]


CANCEL_ALL_DONE = {"code": 200, "msg": "The operation of cancel all open order is done."}


def api_error(status: int, code: int, msg: str = "error") -> httpx.Response:
    return httpx.Response(status, json={"code": code, "msg": msg})


def income(income_type: str, amount: str, asset: str = "USDT") -> dict[str, Any]:
    return {"symbol": "BTCUSDT", "incomeType": income_type, "income": amount, "asset": asset}


@dataclass
class Failure:
    # A callable runs when the request arrives, e.g. to change state at that moment.
    result: httpx.Response | Exception | Callable[[], httpx.Response]
    after: bool = False  # handle the request, then fail anyway


@dataclass
class FakeExchange:
    symbol: str = "BTCUSDT"
    mark: Decimal = Decimal(60000)
    position_amount: Decimal = Decimal(0)
    entry_price: Decimal = Decimal(0)
    liquidation_price: Decimal = Decimal(0)
    margin_type: str = "ISOLATED"
    leverage: int = 5
    wallet: Decimal = Decimal(5000)
    available: Decimal = Decimal(5000)
    hedge_mode: bool = False
    margin_asset: str = "USDT"
    contract_type: str = "PERPETUAL"
    status: str = "TRADING"
    # More listed symbols, by name, with their leverage. Flat unless `add_position` opens one;
    # `exchangeInfo` gives them the same rules as `symbol`.
    other_symbols: dict[str, int] = field(default_factory=dict)
    incomes: list[dict[str, Any]] = field(default_factory=list)
    klines: list[list[Any]] = field(default_factory=list)  # REST rows, oldest first
    open_orders: list[dict[str, Any]] = field(default_factory=list)
    algo_orders: list[dict[str, Any]] = field(default_factory=list)  # every symbol's
    # `positionRisk` rows for symbols other than `symbol`, which no order here changes.
    other_positions: list[dict[str, Any]] = field(default_factory=list)
    orders: dict[str, dict[str, Any]] = field(default_factory=dict)  # every order, by client ID
    # Trade history (D-51): `userTrades` rows, older orders for `allOrders`, and algo orders no
    # longer open for `allAlgoOrders`, every symbol's.
    user_trades: list[dict[str, Any]] = field(default_factory=list)
    past_orders: list[dict[str, Any]] = field(default_factory=list)
    past_algo_orders: list[dict[str, Any]] = field(default_factory=list)
    failures: dict[tuple[str, str], list[Failure]] = field(default_factory=dict)
    requests: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)

    def client(self) -> BinanceRestClient:
        return BinanceRestClient(SETTINGS, transport=httpx.MockTransport(self.handle))

    def fail(self, method: str, path: str, *failures: Failure) -> None:
        self.failures.setdefault((method, path), []).extend(failures)

    def calls(self, method: str, path: str) -> list[dict[str, str]]:
        return [p for m, pa, p in self.requests if (m, pa) == (method, path)]

    def add_stop(
        self,
        side: str,
        trigger: str,
        client_algo_id: str = "qd_s_BTCUSDT_1_00",
        symbol: str | None = None,
    ) -> None:
        self.algo_orders.append(self._algo(side, trigger, client_algo_id, symbol))

    def add_position(self, symbol: str, amount: str, entry: str) -> None:
        """An open position on another symbol (negative amount = short)."""
        self.other_positions.append(
            {
                "symbol": symbol,
                "positionSide": "BOTH",
                "positionAmt": amount,
                "entryPrice": entry,
                "markPrice": entry,
                "liquidationPrice": "0",
            }
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        params = dict(parse_qsl(request.url.query.decode()))
        key = (request.method, request.url.path)
        self.requests.append((*key, params))
        queued = self.failures.get(key)
        if queued:
            failure = queued.pop(0)
            if failure.after:
                self._route(key, params)
            if isinstance(failure.result, Exception):
                raise failure.result
            if callable(failure.result):
                return failure.result()
            return failure.result
        return self._route(key, params)

    def _route(self, key: tuple[str, str], p: dict[str, str]) -> httpx.Response:
        match key:
            case ("GET", "/fapi/v1/time"):
                return httpx.Response(200, json={"serverTime": 1_792_000_000_000})
            case ("GET", "/fapi/v1/accountConfig"):
                return httpx.Response(200, json={"dualSidePosition": self.hedge_mode})
            case ("GET", "/fapi/v1/exchangeInfo"):
                infos = [self._symbol_info(self.symbol)]
                infos += [self._symbol_info(s) for s in self.other_symbols]
                return httpx.Response(200, json={"symbols": infos})
            case ("GET", "/fapi/v3/positionRisk"):
                rows = [self._position()] if self.position_amount != 0 else []
                rows += self.other_positions
                if "symbol" in p:
                    rows = [r for r in rows if r["symbol"] == p["symbol"]]
                return httpx.Response(200, json=rows)
            case ("GET", "/fapi/v1/premiumIndex"):
                price = {"symbol": p["symbol"], "markPrice": str(self.mark)}
                return httpx.Response(200, json=price)
            case ("GET", "/fapi/v1/symbolConfig"):
                other = p["symbol"] != self.symbol
                config = {
                    "symbol": p["symbol"],
                    "marginType": "ISOLATED" if other else self.margin_type,
                    "isAutoAddMargin": False,
                    "leverage": self.other_symbols[p["symbol"]] if other else self.leverage,
                    "maxNotionalValue": "1000000",
                }
                return httpx.Response(200, json=[config])
            case ("GET", "/fapi/v1/leverageBracket"):
                return httpx.Response(200, json={"symbol": p["symbol"], "brackets": BRACKETS})
            case ("GET", "/fapi/v3/balance"):
                balance = {
                    "asset": "USDT",
                    "balance": str(self.wallet),
                    "availableBalance": str(self.available),
                }
                return httpx.Response(200, json=[balance])
            case ("GET", "/fapi/v1/income"):
                page, limit = int(p["page"]), int(p["limit"])
                rows = self.incomes
                if "incomeType" in p:
                    rows = [i for i in rows if i.get("incomeType") == p["incomeType"]]
                return httpx.Response(200, json=rows[(page - 1) * limit : page * limit])
            case ("GET", "/fapi/v1/userTrades"):
                start, end = int(p["startTime"]), int(p["endTime"])
                rows = [
                    t
                    for t in self.user_trades
                    if t["symbol"] == p["symbol"] and start <= t["time"] <= end
                ]
                rows.sort(key=lambda t: (t["time"], t["id"]))
                return httpx.Response(200, json=rows[: int(p["limit"])])
            case ("GET", "/fapi/v1/allOrders"):
                rows = [
                    o
                    for o in [*self.orders.values(), *self.past_orders]
                    if o["symbol"] == p["symbol"] and o["orderId"] >= int(p.get("orderId", 0))
                ]
                rows.sort(key=lambda o: o["orderId"])
                return httpx.Response(200, json=rows[: int(p["limit"])])
            case ("GET", "/fapi/v1/allAlgoOrders"):
                # Filtered by creation time; a row without one is in every period.
                start, end = int(p["startTime"]), int(p["endTime"])
                rows = [
                    a
                    for a in [*self.algo_orders, *self.past_algo_orders]
                    if a["symbol"] == p["symbol"] and start <= a.get("createTime", start) <= end
                ]
                return httpx.Response(200, json=rows[: int(p["limit"])])
            case ("GET", "/fapi/v1/klines"):
                # The most recent rows only, as without startTime (the warm-up).
                return httpx.Response(200, json=self.klines[-int(p["limit"]) :])
            case ("GET", "/fapi/v1/openOrders"):
                return httpx.Response(200, json=self.open_orders)
            case ("GET", "/fapi/v1/openAlgoOrders"):
                orders = [a for a in self.algo_orders if a["symbol"] == p["symbol"]]
                return httpx.Response(200, json=orders)
            case ("GET", "/fapi/v1/order"):
                order = self.orders.get(p["origClientOrderId"])
                if order is None:
                    return api_error(400, -2013, "Order does not exist.")
                return httpx.Response(200, json=order)
            case ("POST", "/fapi/v1/order"):
                return self._market_order(p)
            case ("POST", "/fapi/v1/algoOrder"):
                if any(a["clientAlgoId"] == p["clientAlgoId"] for a in self.algo_orders):
                    return api_error(400, -4116, "clientOrderId is duplicated.")
                algo = self._algo(p["side"], p["triggerPrice"], p["clientAlgoId"])
                self.algo_orders.append(algo)
                return httpx.Response(200, json=algo)
            case ("DELETE", "/fapi/v1/algoOrder"):
                self.algo_orders = [
                    a for a in self.algo_orders if a["clientAlgoId"] != p["clientAlgoId"]
                ]
                return httpx.Response(200, json={"code": "200", "msg": "success"})
            case ("DELETE", "/fapi/v1/order"):
                self.open_orders = [
                    o for o in self.open_orders if o["clientOrderId"] != p["origClientOrderId"]
                ]
                return httpx.Response(200, json={})
            case ("DELETE", "/fapi/v1/allOpenOrders"):
                self.open_orders = []
                return httpx.Response(200, json=CANCEL_ALL_DONE)
            case ("DELETE", "/fapi/v1/algoOpenOrders"):
                self.algo_orders = []
                return httpx.Response(200, json=CANCEL_ALL_DONE)
            case ("POST", "/fapi/v1/marginType"):
                if self.margin_type == p["marginType"]:
                    return api_error(400, -4046, "No need to change margin type.")
                self.margin_type = p["marginType"]
                return httpx.Response(200, json={"code": 200, "msg": "success"})
            case ("POST", "/fapi/v1/leverage"):
                if p["symbol"] == self.symbol:
                    self.leverage = int(p["leverage"])
                else:
                    self.other_symbols[p["symbol"]] = int(p["leverage"])
                return httpx.Response(200, json={"leverage": int(p["leverage"])})
        raise AssertionError(f"unexpected request {key}")

    def _market_order(self, p: dict[str, str]) -> httpx.Response:
        quantity = Decimal(p["quantity"])
        if p["reduceOnly"] == "true":
            closing_side = "SELL" if self.position_amount > 0 else "BUY"
            if self.position_amount == 0 or p["side"] != closing_side:
                return api_error(400, -2022, "ReduceOnly Order is rejected.")
            quantity = min(quantity, abs(self.position_amount))
        signed = quantity if p["side"] == "BUY" else -quantity
        if self.position_amount == 0:
            self.entry_price = self.mark
            self.liquidation_price = self.mark * (Decimal("0.8") if signed > 0 else Decimal("1.2"))
        self.position_amount += signed
        order = {
            "clientOrderId": p["newClientOrderId"],
            "orderId": len(self.orders) + 1,
            "symbol": self.symbol,
            "side": p["side"],
            "type": "MARKET",
            "status": "FILLED",
            "origQty": p["quantity"],
            "executedQty": str(abs(signed)),
            "avgPrice": str(self.mark),
            "reduceOnly": p["reduceOnly"] == "true",
        }
        self.orders[p["newClientOrderId"]] = order
        return httpx.Response(200, json=order)

    def _symbol_info(self, symbol: str) -> dict[str, Any]:
        """The same rules as `RULES`."""
        lot = {"stepSize": "0.001", "minQty": "0.001", "maxQty": "120"}
        own = symbol == self.symbol
        return {
            "symbol": symbol,
            "status": self.status if own else "TRADING",
            "contractType": self.contract_type if own else "PERPETUAL",
            "marginAsset": self.margin_asset if own else "USDT",
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                {"filterType": "LOT_SIZE", **lot},
                {"filterType": "MARKET_LOT_SIZE", **lot},
                {"filterType": "MIN_NOTIONAL", "notional": "100"},
            ],
        }

    def _position(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "positionSide": "BOTH",
            "positionAmt": str(self.position_amount),
            "entryPrice": str(self.entry_price),
            "markPrice": str(self.mark),
            "liquidationPrice": str(self.liquidation_price),
        }

    def _algo(
        self, side: str, trigger: str, client_algo_id: str, symbol: str | None = None
    ) -> dict[str, Any]:
        return {
            "algoId": len(self.algo_orders) + 100,
            "clientAlgoId": client_algo_id,
            "algoType": "CONDITIONAL",
            "orderType": "STOP_MARKET",
            "symbol": symbol or self.symbol,
            "side": side,
            "positionSide": "BOTH",
            "algoStatus": "NEW",
            "triggerPrice": trigger,
            "workingType": "MARK_PRICE",
            "closePosition": True,
            "priceProtect": False,
            "reduceOnly": False,
        }


async def no_sleep(_: float) -> None:
    pass


def open_long(exchange: FakeExchange, amount: str = "0.083", entry: str = "60000") -> None:
    exchange.position_amount = Decimal(amount)
    exchange.entry_price = Decimal(entry)
    exchange.liquidation_price = Decimal(48000)


class FixedGate:
    """An entry gate that caps every entry's risk at `cap` (no cap by default)."""

    def __init__(self, cap: Decimal | None = None) -> None:
        self.cap = cap

    def entry_slot(self) -> AbstractAsyncContextManager[None]:
        return nullcontext()

    async def entry_risk(self, per_trade: Decimal) -> Decimal:
        return per_trade if self.cap is None else min(per_trade, self.cap)


def make_trader(exchange: FakeExchange, gate: EntryGate | None = None) -> SymbolTrader:
    client = exchange.client()
    return SymbolTrader(
        client,
        BTC,
        TRADING,
        RULES,
        BaseBalance(client, "USDT"),
        gate or FixedGate(),
        ClientIdFactory(),
        sleep=no_sleep,
    )
