# Progress

Current state of each area. This is not a changelog — history lives in git.

## Current state

- **GUI:** one screen showing the engine connection, network, halt, today's loss and limit, the base and wallet balances, each symbol's mark price, position, stops, and orders, and the engine log. The main process connects to `run` (D-32), reconnects every 2 s, and relays to the renderer over preload IPC. It starts the engine when none answers at GUI start, and the screen has start and stop buttons (D-34). Emergency close, lifting the halt, and stopping the engine go through a native confirmation dialog. Tested against `run` on testnet 2026-10-16: connect, a state about once a second, the stale-state banner after the engine was killed, retries while it was down, and reconnecting with the new engine's log backlog. Also tested 2026-10-16: the GUI started a hidden engine with no console window, the engine kept running after the GUI closed, a reopened GUI connected to it without starting another, `stop_engine` stopped it with exit code 0, the start button started it again, and a start refused by the instance lock showed the engine's error. Not yet tried from the GUI: emergency close and lifting the halt (the stop was sent from a test client, since the confirmation dialog is native). No strategy settings or charts yet.
- **Engine:** settings from `.env` and `engine/config.toml` with hard bounds (D-28), logging to the console and rolling files (D-12), a Binance client (signed REST, a reconnecting kline stream, a user data stream that parses `ORDER_TRADE_UPDATE`, `ACCOUNT_UPDATE`, and `ALGO_UPDATE`), and order execution per symbol: startup margin type and leverage (D-16), sizing from the day's base balance (D-22, D-23, D-25), market entries and `reduceOnly` closes, `closePosition` stops with retries and emergency stops (D-18, D-19, D-26), lookups for unknown outcomes (D-20), and local state resynced on every stream connect (D-13). Risk management: today's loss from income history (D-24, D-30), the entry gate, and the kill switch with a halt record that survives restarts (D-27, D-29). `quantdeck-engine check` is a read-only connection check. `quantdeck-engine trial` runs entry → stop → emergency stop → close, then the kill switch, once on testnet; it passed on 2026-10-16. `quantdeck-engine lift-halt` lifts a halt. `quantdeck-engine run` is the long-running engine (D-31): startup checks, the user data stream, the risk guard's checks, a display-only mark price stream, and the client WebSocket server (D-32, D-33) with the `emergency_close`, `lift_halt`, and `stop_engine` (D-34) commands; no strategy yet. Tested on testnet 2026-10-16: risk checks ran at startup, 3 s after the stream connected, and a minute later; a second `run` was refused; a Node client with a wrong token was refused, and one with the right token got the welcome, logs, state about once a second, and a `lift_halt` result. Ctrl+C in PowerShell stopped it cleanly and deleted the connection file. Not yet tried on testnet: `emergency_close` from a client (planned from the GUI).
- **Bot:** `bot/` doesn't exist yet.
- **Strategy:** none chosen.

## Roadmap

Keep in sync with the roadmap section of README.md.

- [x] Repository and GUI scaffold
- [x] Memory bank and project docs (CLAUDE.md, `docs/memory-bank/`)
- [x] Engine: Binance testnet connection, market data stream, position and balance queries
- [x] Engine: order execution (entry, `reduceOnly` close, exchange-side stop orders)
- [x] Engine: risk management (max position, daily loss limit, kill switch)
- [ ] Engine ↔ GUI WebSocket connection
- [ ] GUI: position / PnL / log monitoring, start/stop, emergency close
- [ ] Backtest module (same strategy code as live)
- [ ] First strategy and testnet forward test
- [ ] Discord bot
- [ ] Trade history analysis screen

## Known issues

- Kline updates sent while the stream is reconnecting are lost. Before strategies rely on closed candles, backfill the gap from REST klines after each reconnect.
- Closing a stream takes 5–7 s on testnet (`websockets` default `close_timeout`), which slows `check` and the shutdown of `run`; see `binanceNotes.md`. A GUI start pressed during that time is refused by the instance lock, and the GUI shows the lock error.
- The entry gate doesn't count the risk of positions open on other symbols (D-24 Limitation). Fix before a second symbol trades.
