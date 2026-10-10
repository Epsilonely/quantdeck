# Progress

Current state of each area. This is not a changelog — history lives in git.

## Current state

- **GUI:** unmodified electron-vite React + TypeScript template (logo page, `Versions` component, a `ping` IPC test). No QuantDeck features yet.
- **Engine:** settings from `.env` and `engine/config.toml` with hard bounds (D-28), logging to the console and rolling files (D-12), a Binance client (signed REST, a reconnecting kline stream, a user data stream that parses `ORDER_TRADE_UPDATE`, `ACCOUNT_UPDATE`, and `ALGO_UPDATE`), and order execution per symbol: startup margin type and leverage (D-16), sizing from the day's base balance (D-22, D-23, D-25), market entries and `reduceOnly` closes, `closePosition` stops with retries and emergency stops (D-18, D-19, D-26), lookups for unknown outcomes (D-20), and local state resynced on every stream connect (D-13). `quantdeck-engine check` is a read-only connection check; `quantdeck-engine trial` runs entry → stop → emergency stop → close once on testnet and passed on 2026-10-10. No daily loss limit, kill switch, or trading loop yet.
- **Bot:** `bot/` doesn't exist yet.
- **Strategy:** none chosen.

## Roadmap

Keep in sync with the roadmap section of README.md.

- [x] Repository and GUI scaffold
- [x] Memory bank and project docs (CLAUDE.md, `docs/memory-bank/`)
- [x] Engine: Binance testnet connection, market data stream, position and balance queries
- [x] Engine: order execution (entry, `reduceOnly` close, exchange-side stop orders)
- [ ] Engine: risk management (max position, daily loss limit, kill switch)
- [ ] Engine ↔ GUI WebSocket connection
- [ ] GUI: position / PnL / log monitoring, start/stop, emergency close
- [ ] Backtest module (same strategy code as live)
- [ ] First strategy and testnet forward test
- [ ] Discord bot
- [ ] Trade history analysis screen

## Known issues

- Kline updates sent while the stream is reconnecting are lost. Before strategies rely on closed candles, backfill the gap from REST klines after each reconnect.
- Closing a stream takes 5–7 s on testnet (`websockets` default `close_timeout`), which slows `check` and will slow shutdown; see `binanceNotes.md`.
