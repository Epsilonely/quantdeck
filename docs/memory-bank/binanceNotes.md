# Binance Notes

Binance USDⓈ-M Futures behavior the code can't tell you: quirks, limits, and gotchas.

Move an item to **Verified** only after confirming it against the official docs or an actual testnet/mainnet response. Include the date and the source (doc URL or "testnet response"). Binance changes its API, so re-check old entries when something behaves unexpectedly.

Reading the docs: developers.binance.com is a JS-rendered site, and plain page fetches often return the wrong section. Open pages in a browser, or search the index at `https://developers.binance.com/en/docs/llms.txt`, which lists a plain Markdown version of each page (the page URL plus `.md`). Pages under the old `/docs/derivatives/usds-margined-futures/...` paths moved to `/en/docs/products/derivatives-trading-usds-futures/...` (2026-10-18); old links no longer reach the page. The REST reference pages (`/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/...`) aren't in that index and serve only their app shell to a plain fetch, but their "Download schema" link, `.../api/rest-api/1.0.0/schema.yaml`, is the whole USDⓈ-M REST API as OpenAPI YAML: parameters, weights, notes, and response fields. It came back empty to `curl` and whole to a `fetch` from a page on developers.binance.com (2026-10-22).

## Verified

### Base URLs (2026-10-02)

Source: [General Info](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info), [WebSocket Market Streams — Connect](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Connect)

| | REST | WebSocket root |
|---|---|---|
| Mainnet | `https://fapi.binance.com` | `wss://fstream.binance.com` |
| Testnet | `https://demo-fapi.binance.com` | `wss://demo-fstream.binance.com` |

- General Info lists only `demo-fapi` for testnet, but the API Reference pages still show `https://testnet.binancefuture.com` as an alternate endpoint (seen 2026-10-03). The engine uses `demo-fapi`, which works; `testnet.binancefuture.com` is untested.

### Futures testnet is Binance Demo Trading (2026-10-02)

Source: [Derivatives Quick Start](https://developers.binance.com/en/docs/products/common/derivatives-trading/quick-start), testnet response

- The Futures Testnet is for practicing `FUTURES` trading. Demo Trading API keys are set up from the [Futures Demo Trading page](https://demo.binance.com/en/futures/BTCUSDT).
- Demo Trading keys (HMAC) authenticate on `demo-fapi.binance.com`. These are the keys to put in `.env` while `BINANCE_TESTNET` is unset or `true`.
- A new demo account starts in one-way position mode with 5000 USDT, 5000 USDC, and 0.01 BTC.

### WebSocket routing (2026-10-02)

Source: [Important WebSocket Change Notice](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Important-WebSocket-Change-Notice), [Connect](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Connect), testnet response

Mainnet streams must go through a routed path. Legacy unrouted URLs were supported only until 2026-04-23; an unrouted connection now receives `/public` data only. Testnet accepts the same routes (klines received on `/market`).

| Route | Streams |
|---|---|
| `/public` | Book tickers, partial and diff book depth |
| `/market` | Aggregate trades, mark price, kline, mini ticker, ticker, liquidations, composite index, contract info |
| `/private` | User data (listenKey events such as `ORDER_TRADE_UPDATE`, `ACCOUNT_UPDATE`) |

- `ws` mode: `<root>/<route>/ws/<streamName>` — e.g. `wss://fstream.binance.com/market/ws/btcusdt@kline_1m`
- `stream` mode: `<root>/<route>/stream?streams=<a>/<b>`

### WebSocket connection rules (2026-10-02)

Source: [Connect](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Connect)

- A connection is valid for 24 hours, then the server disconnects it. Reconnect logic is mandatory.
- The server sends a ping frame every 3 minutes; no pong within 10 minutes → disconnected.
- Max 10 incoming messages per second per connection.
- Max 1024 streams per connection.
- Observed on testnet: when the client closes a stream, the close handshake completes (code 1000, 2026-10-03), but the server keeps the TCP connection open for several seconds, and `websockets` waits for it to drop, up to its `close_timeout` (default 10 s). Timed on 2026-10-20 on the `/market` kline and mark price streams: with the default the connection dropped 5.2–9.5 s after a close (6 closes), never within 2 s, so with `close_timeout=2` every close ended at the timeout, in 2.0 s (8 closes). The `/private` user data stream wasn't timed.

### Mark price stream (2026-10-16)

Source: testnet response

- `<symbol>@markPrice@1s` works on the `/market` route as a combined stream: `wss://demo-fstream.binance.com/market/stream?streams=btcusdt@markPrice@1s` delivered one update a second.
- Combined-stream messages wrap the payload as `{"stream": ..., "data": {...}}`. The engine reads `data.s` (symbol), `data.p` (mark price, a string), and `data.E` (event time, ms); the other fields are unchecked.

### Signed requests (2026-10-02)

Source: [General Info](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info), testnet response

- Security types: USER_DATA (and TRADE) requests are signed as below; USER_STREAM requests (`listenKey`) send only the `X-MBX-APIKEY` header, with no timestamp or signature. Without the header Binance answers HTTP 401, code `-2014` (testnet response).
- HMAC SHA256 with the secret key over `totalParams` (query string + request body), sent as the `signature` parameter. API key goes in the `X-MBX-APIKEY` header. GET parameters must be in the query string.
- `recvWindow` defaults to 5000 ms (max 60000). The server accepts a request only if `timestamp < serverTime + 1000` and `serverTime - timestamp <= recvWindow`.
- The development PC's clock was 462 ms ahead of the testnet server on the first check — close to the 1000 ms future limit. The engine measures the offset with `GET /fapi/v1/time` and corrects signed timestamps.

### REST endpoints in use (2026-10-02, extended 2026-10-10 and 2026-10-22)

Source: [Trade](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade), [Account](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account), [Market Data](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data), testnet response

| Endpoint | Weight | Notes |
|---|---|---|
| `GET /fapi/v1/time` | 1 | |
| `GET /fapi/v1/exchangeInfo` | 1 | Symbols, filters, and the account's rate limits |
| `GET /fapi/v1/accountConfig` | 5 | `dualSidePosition`: `true` = hedge, `false` = one-way. Use this, not `GET /fapi/v1/positionSide/dual` (weight 30) |
| `GET /fapi/v3/balance` | 5 | One entry per asset, including zero balances. `balance` is the wallet balance |
| `GET /fapi/v3/positionRisk` | 5 | Only symbols with a position or open orders. Has `liquidationPrice` and `isolatedWallet`, but no `leverage` or `marginType` (v2 had them) |
| `GET /fapi/v1/symbolConfig` | 5 | Current `marginType` (`ISOLATED`/`CROSSED`) and `leverage` per symbol |
| `GET /fapi/v1/premiumIndex` | 1 with `symbol` | `markPrice`. An object with `symbol`, an array without (weight 10) |
| `GET /fapi/v1/leverageBracket` | 1 | Bracket values (`notionalFloor`, `notionalCap`, `maintMarginRatio`, `cum`, `initialLeverage`) are JSON numbers, not strings. Docs: an object when `symbol` is sent, an array otherwise |
| `GET /fapi/v1/income` | 30 | See Income history |
| `GET /fapi/v1/openOrders`, `GET /fapi/v1/openAlgoOrders` | 1 with `symbol`, 40 without | |
| `POST /fapi/v1/marginType` | 1 | `-4046` no change needed; `-4047` open orders exist; `-4048` a position exists (docs) |
| `POST /fapi/v1/leverage` | 1 | Response `{"leverage", "maxNotionalValue", "symbol"}` |
| `POST /fapi/v1/listenKey` | 1 | USER_STREAM. Returns the active key if there is one |
| `PUT /fapi/v1/listenKey` | 1 | USER_STREAM. Response `{"listenKey": ...}`; `-1125` if no key is active |
| `DELETE /fapi/v1/allOpenOrders` | 1 | `symbol` required. Docs response `{"code": 200, "msg": "The operation of cancel all open order is done."}`. Succeeded on testnet with no open orders (2026-10-16) |
| `DELETE /fapi/v1/algoOpenOrders` | 1 | `symbol` required. Cancels every open algo order on the symbol, stops included. Same docs response as above. Succeeded on testnet with no open orders (2026-10-16) |
| `GET /fapi/v1/userTrades` | 5 | Docs only (OpenAPI schema, 2026-10-22). `symbol` required. `startTime` and `endTime` at most 7 days apart; neither sent → the last 7 days; `fromId` can't be sent with them. `limit` default 500, max 1000. Only the last 3 months (6 until the Change Log of 2026-08-26). Rows: `id`, `orderId`, `side`, `price`, `qty`, `quoteQty`, `realizedPnl`, `commission`, `commissionAsset`, `time`, `buyer`, `maker`, `positionSide`, and no client order ID |
| `GET /fapi/v1/allOrders` | 5 | Docs only (2026-10-22). `symbol` optional since 2026-08-25 (Change Log); the engine sends it. With `orderId`, orders from that ID on; otherwise the most recent. Period under 7 days, the last 7 by default. Gone: cancelled or expired orders without fills after 3 days, any order after 90 days. Rows carry `clientOrderId`, `orderId`, `type`, `origType`, `status`, `executedQty`, `avgPrice`, `reduceOnly`, `closePosition`, `time`, `updateTime` |
| `GET /fapi/v1/allAlgoOrders` | 5 | Docs only (2026-10-22). `symbol` required; `algoId` (from that ID on), `startTime`/`endTime` (under 7 days apart, the last 7 by default), `limit` max 1000. Kept like `allOrders`. Rows carry `clientAlgoId`, `algoStatus`, `actualOrderId` (a string; the order a triggered one placed), `triggerPrice`, `createTime`, `updateTime`, `triggerTime` |

- Boolean request parameters (`reduceOnly`, `closePosition`, `priceProtect`) are the strings `true`/`false`; boolean response fields are JSON booleans. Prices and quantities in responses are strings; IDs and times are numbers.
- Changing margin type and leverage with no position worked on testnet (2026-10-10). The margin type change also pushes `ACCOUNT_UPDATE` with `m: "MARGIN_TYPE_CHANGE"`; the leverage change pushes `ACCOUNT_CONFIG_UPDATE` `{"ac": {"s": "BTCUSDT", "l": 5}}`.

### Symbol filters (2026-10-02, extended 2026-10-10)

Source: testnet response, [Common Definition](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/common-definition)

- Field names: `PRICE_FILTER` {`tickSize`, `minPrice`, `maxPrice`}, `LOT_SIZE` {`stepSize`, `minQty`, `maxQty`}, `MIN_NOTIONAL` {`notional`}. Docs also list `MARKET_LOT_SIZE`, `MAX_NUM_ORDERS`, `PERCENT_PRICE`.
- Values differ per symbol and per network (testnet BTCUSDT: tick 0.10, step 0.0001, min notional 50), so always read them from `exchangeInfo` at runtime.
- Mainnet BTCUSDT (2026-10-21, mainnet response): `MIN_NOTIONAL` 50, `MARKET_LOT_SIZE` `minQty` and `stepSize` 0.001, ten times testnet's step; about 83 USDT a step at the day's mark price. ETHUSDT: min notional 20, step 0.001. So a small mainnet account sizes BTCUSDT far more coarsely than the backtest, which uses the configured network's rules (testnet by default, D-40): at 1% risk one step needs a base of about 0.1 × stop distance × price (250–830 USDT for stops 3–10% away).
- `MARKET_LOT_SIZE` limits MARKET orders; the docs don't say whether `LOT_SIZE` does too, so the engine applies the stricter of the two. For MARKET orders `MIN_NOTIONAL` is checked at the mark price.
- Each symbol in `exchangeInfo` also has `triggerProtect` (the `priceProtect` threshold) and `marketTakeBound`.

### Rate-limit signals (2026-10-02)

Source: [General Info](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info), testnet response

- Headers: `X-MBX-USED-WEIGHT-<n><unit>` (per IP) on every response; `X-MBX-ORDER-COUNT-<n><unit>` (per account) on order responses.
- 429 = rate limit hit, 418 = IP auto-banned after repeated 429s, 403 = WAF block, 5xx = server error (outcome of an order unknown).
- Mainnet response (2026-10-19): a backtest of SOLUSDT 15m from 2020-01-01, before the symbol's first archived month, found no archive files for those months and asked REST for every missing day, and mainnet answered 429 (2400 weight per minute per IP). Start a backtest at the symbol's first archived month (Historical data archive below), and don't repeat a run that got a 429 right away.

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
- `ACCOUNT_UPDATE` is pushed only when balances, positions, or margin type change (not for unfilled or cancelled orders), with only the changed symbols in `P`. `a.m` gives the reason (`ORDER`, `FUNDING_FEE`, `MARGIN_TYPE_CHANGE`, ...).
- Seen on testnet (2026-10-10): a market order produces `TRADE_LITE`, then `ORDER_TRADE_UPDATE` (`x`/`X` `NEW`), `ACCOUNT_UPDATE` (`m: "ORDER"`), and `ORDER_TRADE_UPDATE` (`TRADE`/`FILLED`). `n` and `N` were present even at zero commission (older docs said they could be left out; the parser tolerates both).
- `ACCOUNT_UPDATE` `P` entries also carry `cr`, `iw` (isolated wallet), `ma` (margin asset), and `bep`. `mt` is lowercase (`isolated`), unlike REST's `ISOLATED`. Right after an entry, `iw` was exactly the initial margin (notional ÷ leverage) minus the opening commission.
- `CONDITIONAL_ORDER_TRIGGER_REJECT` is deprecated since 2025-12-15 (Change Log); rejection reasons come in `ALGO_UPDATE` `rm`.
- Orders the exchange places itself (docs, 2026-10-22): a liquidation's `ORDER_TRADE_UPDATE` has `c` `autoclose-XXX`, an ADL's `adl_autoclose`.

### Algo (conditional) orders (2026-10-03, testnet 2026-10-10)

Source: [Trade — New Algo Order](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade), Change Log, testnet response

- `POST /fapi/v1/algoOrder` (TRADE, signed) with `algoType=CONDITIONAL`. Types: `STOP_MARKET`, `TAKE_PROFIT_MARKET`, `STOP`, `TAKE_PROFIT`, `TRAILING_STOP_MARKET`. Costs 1 on the 10 s and 1 min order-count limits and 0 IP weight. Since 2025-12-09 `POST /fapi/v1/order` rejects these types with `-4120`, so stops go through algo orders only.
- `triggerPrice` sets the trigger. `STOP_MARKET` SELL triggers when the price falls to it, BUY when the price rises to it.
- `workingType`: `MARK_PRICE` or `CONTRACT_PRICE` (last price); default `CONTRACT_PRICE`.
- `closePosition=true` (`STOP_MARKET`/`TAKE_PROFIT_MARKET` only) closes the whole current position on trigger: a long if SELL, a short if BUY. It cannot be combined with `quantity` or `reduceOnly`.
- `priceProtect=true` (default false): at trigger time, the mark/last price difference must not exceed the symbol's `triggerProtect` from `exchangeInfo`.
- `clientAlgoId`: unique among open orders, `^[\.A-Z\:/a-z0-9_-]{1,36}$`, generated if not sent.
- Query (`GET`) and Cancel (`DELETE /fapi/v1/algoOrder`) take `algoId` or `clientAlgoId` and no `symbol`. Cancel answers `{"code": "200", "msg": "success"}` with `code` as a string. The docs give no error code for "algo order not found", so the engine checks `openAlgoOrders` instead of querying one.
- `ALGO_UPDATE` fields: `caid` clientAlgoId, `aid` algoId, `o` order type, `X` status, `tp` trigger price, `cp` closePosition, `R` reduceOnly, `wt` working type, `ai` the matching-engine `orderId` once triggered (a string, `""` before), `rm` failure reason. `ap`, `aq`, `act`, `rm` are left out until they apply.
- Statuses: `NEW` (waiting), `CANCELED`, `TRIGGERING` (sent to the matching engine), `TRIGGERED` (placed there), `FINISHED` (that order filled or was cancelled), `REJECTED`, `EXPIRED` (cancelled by the system).
- Testnet (2026-10-10): a `STOP_MARKET` with `closePosition=true` and `MARK_PRICE` was accepted and reported `f: "GTE_GTC"`, `q: "0"`, `R: true` (reduceOnly, though not sent). `ALGO_UPDATE` went `NEW` on placement, `CANCELED` on cancel, and `EXPIRED` about 60 ms after the position was closed by a reduceOnly market order: Binance removes a `closePosition` stop itself once its position is gone.
- Testnet (2026-10-21): seen again when the kill switch closed a strategy's ETHUSDT position. `GET /fapi/v1/algoOrder?clientAlgoId=...` (signed) afterwards returned `algoStatus` `EXPIRED`, with `updateTime` at the close's fill, before the kill switch's bulk cancels ran, and `triggerTime` 0 and `actualOrderId` `""` for a stop that never triggered. The response also carries `algoId`, `algoType`, `orderType`, `side`, `triggerPrice`, `closePosition`, `workingType`, `priceProtect`, `createTime`, and `quantity`, among others.
- Testnet (2026-10-26): the first engine stops to trigger, five on ETHUSDT. Afterwards `allAlgoOrders` showed each as `FINISHED`, with `triggerTime` and `actualOrderId` set. The order a stop placed (`allOrders`) is a `MARKET` order whose `clientOrderId` is the stop's `clientAlgoId`, with `reduceOnly` and `closePosition` true and `workingType` `MARK_PRICE`. Its fill was within ±0.09% of the trigger, on either side, since the mark price triggers and the last price fills.
- Docs: no margin check before a conditional order triggers (Change Log); `-2021` "Order would immediately trigger." and `-4142` "REJECT: take profit or stop order will be triggered immediately" are the crossed-trigger errors.

### Orders, lookups, and unknown outcomes (2026-10-03, testnet 2026-10-10)

Source: [Trade](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade), [General Info](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info), [Error Codes](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/error-code), testnet response

- `newClientOrderId` follows the same pattern as `clientAlgoId` (above). Both are unique among **open** orders only, so a filled or cancelled order's ID can be sent again.
- A MARKET order with `newOrderRespType=RESULT` answers with its final fill: on testnet the response had `status` `FILLED` and `executedQty`. The docs' only example has no `avgPrice` or `cumQuote`; the engine doesn't rely on them.
- `positionSide` defaults to `BOTH` in one-way mode; `reduceOnly` can't be sent in hedge mode. A reduceOnly market close worked in one-way mode on testnet.
- `GET /fapi/v1/order` (weight 1) takes `symbol` plus `orderId` or `origClientOrderId`; a missing order is `-2013` "Order does not exist.". It and `GET /fapi/v1/algoOrder` don't find an order cancelled or expired without fills more than 3 days ago, or any order older than 90 days.
- Unknown outcome (look the order up before resending): HTTP 503 "Unknown error, please check your request or try again later.", other 5xx, HTTP 408, `-1006`, `-1007`. Definite failure: HTTP 503 "Service Unavailable.", `-1001`, `-1008` (reduceOnly and closePosition orders are exempt from `-1008` throttling).
- Order rejections (docs): `-2019` margin insufficient, `-2022` ReduceOnly rejected, `-2027` exceeds the maximum position at current leverage, `-4164` below min notional, `-1111` precision, `-4004`/`-4005` quantity below min/above max, `-4023` quantity not a step multiple, `-4014` price not a tick multiple, `-4116` duplicated client order ID.
- `-2019` on testnet (2026-10-26): two market entries sized to exactly the available balance × leverage (2×, no other position open) were refused. The margin check prices a market order above the last price (Cost to open a position, below); at that price the initial margin alone exceeded the available balance in both.

### Income history (2026-10-10)

Source: [Account — Income History](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account), testnet response

- `GET /fapi/v1/income` (USER_DATA, weight 30): `startTime`/`endTime` in ms, both inclusive; `page`; `limit` default 100, max 1000. Without times it returns the last 7 days; only the last three months are kept. Results are oldest first.
- Fields: `symbol`, `incomeType`, `income` (string), `asset`, `info`, `time`, `tranId`, `tradeId`.
- `incomeType` and `symbol` filter the records (docs, 2026-10-22); without `incomeType` every type comes back.
- The 23 `incomeType` values listed in the docs (2026-10-16): `TRANSFER`, `WELCOME_BONUS`, `REALIZED_PNL`, `FUNDING_FEE`, `COMMISSION`, `INSURANCE_CLEAR`, `REFERRAL_KICKBACK`, `COMMISSION_REBATE`, `API_REBATE`, `CONTEST_REWARD`, `CROSS_COLLATERAL_TRANSFER`, `OPTIONS_PREMIUM_FEE`, `OPTIONS_SETTLE_PROFIT`, `INTERNAL_TRANSFER`, `AUTO_EXCHANGE`, `DELIVERED_SETTELMENT` (sic), `COIN_SWAP_DEPOSIT`, `COIN_SWAP_WITHDRAW`, `POSITION_LIMIT_INCREASE_FEE`, `STRATEGY_UMFUTURES_TRANSFER`, `FEE_RETURN`, `BFUSD_REWARD`, `SPECIAL_FUNDING_FEE`. Which ones count toward today's loss is in D-24.
- Testnet: one entry-and-close produced two `COMMISSION` records and one `REALIZED_PNL`, and the day's records summed exactly to the wallet balance change — the basis for rebuilding the day's base balance (D-23).
- Record `time` is truncated to whole seconds (testnet 2026-10-16): a fill at …701.4 s was recorded as …701000. A `startTime` in the middle of a second leaves out that second's records.
- One record pair per trade, not per order (testnet 2026-10-16): a market close that filled in 18 trades produced 18 `REALIZED_PNL` and 18 `COMMISSION` records, each with its `tradeId`. A busy day reaches the 1000-record page sooner than the order count suggests.
- Testnet market-order commission was 0.04% of notional (2026-10-16).

### Cost to open a position (2026-10-26)

Source: Binance FAQ "How to Calculate Cost Required to Open a Position in Perpetual Futures Contracts" (updated 2026-07-08), testnet response. Docs only, apart from the two `-2019` refusals above and one entry sized to the engine's available-balance cap, which allows for this cost (D-25 note of 2026-10-26): it filled (testnet, 2026-10-28, XRPUSDT at 3× with an ETHUSDT position open).

- Cost = initial margin + open loss, checked against the available balance.
- Initial margin = assumed price × quantity ÷ leverage. A market order's assumed price is the last price × (1 + 0.15%), long or short.
- Open loss = quantity × |min(0, direction × (mark price − assumed price))|, direction 1 for a long and −1 for a short. A long's assumed price sits above the mark, so it has an open loss of about 0.15% of notional; a short's usually has none.
- The FAQ's worked example is inconsistent (its short cost uses the last price, "after an update of the assuming price logic"), and it doesn't say whether the commission is counted. Sizing allows for both (D-25 note of 2026-10-26).

### Liquidation price (2026-10-10)

Source: Binance FAQ "How to Calculate Liquidation Price of USDⓈ-M Futures Contracts" (updated 2025-12-31). Docs only.

- For an isolated one-way position, with Q the absolute size, EP the entry price, WB the isolated wallet balance, and MMR/`cum` from the position's bracket: long LP = (Q·EP − WB − cum) / (Q·(1 − MMR)); short LP = (Q·EP + WB + cum) / (Q·(1 + MMR)). If the notional at LP falls in another bracket, recompute with that bracket.

### Historical data archive: data.binance.vision (2026-10-18)

Source: [binance-public-data](https://github.com/binance/binance-public-data) README and `python/utility.py`, the archive's listing and files

- Public, no key. USDⓈ-M data is under `data/futures/um/`: `klines`, `markPriceKlines`, `indexPriceKlines`, `premiumIndexKlines`, `aggTrades`, and `trades` come monthly and daily; `fundingRate` monthly only; `metrics` and `bookDepth` daily only. The README lists only `aggTrades`, `klines`, and `trades`; the others exist anyway. There is no testnet data.
- URL: `https://data.binance.vision/data/futures/um/<monthly|daily>/klines/<SYMBOL>/<interval>/<SYMBOL>-<interval>-<YYYY-MM|YYYY-MM-DD>.zip`. The checksum file is the same URL plus `.CHECKSUM` and holds `<sha256 hex>  <file name>` (matched on `BTCUSDT-1h-2026-09.zip`). The S3 ETag is not a SHA-256.
- Intervals: the REST ones (1m to 1w), with `1mo` as the folder for 1M. No `1s`.
- Kline CSV, 12 columns: `open_time, open, high, low, close, volume, close_time, quote_volume, count, taker_buy_volume, taker_buy_quote_volume, ignore`. Some files start with a header row and some don't, with no clean cutoff (BTCUSDT 1h monthly: none through 2021-12, a header from 2022-01; daily files around 2022 are mixed), so detect it. Times are milliseconds; the README's switch to microseconds from 2025 applies to spot only.
- `markPriceKlines` and `premiumIndexKlines` have the same columns with zero volumes. `fundingRate` has `calc_time, funding_interval_hours, last_funding_rate`, no mark price; `calc_time` can be a few ms past the hour.
- BTCUSDT coverage: klines from 2019-12-31 (daily) and 2020-01 (monthly), while REST history starts 2019-09-08; `fundingRate` from 2020-01.
- Publishing: daily BTCUSDT 1h files appeared the next day between 07:51 and 14:17 UTC (2026-10-02 to 2026-10-17), monthly ones on the 2nd of the next month around 09:00–12:00 UTC, not on "the first Monday" the README gives. Times are from LastModified, which a re-upload can change.
- Files are sometimes replaced later (the README lists fixes), and the repo's issues report missing rows that were backfilled afterwards. Build URLs from the pattern rather than crawling listings, which hold stray objects, and check the candles for gaps after loading.
- BTCUSDT 1h, 2020-01-01 to 2026-10-18 (downloaded 2026-10-19): `klines` had no gaps. `markPriceKlines` lacked six whole UTC days inside otherwise complete monthly files: 2021-07-01, 2021-07-24 to 2021-07-27, 2022-07-31, 2022-10-02, 2023-02-24, and 2026-06-29. REST `GET /fapi/v1/markPriceKlines` returned all of them, so the archive files are incomplete rather than the exchange having been down. Funding settled every 8 hours throughout.
- 15m, BTCUSDT, ETHUSDT, SOLUSDT, and XRPUSDT, 2020-01-01 to 2026-10-18 (downloaded 2026-10-19):
  - `markPriceKlines` has no candles from 2020-01-19 13:00 to 13:30 UTC (BTC, ETH, and XRP; SOL wasn't listed yet) or from 2023-11-10 03:30 to 04:00 UTC (all four). REST has none for those times either, so these are real gaps.
  - The SOLUSDT archive starts with a partial month, 2020-09; 2020-08 is a 404.

### Klines and funding rate history over REST (2026-10-18)

Source: [Market Data](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data), mainnet response

| Endpoint | Weight | Notes |
|---|---|---|
| `GET /fapi/v1/klines` | By `limit`: 1–100 → 1, 101–500 → 2, 501–1000 → 5, 1001–1500 → 10 (measured; the docs' table puts each edge one lower) | `limit` default 500, max 1500 (`-1130` above). Ascending. Without times: the most recent candles. With only `endTime`: ends at the candle that opens at `endTime`. Paging from `startTime` reaches the first candle (BTCUSDT: 2019-09-08 17:00 UTC). The last candle can still be open; drop any whose `closeTime` is at or after the server time. `1s` → `-1120` |
| `GET /fapi/v1/markPriceKlines` | As klines | Volume fields are "Ignore" |
| `GET /fapi/v1/fundingRate` | Shares 500 per 5 min per IP with `GET /fapi/v1/fundingInfo`; no weight header | `limit` default 100, max 1000; `startTime`/`endTime` inclusive. Without times it returned 100 rows, and 500 with `limit=1000` (docs: the most recent 200). `startTime=0` counts as not sent; use 1. Fields: `fundingRate`, `fundingTime` (can be a few ms past the hour), `markPrice` (`""` on old rows), `rateType` (`Regular`, or `Special` for stock dividends). BTCUSDT history starts 2019-09-10 08:00 UTC |

### Kline stream and REST around a close (2026-10-19)

Source: testnet responses, BTCUSDT 1m

- The stream's closed message (`x: true`) carried the same values as the candle's last update before it.
- REST `GET /fapi/v1/klines` lags the stream. Right after a close, REST returned the candle with a smaller volume, and once a different close (at 0.3 s). Of the candles polled at fixed delays, one first matched the stream at 10 s, one still differed at 10 s, and one matched at 15 s and stayed matched at 30 and 60 s. The closed-candle feed therefore trusts REST only a minute after the close (D-42); whether mainnet lags too is unchecked.
- A stream left disconnected for 130 s and reconnected missed two closes; the feed read both from REST and continued on the stream with no gap or duplicate.

### Testnet market data is not mainnet's (2026-10-18)

Source: mainnet and testnet responses. No official statement for futures; the spot [Demo Mode](https://developers.binance.com/en/docs/products/spot/demo-mode/general-info) page calls its market data realistic, not real.

- Over the same three BTCUSDT 1h candles, testnet OHLC differed from mainnet's by up to about 0.1% and volume was about 20 times higher. The latest funding rates differed too. Testnet kline history starts 2019-09-02, before mainnet's. Backtests therefore use mainnet data (D-38).

### Fees (2026-10-18)

Source: [USDⓈ-M fee rates](https://www.binance.com/en/fee/futureFee), [Account — User Commission Rate](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account)

- Regular tier, USDT-margined: maker 0.0200%, taker 0.0500%; 0.0180% and 0.0450% with the BNB discount. Testnet charged 0.04% (Income history above).
- `GET /fapi/v1/commissionRate` (USER_DATA, weight 20, `symbol` required) returns the account's `makerCommissionRate`, `takerCommissionRate`, and `rpiCommissionRate`. The docs' example values are illustrative, not current rates.

### Limit order options (2026-10-19)

Source: [Common Definition](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/common-definition), [Error Codes](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/error-code). Docs only; nothing placed yet.

- `timeInForce`: `GTC` (valid for one year), `IOC`, `FOK`, `GTX` (Good Till Crossing, post only), `GTD` (Good Till Date), `RPI` (post only, matched only against orders from the app or web).
- A post-only order that can't rest as a maker is rejected: `-5022` "Due to the order could not be executed as maker, the Post Only order will be rejected." The backtest's limit entries follow this (D-45).
- `GTD`: `goodTillDate` must be more than 600 s after the current time (`-5040`). An order meant to expire at the next close of a candle shorter than about 10 minutes would need the engine to cancel it itself.
- `priceMatch`: `NONE`, `OPPONENT` (the other side's best price), `OPPONENT_5`/`_10`/`_20`, `QUEUE` (the best price on the order's own side), `QUEUE_5`/`_10`/`_20`. Only for `LIMIT`, `STOP`, and `TAKE_PROFIT` orders (`-5038`); `-5037` is an invalid price match.
- The New Order page itself wasn't read: the docs site served only its app shell to a plain fetch, so how `priceMatch` combines with `price` is unchecked.

## To verify

Check each item while implementing the related feature:

- [ ] Symbol filters: the error codes testnet actually returns for orders that violate them (docs list them under Orders, lookups, and unknown outcomes).
- [ ] Signed requests: the error code returned when the timestamp is outside `recvWindow`.
- [ ] Exchange-side stops: the error a `STOP_MARKET` gets when its trigger is already crossed (`-2021` or `-4142`), and whether a `closePosition` stop can be placed with no open position.
- [ ] A stop that triggers: the `ALGO_UPDATE` sequence (`TRIGGERING` → `TRIGGERED` → `FINISHED`?), which the engine doesn't log at INFO. The order it places is under Algo orders above.
- [ ] Trade history reads (D-51): whether `userTrades` with `startTime` returns the oldest fills first (the engine pages forward by time past 1000), whether `allOrders` with `orderId` alone reaches back only 7 days, and which time `allAlgoOrders`' `startTime`/`endTime` filter (creation or update).
- [ ] `GET /fapi/v1/openOrders` with a non-empty result (the engine reads `clientOrderId`, `orderId`, `side`, `type`, `origQty`, `reduceOnly`).
- [ ] Margin type and leverage: the errors when changing them with a position or open orders (docs: `-4047`, `-4048`, `-4161`).
- [ ] Income history paging past 1000 records within one day.
- [ ] Income history delay: how soon `REALIZED_PNL` and `COMMISSION` appear after a fill (D-30). The `trial` prints it. Its first run (2026-10-16) queried from a mid-second start time and missed the records. After the fix, the second run (2026-10-16) saw the close 1.1 s after the kill switch finished, on the second read. Only one sample on testnet; repeat it, and check mainnet before relying on it.
- [ ] Bulk cancels with orders actually open: on 2026-10-16 and again on 2026-10-21 (with a strategy position open) the stop had already expired with its position, so both calls ran on an empty book. Since the kill switch cancels only after a symbol is flat (D-27 note) and every engine stop uses `closePosition`, that is the normal case; the cancels only meet an order the engine doesn't place itself, so checking them needs a deliberately placed resting order.
- [ ] Liquidation price before entry (D-25): compare the engine's estimate with `liquidationPrice` from `positionRisk` while a position is open. The estimate uses notional ÷ leverage as WB, which runs one opening commission higher than the real `iw`.
- [ ] `GET /fapi/v1/commissionRate` with testnet keys, if the backtest reads the fee rate from it.
- [ ] Limit entries, before the engine places any (D-45): how a crossing `GTX` order is refused on testnet (an HTTP error with `-5022`, or an accepted order that expires), `GTD` expiry and its user data events, `priceMatch` with and without `price`, and whether `MIN_NOTIONAL` is checked at the limit price.
