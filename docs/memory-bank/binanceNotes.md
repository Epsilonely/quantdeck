# Binance Notes

Binance USDⓈ-M Futures behavior the code can't tell you: quirks, limits, and gotchas.

Move an item to **Verified** only after confirming it against the official docs or an actual testnet/mainnet response. Include the date and the source (doc URL or "testnet response"). Binance changes its API, so re-check old entries when something behaves unexpectedly.

## Verified

### Base URLs (2026-10-02)

Source: [General Info](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info), [WebSocket Market Streams — Connect](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Connect)

| | REST | WebSocket root |
|---|---|---|
| Mainnet | `https://fapi.binance.com` | `wss://fstream.binance.com` |
| Testnet | `https://demo-fapi.binance.com` | `wss://demo-fstream.binance.com` |

- The docs no longer list the old testnet host `testnet.binancefuture.com`; older tutorials and libraries may still use it.

### WebSocket routing (2026-10-02)

Source: [Important WebSocket Change Notice](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Important-WebSocket-Change-Notice), [Connect](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Connect)

Mainnet streams must go through a routed path. Legacy unrouted URLs were supported only until 2026-04-23; an unrouted connection now receives `/public` data only.

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

## To verify

Check each item while implementing the related feature:

- [ ] Testnet: whether testnet API keys are separate from mainnet keys, and whether `demo-fstream.binance.com` uses the same `/public` `/market` `/private` routes as mainnet.
- [ ] Position mode: how to confirm the account is in one-way mode (`dualSidePosition`), and how `reduceOnly` behaves in each mode.
- [ ] Symbol filters from `exchangeInfo` (price tick size, quantity step size, minimum notional) and how orders that violate them are rejected.
- [ ] Signed requests: `timestamp` / `recvWindow`, and the error returned when the local clock drifts.
- [ ] Rate limits: request-weight and order-count limits, and the response headers that report current usage.
- [ ] User Data Stream: `listenKey` expiry and keepalive interval; exact `/private` URL format with a listenKey.
- [ ] Exchange-side stop orders: order type, `closePosition` vs `reduceOnly`, trigger price source (mark vs last price), and whether conditional orders use a separate endpoint.
- [ ] State restore on restart: which endpoints return open positions, open orders, and balances.
