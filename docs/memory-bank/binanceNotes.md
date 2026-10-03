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

- General Info lists only `demo-fapi` for testnet, but the API Reference pages still show `https://testnet.binancefuture.com` as an alternate endpoint (seen 2026-10-03). The engine uses `demo-fapi`, which works; `testnet.binancefuture.com` is untested.

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
- Observed on testnet (2026-10-03): when the client closes a stream, the close handshake completes (code 1000) but `websockets` still waits 5–7 s before the connection is gone, up to its `close_timeout` (default 10 s). With `close_timeout=2` the close took 2 s.

### Signed requests (2026-10-02)

Source: [General Info](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info), testnet response

- Security types: USER_DATA (and TRADE) requests are signed as below; USER_STREAM requests (`listenKey`) send only the `X-MBX-APIKEY` header, with no timestamp or signature. Without the header Binance answers HTTP 401, code `-2014` (testnet response).
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
| `POST /fapi/v1/listenKey` | 1 | USER_STREAM. Returns the active key if there is one |
| `PUT /fapi/v1/listenKey` | 1 | USER_STREAM. Response `{"listenKey": ...}`; `-1125` if no key is active |

### Symbol filters (2026-10-02)

Source: testnet response

- Field names: `PRICE_FILTER` {`tickSize`, `minPrice`, `maxPrice`}, `LOT_SIZE` {`stepSize`, `minQty`, `maxQty`}, `MIN_NOTIONAL` {`notional`}. Docs also list `MARKET_LOT_SIZE`, `MAX_NUM_ORDERS`, `PERCENT_PRICE`.
- Values differ per symbol and per network (testnet BTCUSDT: tick 0.10, step 0.0001, min notional 50), so always read them from `exchangeInfo` at runtime.

### Rate-limit signals (2026-10-02)

Source: [General Info](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info), testnet response

- Headers: `X-MBX-USED-WEIGHT-<n><unit>` (per IP) on every response; `X-MBX-ORDER-COUNT-<n><unit>` (per account) on order responses.
- 429 = rate limit hit, 418 = IP auto-banned after repeated 429s, 403 = WAF block, 5xx = server error (outcome of an order unknown).

### User data stream (2026-10-03)

Source: [User Data Streams](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/user-data-streams), [REST: User Data Streams](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/user-data-streams), testnet response

- URL: `<ws root>/private/ws/<listenKey>`, e.g. `wss://demo-fstream.binance.com/private/ws/<listenKey>` on testnet. A connection lasts at most 24 hours.
- A `listenKey` (64 characters on testnet) is valid for 60 minutes after its last `POST` or `PUT`. The REST docs recommend a keepalive "about every 60 minutes", which leaves no margin; the engine sends one every 30 minutes.
- One `listenKey` per account: a second `POST` while one is active returns the same key and extends it. Every process on the account therefore shares the key.
- `DELETE /fapi/v1/listenKey` (weight 1) invalidates the key: an open connection on it receives `listenKeyExpired` (payload `e`, `E`, `listenKey`), a later `PUT` fails with HTTP 400 `-1125` "This listenKey does not exist.", and the next `POST` returns a new key.
- After `listenKeyExpired` no more events arrive on that connection until it reconnects with a valid key. The event is unrelated to the 24-hour disconnect.
- Connecting with a nonexistent key completes the handshake and then stays silent: no error, no events. An unrouted `<ws root>/ws/<listenKey>` also completes the handshake; whether it delivers events is untested.
- Event types: `ORDER_TRADE_UPDATE`, `ACCOUNT_UPDATE`, `ALGO_UPDATE` (algo/conditional orders), `ACCOUNT_CONFIG_UPDATE`, `MARGIN_CALL`, `TRADE_LITE`, `CONDITIONAL_ORDER_TRIGGER_REJECT`, `STRATEGY_UPDATE`, `GRID_UPDATE` (deprecated), `listenKeyExpired`.
- Ordering: on one connection, events of the same type are strictly ordered by `T` and `E`; across types, order by `E`.
- `ACCOUNT_UPDATE` is pushed only when balances, positions, or margin type change (not for unfilled or cancelled orders), with only the changed symbols in `P`. `a.m` gives the reason (`ORDER`, `FUNDING_FEE`, ...).
- Not yet seen on testnet: real `ORDER_TRADE_UPDATE` / `ACCOUNT_UPDATE` / `ALGO_UPDATE` payloads (they need orders). Older docs said `ORDER_TRADE_UPDATE` omits `N` and `n` when there is no commission; the parser tolerates that.

### Algo (conditional) orders (2026-10-03)

Source: [Trade — New Algo Order](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade). Docs only; nothing placed on testnet yet.

- `POST /fapi/v1/algoOrder` (TRADE, signed) with `algoType=CONDITIONAL`. Types: `STOP_MARKET`, `TAKE_PROFIT_MARKET`, `STOP`, `TAKE_PROFIT`, `TRAILING_STOP_MARKET`. Costs 1 on the 10 s and 1 min order-count limits and 0 IP weight. Related: Query Algo Order, Cancel Algo Order, Cancel All Algo Open Orders.
- `triggerPrice` sets the trigger. `STOP_MARKET` SELL triggers when the price falls to it, BUY when the price rises to it.
- `workingType`: `MARK_PRICE` or `CONTRACT_PRICE` (last price); default `CONTRACT_PRICE`.
- `closePosition=true` (`STOP_MARKET`/`TAKE_PROFIT_MARKET` only) closes the whole current position on trigger: a long if SELL, a short if BUY. It cannot be combined with `quantity` or `reduceOnly`.
- `priceProtect=true` (default false): at trigger time, the mark/last price difference must not exceed the symbol's `triggerProtect` from `exchangeInfo`.
- `clientAlgoId`: unique among open orders, `^[\.A-Z\:/a-z0-9_-]{1,36}$`, generated if not sent.
- The response carries `algoId`, `clientAlgoId`, and `algoStatus` (`NEW` on placement); status changes then arrive as `ALGO_UPDATE` on the user data stream.

### Order IDs and lookups (2026-10-03)

Source: [Trade](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade). Docs only.

- `newClientOrderId` follows the same pattern as `clientAlgoId` (above). Both are unique among **open** orders only, so a filled or cancelled order's ID can be sent again.
- `GET /fapi/v1/order` (weight 1) takes `symbol` plus `orderId` or `origClientOrderId`; `GET /fapi/v1/algoOrder` (weight 1) takes `algoId` or `clientAlgoId`. Neither finds an order cancelled or expired without fills more than 3 days ago, or any order older than 90 days.
- Other Trade endpoints relevant later: Current All Open Orders, Current All Algo Open Orders, Query All Algo Orders, Change Initial Leverage, Change Margin Type, Modify Isolated Position Margin.

## To verify

Check each item while implementing the related feature:

- [ ] Position mode: how `reduceOnly` behaves in one-way vs hedge mode.
- [ ] Symbol filters: how orders that violate them are rejected (error codes).
- [ ] Signed requests: the error code returned when the timestamp is outside `recvWindow`.
- [ ] Exchange-side stops on testnet (D-18): place a `STOP_MARKET` with `closePosition=true` and `MARK_PRICE`; the error when the trigger price is already crossed (docs mention `-2021` "Order would immediately trigger" for trailing stops); whether a `closePosition` stop can be placed with no open position; and whether it is cancelled or expired automatically when the position closes.
- [ ] Real `ORDER_TRADE_UPDATE`, `ACCOUNT_UPDATE`, and `ALGO_UPDATE` payloads on testnet, compared with the documented fields.
- [ ] State restore on restart: response shapes of Current All Open Orders and Current All Algo Open Orders on testnet.
- [ ] Margin type and leverage (D-16): the endpoints that set them, how to read the current values per symbol, and the error returned when changing margin type while a position or open orders exist.
