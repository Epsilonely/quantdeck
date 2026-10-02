# Progress

Current state of each area. This is not a changelog — history lives in git.

## Current state

- **GUI:** unmodified electron-vite React + TypeScript template (logo page, `Versions` component, a `ping` IPC test). No QuantDeck features yet.
- **Engine, bot:** `engine/` and `bot/` don't exist yet.
- **Strategy:** none chosen.

## Roadmap

Keep in sync with the roadmap section of README.md.

- [x] Repository and GUI scaffold
- [x] Memory bank and project docs (CLAUDE.md, `docs/memory-bank/`)
- [ ] GUI: move API key handling to the main process — see the open question in `activeContext.md`
- [ ] Engine: Binance testnet connection, market data stream, position and balance queries
- [ ] Engine: order execution (entry, `reduceOnly` close, exchange-side stop orders)
- [ ] Engine: risk management (max position, daily loss limit, kill switch)
- [ ] Engine ↔ GUI WebSocket connection
- [ ] GUI: position / PnL / log monitoring, start/stop, emergency close
- [ ] Backtest module (same strategy code as live)
- [ ] First strategy and testnet forward test
- [ ] Discord bot
- [ ] Trade history analysis screen

## Known issues

None.
