"""An in-memory stand-in for the Binance endpoints the executor uses.

Market orders fill at the mark price. Failures can be queued per endpoint: an exception or
response is returned instead of handling the request, or, with `after=True`, the request is
handled first and its response then lost (an executed order with an unknown outcome).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl

import httpx

from quantdeck_engine.binance.rest import BinanceRestClient
from quantdeck_engine.config import parse_settings

SETTINGS = parse_settings({"BINANCE_API_KEY": "test-key", "BINANCE_API_SECRET": "test-secret"})

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
    incomes: list[dict[str, Any]] = field(default_factory=list)
    open_orders: list[dict[str, Any]] = field(default_factory=list)
    algo_orders: list[dict[str, Any]] = field(default_factory=list)
    orders: dict[str, dict[str, Any]] = field(default_factory=dict)  # every order, by client ID
    failures: dict[tuple[str, str], list[Failure]] = field(default_factory=dict)
    requests: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)

    def client(self) -> BinanceRestClient:
        return BinanceRestClient(SETTINGS, transport=httpx.MockTransport(self.handle))

    def fail(self, method: str, path: str, *failures: Failure) -> None:
        self.failures.setdefault((method, path), []).extend(failures)

    def calls(self, method: str, path: str) -> list[dict[str, str]]:
        return [p for m, pa, p in self.requests if (m, pa) == (method, path)]

    def add_stop(self, side: str, trigger: str, client_algo_id: str = "qd_s_BTCUSDT_1_00") -> None:
        self.algo_orders.append(self._algo(side, trigger, client_algo_id))

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
            case ("GET", "/fapi/v3/positionRisk"):
                if self.position_amount == 0:
                    return httpx.Response(200, json=[])
                return httpx.Response(200, json=[self._position()])
            case ("GET", "/fapi/v1/premiumIndex"):
                price = {"symbol": self.symbol, "markPrice": str(self.mark)}
                return httpx.Response(200, json=price)
            case ("GET", "/fapi/v1/symbolConfig"):
                config = {
                    "symbol": self.symbol,
                    "marginType": self.margin_type,
                    "isAutoAddMargin": False,
                    "leverage": self.leverage,
                    "maxNotionalValue": "1000000",
                }
                return httpx.Response(200, json=[config])
            case ("GET", "/fapi/v1/leverageBracket"):
                return httpx.Response(200, json={"symbol": self.symbol, "brackets": BRACKETS})
            case ("GET", "/fapi/v3/balance"):
                balance = {
                    "asset": "USDT",
                    "balance": str(self.wallet),
                    "availableBalance": str(self.available),
                }
                return httpx.Response(200, json=[balance])
            case ("GET", "/fapi/v1/income"):
                page, limit = int(p["page"]), int(p["limit"])
                return httpx.Response(200, json=self.incomes[(page - 1) * limit : page * limit])
            case ("GET", "/fapi/v1/openOrders"):
                return httpx.Response(200, json=self.open_orders)
            case ("GET", "/fapi/v1/openAlgoOrders"):
                return httpx.Response(200, json=self.algo_orders)
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
                self.leverage = int(p["leverage"])
                return httpx.Response(200, json={"leverage": self.leverage})
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

    def _position(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "positionSide": "BOTH",
            "positionAmt": str(self.position_amount),
            "entryPrice": str(self.entry_price),
            "markPrice": str(self.mark),
            "liquidationPrice": str(self.liquidation_price),
        }

    def _algo(self, side: str, trigger: str, client_algo_id: str) -> dict[str, Any]:
        return {
            "algoId": len(self.algo_orders) + 100,
            "clientAlgoId": client_algo_id,
            "algoType": "CONDITIONAL",
            "orderType": "STOP_MARKET",
            "symbol": self.symbol,
            "side": side,
            "positionSide": "BOTH",
            "algoStatus": "NEW",
            "triggerPrice": trigger,
            "workingType": "MARK_PRICE",
            "closePosition": True,
            "priceProtect": False,
            "reduceOnly": False,
        }
