# Active Context

_Last updated: 2026-10-02_

## Current focus

The engine settings loader is done. Next is the first engine milestone: a read-only connection to Binance testnet, using the in-house client approach from D-10.

## Open questions

- **Engine ↔ GUI message protocol:** message types and schemas are undefined.
- **First strategy:** not chosen.

## Next steps

1. Get Binance Futures testnet API keys into `.env` (the user does this). `uv run quantdeck-engine` reports missing keys until then.
2. Engine: read-only testnet connection — `exchangeInfo`, balance, positions, a kline stream (`/market` route). Verify `binanceNotes.md` items along the way.
