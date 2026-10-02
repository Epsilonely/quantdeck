# Binance Notes

Binance USDⓈ-M Futures behavior the code can't tell you: quirks, limits, and gotchas.

Move an item to **Verified** only after confirming it against the official docs or an actual testnet/mainnet response. Include the date and the source (doc URL or "testnet response"). Binance changes its API, so re-check old entries when something behaves unexpectedly.

## Verified

_None yet._

## To verify

Check each item while implementing the related feature:

- [ ] Testnet: REST and WebSocket base URLs; whether testnet API keys are separate from mainnet keys.
- [ ] Position mode: how to confirm the account is in one-way mode (`dualSidePosition`), and how `reduceOnly` behaves in each mode.
- [ ] Symbol filters from `exchangeInfo` (price tick size, quantity step size, minimum notional) and how orders that violate them are rejected.
- [ ] Signed requests: `timestamp` / `recvWindow`, and the error returned when the local clock drifts.
- [ ] Rate limits: request-weight and order-count limits, and the response headers that report current usage.
- [ ] User Data Stream: `listenKey` expiry and keepalive interval; maximum lifetime of a single WebSocket connection.
- [ ] Exchange-side stop orders: order type, `closePosition` vs `reduceOnly`, trigger price source (mark vs last price), and whether conditional orders use a separate endpoint.
- [ ] State restore on restart: which endpoints return open positions, open orders, and balances.
