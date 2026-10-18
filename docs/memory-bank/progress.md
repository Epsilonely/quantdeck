# Progress

Current state of each area. This is not a changelog — history lives in git.

## Current state

- **GUI:** one screen showing the engine connection, network, halt, today's loss and limit, the base and wallet balances, each symbol's mark price, position, stops, and orders, and the engine log. The main process connects to `run` (D-32), reconnects every 2 s, and relays to the renderer over preload IPC. It starts the engine when none answers at GUI start, and the screen has start and stop buttons (D-34). Emergency close, lifting the halt, and stopping the engine go through a native confirmation dialog. Tested against `run` on testnet 2026-10-16: connect, a state about once a second, the stale-state banner after the engine was killed, retries while it was down, and reconnecting with the new engine's log backlog. Also tested 2026-10-16: the GUI started a hidden engine with no console window, the engine kept running after the GUI closed, a reopened GUI connected to it without starting another, `stop_engine` stopped it with exit code 0, the start button started it again, and a start refused by the instance lock showed the engine's error. Emergency close and lifting the halt were tried from the GUI on a flat testnet account on 2026-10-16: the kill switch finished in 0.4 s, the halt banner showed its reason and time, and lifting it deleted the halt record. The user also stopped the engine with the GUI's stop button. Times on the screen are local (KST). An icon toggle in the header switches the theme between system, light, and dark (D-35). Tested 2026-10-16: each choice applied to the page, scrollbars, and title bar, the choice survived a GUI restart, and system followed the Windows dark mode. The sliding selection and the color fade ran, and with reduced motion emulated no transition ran. Switching the Windows app mode while the GUI runs is untested. No strategy settings or charts yet.
- **Engine:** settings from `.env` and `engine/config.toml` with hard bounds (D-28), logging to the console and rolling files in local time (D-12), a Binance client (signed REST, a reconnecting kline stream, a user data stream that parses `ORDER_TRADE_UPDATE`, `ACCOUNT_UPDATE`, and `ALGO_UPDATE`), and order execution per symbol: startup margin type and leverage (D-16), sizing from the day's base balance (D-22, D-23, D-25), market entries and `reduceOnly` closes, `closePosition` stops with retries and emergency stops (D-18, D-19, D-26), lookups for unknown outcomes (D-20), and local state resynced on every stream connect (D-13). Risk management: today's loss from income history (D-24, D-30), the entry gate, and the kill switch with a halt record that survives restarts (D-27, D-29). `quantdeck-engine check` is a read-only connection check. `quantdeck-engine trial` runs entry → stop → emergency stop → close, then the kill switch, once on testnet; it passed on 2026-10-16. `quantdeck-engine lift-halt` lifts a halt. `quantdeck-engine run` is the long-running engine (D-31): startup checks, the user data stream, the risk guard's checks, a display-only mark price stream, and the client WebSocket server (D-32, D-33) with the `emergency_close`, `lift_halt`, and `stop_engine` (D-34) commands; no strategy yet. Tested on testnet 2026-10-16: risk checks ran at startup, 3 s after the stream connected, and a minute later; a second `run` was refused; a Node client with a wrong token was refused, and one with the right token got the welcome, logs, state about once a second, and a `lift_halt` result. Ctrl+C in PowerShell stopped it cleanly and deleted the connection file. `emergency_close` and `lift_halt` were tried from the GUI on 2026-10-16 (see GUI).
- **Bot:** `bot/` doesn't exist yet.
- **Backtest:** `quantdeck-engine backtest` runs a strategy over mainnet history (D-36 to D-40): candles and funding from the archive and REST, checked and kept in `engine/data/` (D-38), the live sizing and risk rules (D-37), mark-price stops, slippage, fees, and funding (D-40), and a report with expectancy, drawdown, losing streak, daily-limit days, and a yearly breakdown. The runs that chose the first strategy (2026-10-19) are summarized in D-41.
- **Strategy:** the Donchian breakout (`strategy/donchian.py`) and the Bollinger mean reversion exist for backtests only. The first strategy is the Donchian breakout 55/20 on BTCUSDT 4h at 2× (D-41); it isn't connected to `run` yet, and the Bollinger one was dropped.

## Roadmap

Keep in sync with the roadmap section of README.md.

- [x] Repository and GUI scaffold
- [x] Memory bank and project docs (CLAUDE.md, `docs/memory-bank/`)
- [x] Engine: Binance testnet connection, market data stream, position and balance queries
- [x] Engine: order execution (entry, `reduceOnly` close, exchange-side stop orders)
- [x] Engine: risk management (max position, daily loss limit, kill switch)
- [x] Engine ↔ GUI WebSocket connection
- [x] GUI: position / PnL / log monitoring, start/stop, emergency close
- [x] Backtest module (same strategy code as live)
- [ ] First strategy and testnet forward test
- [ ] Discord bot
- [ ] Trade history analysis screen

## Known issues

- Kline updates sent while the stream is reconnecting are lost. Strategies act on closed candles (D-36), so before one trades, backfill the gap from REST klines after each reconnect (GitHub issue #9).
- Closing a stream takes 5–7 s on testnet (`websockets` default `close_timeout`), which slows `check` and the shutdown of `run`; see `binanceNotes.md`. A GUI start pressed during that time is refused by the instance lock, and the GUI shows the lock error.
- The entry gate doesn't count the risk of positions open on other symbols (D-24 Limitation). Fix before a second symbol trades.
