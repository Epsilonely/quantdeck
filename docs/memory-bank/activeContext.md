# Active Context

_Last updated: 2026-10-03_

## Current focus

The engine connects to testnet read-only, logs to `engine/logs/`, and receives the user data stream (GitHub issue #2). Next is the order execution milestone: entry, `reduceOnly` close, and exchange-side stop orders. Work items are tracked as GitHub issues.

## Open questions

- **Engine ↔ GUI message protocol:** message types and schemas are undefined.
- **First strategy:** not chosen.

## Next steps

1. Order execution on testnet: entry, `reduceOnly` close, and exchange-side stop orders (algo orders). Round prices and quantities to the symbol filters. Keep local order/position state from user data events, resyncing from REST on every `UserStreamConnected` (D-13), and check real event payloads against `binanceNotes.md`. Parse `ALGO_UPDATE` along with the stop orders.
2. Backfill klines missed during stream reconnects (see Known issues in `progress.md`). A GitHub issue for this was drafted but the user put filing it on hold (2026-10-03).
