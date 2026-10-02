# Active Context

_Last updated: 2026-10-02_

## Current focus

`engine/` is scaffolded with uv, pytest, and ruff (D-09). Next is the first engine milestone: a read-only connection to Binance testnet, using the in-house client approach from D-10.

## Open questions

- **Engine ↔ GUI message protocol:** message types and schemas are undefined.
- **First strategy:** not chosen.

## Next steps

1. Get Binance Futures testnet API keys into `.env` (the user does this).
2. Engine settings loader: read `.env`; testnet unless `BINANCE_TESTNET` is explicitly `false`; reject unrecognized values; never expose secrets in logs or `repr`.
3. Engine: read-only testnet connection — `exchangeInfo`, balance, positions, a kline stream. Verify `binanceNotes.md` items along the way.
