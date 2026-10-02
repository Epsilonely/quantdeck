# Progress

Current state of each area. This is not a changelog — history lives in git.

## Current state

- **GUI:** unmodified electron-vite React + TypeScript template (logo page, `Versions` component, a `ping` IPC test). No QuantDeck features yet.
- **Engine:** settings loader (testnet by default, masked secrets) and a read-only Binance client: signed REST queries (server time, exchange info, account config, balances, positions) and a reconnecting kline stream. `quantdeck-engine check` exercises all of it against the configured network. No orders, no user data stream, no trading loop yet.
- **Bot:** `bot/` doesn't exist yet.
- **Strategy:** none chosen.

## Roadmap

Keep in sync with the roadmap section of README.md.

- [x] Repository and GUI scaffold
- [x] Memory bank and project docs (CLAUDE.md, `docs/memory-bank/`)
- [x] Engine: Binance testnet connection, market data stream, position and balance queries
- [ ] Engine: order execution (entry, `reduceOnly` close, exchange-side stop orders)
- [ ] Engine: risk management (max position, daily loss limit, kill switch)
- [ ] Engine ↔ GUI WebSocket connection
- [ ] GUI: position / PnL / log monitoring, start/stop, emergency close
- [ ] Backtest module (same strategy code as live)
- [ ] First strategy and testnet forward test
- [ ] Discord bot
- [ ] Trade history analysis screen

## Known issues

- Kline updates sent while the stream is reconnecting are lost. Before strategies rely on closed candles, backfill the gap from REST klines after each reconnect.
