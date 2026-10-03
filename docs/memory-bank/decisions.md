# Decisions

Design decisions and why they were made. Add new entries at the bottom. When a decision is reversed, mark the old entry `Superseded by D-XX` instead of deleting it.

Safety rules (`reduceOnly`, exchange-side stops, kill switch, key handling, …) live in CLAUDE.md, not here.

Entries dated `initial` were carried over from README.md. Where README gives no reason, **Why** says _not recorded_ — fill it in when known.

## D-01 Engine and GUI run as separate processes

- **Decision:** The Python engine and the Electron GUI are independent processes.
- **Why:** Closing the GUI must not stop trading.
- **Date:** initial

## D-02 Only the engine makes trading decisions

- **Decision:** Strategy evaluation and order placement happen only in the engine. The GUI and Discord bot send commands to the engine and never call Binance order endpoints themselves.
- **Why:** _not recorded_
- **Date:** initial

## D-03 WebSocket on localhost between engine and clients

- **Decision:** The engine talks to the GUI and the Discord bot over WebSocket on localhost.
- **Why:** _not recorded_
- **Date:** initial

## D-04 SQLite for records, Parquet for historical candles

- **Decision:** Trades, orders, and logs go in SQLite. Historical candle data goes in Parquet files.
- **Why:** _not recorded_
- **Date:** initial

## D-05 One-way position mode

- **Decision:** The account runs in one-way position mode, not hedge mode.
- **Why:** _not recorded_
- **Date:** initial

## D-06 The exchange is the source of truth

- **Decision:** On restart, the engine restores positions, open orders, and balances from Binance, not from its local records.
- **Why:** Local records can drift from the exchange (a missed fill, a crash mid-update); acting on stale state is dangerous with leverage.
- **Date:** initial

## D-07 Backtest and live share strategy code

- **Decision:** The backtest module runs the exact strategy code the live engine runs.
- **Why:** Backtest results then reflect what the live code actually does.
- **Date:** initial

## D-08 The GUI never handles Binance API keys

- **Decision:** Binance API keys live only in the engine. The GUI — main and renderer process alike — has no keys and makes no Binance calls; it gets positions, PnL, and logs from the engine over WebSocket.
- **Why:** All exchange access already goes through the engine (D-02), so the GUI has no use for keys. Keeping secrets in one process leaves one place to protect.
- **Date:** 2026-10-02
- **Consequence:** Dropped the roadmap item "GUI: move API key handling to the main process".

## D-09 Engine toolchain: uv, pytest, ruff

- **Decision:** The engine uses `uv` for the virtualenv, dependencies, lock file, and Python version; `pytest` for tests; `ruff` for lint and formatting.
- **Why:** One tool covers environment and dependencies, and `uv.lock` pins exact versions so the engine runs the same everywhere. `ruff` plays the role ESLint + Prettier play in the GUI.
- **Date:** 2026-10-02

## D-10 Own thin async Binance client on httpx + websockets

- **Decision:** The engine talks to Binance through a small in-house client built on `httpx` (REST) and `websockets` (streams), using asyncio. No ccxt, no Binance SDK.
- **Why:** The engine needs only a handful of endpoints. Writing them directly keeps every safety-relevant parameter (`reduceOnly`, stop order types, `recvWindow`) visible in our own code instead of behind a library, and the client is easy to mock in tests. asyncio fits running the market stream, the user data stream, and the GUI/bot WebSocket server in one process.
- **Alternatives:** ccxt — a multi-exchange abstraction we don't need (Binance only). Official Binance SDK — reconsider if keeping up with API changes by hand becomes a burden.
- **Date:** 2026-10-02

## D-11 HMAC API keys for now; reconsider Ed25519 before mainnet

- **Decision:** Use Binance's system-generated HMAC keys (API key + secret). The settings loader and request signing assume HMAC.
- **Why:** Simplest to set up and sign, and enough for testnet with virtual funds.
- **Revisit:** Before creating the mainnet key. With a self-generated Ed25519 key, Binance stores only the public key, so the private key never leaves this machine. That would need a private-key setting in place of `BINANCE_API_SECRET` and a different signing function.
- **Date:** 2026-10-02

## D-12 Engine diagnostic logs go to rolling text files that are never renamed

- **Decision:** The engine logs to the console and to text files in `engine/logs/`. Rolling over (size limit or a new UTC date) always opens a new file with a higher index or a new start time; a written file is never renamed. Details are in `techContext.md` (Engine logs).
- **Why:** The stdlib `RotatingFileHandler` and `TimedRotatingFileHandler` roll over by renaming the active file, and on Windows a rename fails while another program (an editor, a log viewer) has the file open. Start-time names also make it easy to find what happened on a given day. The user chose the name format, including index `000` on the first file.
- **Relation to D-04:** These are diagnostic logs. SQLite remains the plan for trade and order records; whether log lines also go to SQLite is decided with the GUI log view.
- **Date:** 2026-10-03
