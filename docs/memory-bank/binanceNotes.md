# Binance Notes

Binance USDⓈ-M Futures behavior the code can't tell you: quirks, limits, and gotchas.

Move an item to **Verified** only after confirming it against the official docs or an actual testnet/mainnet response. Include the date and the source (doc URL or "testnet response"). Binance changes its API, so re-check old entries when something behaves unexpectedly.

Reading the docs: developers.binance.com is a JS-rendered site, and plain page fetches often return the wrong section. Open pages in a browser, or search the index at `https://developers.binance.com/en/docs/llms.txt`.

## Verified

### Base URLs (2026-10-02)

Source: [General Info](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info), [WebSocket Market Streams — Connect](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Connect)

| | REST | WebSocket root |
|---|---|---|
| Mainnet | `https://fapi.binance.com` | `wss://fstream.binance.com` |
| Testnet | `https://demo-fapi.binance.com` | `wss://demo-fstream.binance.com` |

- The docs no longer list the old testnet host `testnet.binancefuture.com`; older tutorials and libraries may still use it.

### Futures testnet is Binance Demo Trading (2026-10-02)

Source: [Derivatives Quick Start](https://developers.binance.com/docs/derivatives/quick-start), testnet response

- The Futures Testnet supports `FUTURES` trading, API only. Demo Trading API keys are set up from the [Futures Demo Trading page](https://demo.binance.com/en/futures/BTCUSDT).
- Demo Trading keys (HMAC) authenticate on `demo-fapi.binance.com`. These are the keys to put in `.env` while `BINANCE_TESTNET` is unset or `true`.
- A new demo account starts in one-way position mode with 5000 USDT, 5000 USDC, and 0.01 BTC.

### WebSocket routing (2026-10-02)

Source: [Important WebSocket Change Notice](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Important-WebSocket-Change-Notice), [Connect](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Connect), testnet response

Mainnet streams must go through a routed path. Legacy unrouted URLs were supported only until 2026-04-23; an unrouted connection now receives `/public` data only. Testnet accepts the same routes (klines received on `/market`).

| Route | Streams |
|---|---|
| `/public` | Book tickers, partial and diff book depth |
| `/market` | Aggregate trades, mark price, kline, mini ticker, ticker, liquidations, composite index, contract info |
| `/private` | User data (listenKey events such as `ORDER_TRADE_UPDATE`, `ACCOUNT_UPDATE`) |

- `ws` mode: `<root>/<route>/ws/<streamName>` — e.g. `wss://fstream.binance.com/market/ws/btcusdt@kline_1m`
- `stream` mode: `<root>/<route>/stream?streams=<a>/<b>`

### WebSocket connection rules (2026-10-02)

Source: [Connect](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Connect)

- A connection is valid for 24 hours, then the server disconnects it. Reconnect logic is mandatory.
- The server sends a ping frame every 3 minutes; no pong within 10 minutes → disconnected.
- Max 10 incoming messages per second per connection.
- Max 1024 streams per connection.

### Signed requests (2026-10-02)

Source: [General Info](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info), testnet response

- HMAC SHA256 with the secret key over `totalParams` (query string + request body), sent as the `signature` parameter. API key goes in the `X-MBX-APIKEY` header. GET parameters must be in the query string.
- `recvWindow` defaults to 5000 ms (max 60000). The server accepts a request only if `timestamp < serverTime + 1000` and `serverTime - timestamp <= recvWindow`.
- The development PC's clock was 462 ms ahead of the testnet server on the first check — close to the 1000 ms future limit. The engine measures the offset with `GET /fapi/v1/time` and corrects signed timestamps.

### REST endpoints in use (2026-10-02)

Source: [Trade](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade), [Account](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account), [Market Data](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data), testnet response

| Endpoint | Weight | Notes |
|---|---|---|
| `GET /fapi/v1/time` | 1 | |
| `GET /fapi/v1/exchangeInfo` | 1 | Symbols, filters, and the account's rate limits |
| `GET /fapi/v1/accountConfig` | 5 | `dualSidePosition`: `true` = hedge, `false` = one-way. Use this, not `GET /fapi/v1/positionSide/dual` (weight 30) |
| `GET /fapi/v3/balance` | 5 | One entry per asset, including zero balances |
| `GET /fapi/v3/positionRisk` | 5 | Only symbols with a position or open orders |

### Symbol filters (2026-10-02)

Source: testnet response

- Field names: `PRICE_FILTER` {`tickSize`, `minPrice`, `maxPrice`}, `LOT_SIZE` {`stepSize`, `minQty`, `maxQty`}, `MIN_NOTIONAL` {`notional`}. Docs also list `MARKET_LOT_SIZE`, `MAX_NUM_ORDERS`, `PERCENT_PRICE`.
- Values differ per symbol and per network (testnet BTCUSDT: tick 0.10, step 0.0001, min notional 50), so always read them from `exchangeInfo` at runtime.

### Rate-limit signals (2026-10-02)

Source: [General Info](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info), testnet response

- Headers: `X-MBX-USED-WEIGHT-<n><unit>` (per IP) on every response; `X-MBX-ORDER-COUNT-<n><unit>` (per account) on order responses.
- 429 = rate limit hit, 418 = IP auto-banned after repeated 429s, 403 = WAF block, 5xx = server error (outcome of an order unknown).

## To verify

Check each item while implementing the related feature:

- [ ] Position mode: how `reduceOnly` behaves in one-way vs hedge mode.
- [ ] Symbol filters: how orders that violate them are rejected (error codes).
- [ ] Signed requests: the error code returned when the timestamp is outside `recvWindow`.
- [ ] User Data Stream: `listenKey` creation, expiry, and keepalive interval; exact `/private` URL format; whether testnet supports it the same way.
- [ ] Exchange-side stop orders: the docs list TP/SL and trailing stops as algo (conditional) orders with their own endpoints (e.g. `GET /fapi/v1/allAlgoOrders`). Check how to place one, `closePosition` vs `reduceOnly`, and the trigger price source (mark vs last price).
- [ ] State restore on restart: which endpoint returns open orders, including algo orders.
