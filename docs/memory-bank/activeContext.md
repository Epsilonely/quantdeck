# Active Context

_Last updated: 2026-10-03_

## Current focus

The read-only testnet connection works end to end (`quantdeck-engine check`), and the engine now logs to `engine/logs/` (GitHub issue #1). Next is the order execution milestone: entry, `reduceOnly` close, and exchange-side stop orders. Seeing fills reliably needs the user data stream first.

## Open questions

- **Engine ↔ GUI message protocol:** message types and schemas are undefined.
- **First strategy:** not chosen.

## Next steps

1. User data stream: `listenKey` lifecycle and the `/private` route; parse `ORDER_TRADE_UPDATE` and `ACCOUNT_UPDATE`. Verify the related `binanceNotes.md` items first.
2. Order execution on testnet: entry, `reduceOnly` close, and exchange-side stop orders (algo orders). Round prices and quantities to the symbol filters.
3. Backfill klines missed during stream reconnects (see Known issues in `progress.md`). A GitHub issue for this was drafted but the user put filing it on hold (2026-10-03).
