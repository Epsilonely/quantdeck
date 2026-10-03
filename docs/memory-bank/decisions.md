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

## D-13 User data stream: fresh listenKey per connection, never deleted, resync after every connect

- **Decision:** Before every connection the engine fetches the `listenKey` with `POST` (which returns the active key) instead of reusing a stored one, keeps it alive every 30 minutes, and never sends `DELETE`. Every (re)connect is reported to the consumer, which must then refresh positions, balances, and open orders from REST.
- **Why:** A nonexistent key connects silently, so a stale key would look healthy while delivering nothing. The key is shared by every process on the account, so a `DELETE` from a short-lived process (such as `check`) would cut off a running engine; an unused key expires on its own after 60 minutes. Events sent while disconnected are lost, and the exchange is the source of truth (D-06).
- **Date:** 2026-10-03

## D-14 The engine's Binance account is used by the engine only

- **Decision:** Nobody trades manually on the account the engine uses; manual trading happens on a different account. The engine treats every position and open order on its account as its own.
- **Why:** In one-way mode (D-05) a symbol has a single net position, so manual and engine trades in the same symbol can't be told apart. A dedicated account keeps the kill switch's "cancel all orders, close all positions" correct as written and keeps account PnL equal to engine PnL. Sharing the account would also share margin, so manual losses could liquidate engine positions.
- **Consequence:** On startup the engine adopts whatever positions and orders it finds (D-06). What it does with a position that has no exchange-side stop is decided with order execution. Whether sub-accounts are available for the user's Binance account is unchecked.
- **Date:** 2026-10-03

## D-15 Multi-symbol structure, one symbol in operation first

- **Decision:** Engine state, limits, and streams are keyed by symbol, so adding symbols is a configuration change. The engine runs a single symbol until the first strategy has been forward-tested on testnet. The symbol itself is chosen with the first strategy.
- **Why:** Hard-coding one symbol would force a restructure later, while running several from the start would need account-wide exposure limits and margin allocation before anything else works.
- **Date:** 2026-10-03

## D-16 Isolated margin, applied by the engine at startup

- **Decision:** Positions use isolated margin. Margin type and leverage per symbol come from engine configuration. At startup the engine reads the account: if the symbol has no position, it applies the configured values; if a position exists under different settings, it refuses to trade and warns. The leverage cap is decided with risk management. `check` stays read-only.
- **Why:** The biggest risk in automated trading is a bug such as a missing stop or a wrong quantity. Isolated margin caps that loss at the position's margin; cross margin would put the whole balance at risk. Keeping the values in configuration means the account can't silently drift from what the engine assumes.
- **Consequence:** Liquidation sits closer to entry than with cross margin, so before every entry the engine checks that the stop price is safely inside the liquidation price.
- **Date:** 2026-10-03

## D-17 Market orders for entries and strategy exits, to start

- **Decision:** Entries and strategy-signal exits use market orders (exits with `reduceOnly`). Limit orders are added when costs or a strategy call for them, so the order code keeps the order type open for extension. Stop-loss orders are decided separately.
- **Why:** A market order fills at once, which keeps the entry → exchange-side stop → exit flow simple while it is being proven. The cost is taker fees and unbounded slippage in fast markets.
- **Date:** 2026-10-03

## D-18 Exchange-side stops: `STOP_MARKET` with `closePosition`, triggered by mark price

- **Decision:** Every position's stop is a `STOP_MARKET` algo order with `closePosition=true`, `workingType=MARK_PRICE`, and `priceProtect` off. When a position closes by any other route, the engine cancels its leftover stop. Safety principle 5 now reads "`reduceOnly` closes and `closePosition` stops".
- **Why:** `closePosition` closes whatever position exists when it triggers, so the stop never has to be resized when the position changes; a quantity-based `reduceOnly` stop leaves part of the position unprotected until it is updated. Mark price is what liquidation uses, so "the stop fires before liquidation" (D-16) can be checked in a single price type, and mark price ignores short wicks in the last price. `priceProtect` can block the trigger when mark and last price diverge, which is exactly when a stop matters.
- **Trade-off:** A mark-price stop can fire later than a last-price stop when the last price moves first, so fills can be worse than the trigger price.
- **Date:** 2026-10-03


## D-19 Never leave a position without an exchange-side stop

- **Decision:**
  - After an entry fills, the engine places its stop at once. If that fails, it retries a few times within a few seconds, checking before each retry whether the stop was in fact placed (the outcome of a failed request can be unknown). If the stop is still not confirmed, or its trigger price has already been crossed, the engine closes the position with a market `reduceOnly` order, stops trading that symbol, and logs an ERROR.
  - At startup, a position without a stop gets an emergency stop at a configured distance from its entry price and is adopted. If that trigger price is already crossed, would sit beyond the liquidation price (D-16), or can't be placed (as above), the engine closes the position instead. Either way it logs an ERROR.
- **Why:** Safety principle 6. Brief retries ride out transient errors without leaving the position unprotected for long. For a position found at startup, the user chose an emergency stop over closing at whatever price the restart happens to meet; because the engine no longer knows where the strategy meant to stop out, the distance is a configured risk setting rather than a strategy value.
- **Open:** The emergency distance is set with the risk limits. Whether the strategy manages an adopted position is decided with the strategy interface.
- **Date:** 2026-10-03

## D-20 Unknown order outcomes are resolved by lookup, never by blind retry

- **Decision:** When an order request times out, fails at the network level, or gets a 5xx, the engine waits briefly and looks the order up by its client ID (REST query plus user data stream events). Found: carry on with it. Not found: resend once with the same client ID. Still unclear: stop trading that symbol and resync positions and orders from REST, which brings D-19 into play for any position left without a stop.
- **Why:** Binance enforces client-ID uniqueness only among open orders, and a market order closes the moment it fills, so blindly resending an entry can fill it twice. A 5xx means the outcome is unknown, not that the order failed.
- **Date:** 2026-10-03

## D-21 Client order IDs encode the order's role

- **Decision:** Client order IDs and `clientAlgoId`s follow `qd_<role>_<symbol>_<ms timestamp>_<seq>`, with roles `e` entry, `x` strategy exit, `s` stop, `k` kill switch — e.g. `qd_s_BTCUSDT_1759480000123_01` (29 of the 36 allowed characters). A retry reuses the original ID.
- **Why:** After a restart the engine can tell what each open order is for from the exchange alone (D-06), logs read clearly, and reusing the ID on retry is what makes D-20's lookup possible. Random IDs would need a local record to recover each order's role.
- **Date:** 2026-10-03

## D-22 Position size from a fixed risk amount

- **Decision:** Quantity = risk amount ÷ |entry price − stop price|, where the risk amount is a configured fraction of the account balance. Strategies supply a stop price with every entry signal. The quantity is rounded down to `stepSize`, an entry below `MIN_NOTIONAL` is skipped, and the maximum position size and leverage cap trim it further.
- **Why:** Each stop-out then loses about the same amount, so the daily loss limit and kill switch can be reasoned about as a number of stop-outs. Every entry needs a stop anyway (D-19), so asking the strategy for it costs nothing. Rounding down keeps the risk at or below the configured amount.
- **Caveat:** Realized losses can exceed the risk amount through stop slippage, mark-price trigger lag (D-18), and fees.
- **Open:** The fraction, and which balance it applies to, are set with the risk limits.
- **Date:** 2026-10-03

