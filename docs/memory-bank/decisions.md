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
- **Note:** The engine's diagnostic logs go to rolling files (D-12); whether log lines are also stored in SQLite is still open.

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
- **Consequence:** On startup the engine adopts whatever positions and orders it finds (D-06). What it does with a position that has no exchange-side stop is set by D-19 and D-26. Whether sub-accounts are available for the user's Binance account is unchecked.
- **Date:** 2026-10-03

## D-15 Multi-symbol structure, one symbol in operation first

- **Decision:** Engine state, limits, and streams are keyed by symbol, so adding symbols is a configuration change. The engine runs a single symbol until the first strategy has been forward-tested on testnet. The symbol itself is chosen with the first strategy.
- **Why:** Hard-coding one symbol would force a restructure later, while running several from the start would need account-wide exposure limits and margin allocation before anything else works.
- **Date:** 2026-10-03

## D-16 Isolated margin, applied by the engine at startup

- **Decision:** Positions use isolated margin, a code constant. Leverage per symbol comes from engine configuration. At startup the engine reads the account: if the symbol has no position, it applies the configured values; if a position exists under different settings, it refuses to trade and warns. The leverage values and the liquidation check are set in D-25. `check` stays read-only.
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
- **Note:** Binance itself expires a `closePosition` stop once its position is gone (testnet, 2026-10-10), so the engine's cancel is a backstop.
- **Date:** 2026-10-03

## D-19 Never leave a position without an exchange-side stop

- **Decision:**
  - After an entry fills, the engine places its stop at once. If that fails, it retries a few times within a few seconds, checking before each retry whether the stop was in fact placed (the outcome of a failed request can be unknown). If the stop is still not confirmed, or its trigger price has already been crossed, the engine closes the position with a market `reduceOnly` order, stops trading that symbol, and logs an ERROR.
  - At startup, a position without a stop gets an emergency stop (placed per D-26) and is adopted. If that trigger price is already crossed, would sit beyond the liquidation price (D-16), or can't be placed (as above), the engine closes the position instead. Either way it logs an ERROR.
- **Why:** Safety principle 6. Brief retries ride out transient errors without leaving the position unprotected for long. For a position found at startup, the user chose an emergency stop over closing at whatever price the restart happens to meet; because the engine no longer knows where the strategy meant to stop out, the distance comes from the risk settings rather than the strategy.
- **Open:** Whether the strategy manages an adopted position is decided with the strategy interface.
- **Date:** 2026-10-03

## D-20 Unknown order outcomes are resolved by lookup, never by blind retry

- **Decision:** When an order request times out, fails at the network level, or gets a 5xx, the engine waits briefly and looks the order up by its client ID with a REST query. Found: carry on with it. Not found: resend once with the same client ID. Still unclear: stop trading that symbol and resync positions and orders from REST, which brings D-19 into play for any position left without a stop. For a stop, D-19's check of the open algo orders does this job, because the docs give no "not found" code for algo orders.
- **Why:** Binance enforces client-ID uniqueness only among open orders, and a market order closes the moment it fills, so blindly resending an entry can fill it twice. A 5xx means the outcome is unknown, not that the order failed.
- **Date:** 2026-10-03

## D-21 Client order IDs encode the order's role

- **Decision:** Client order IDs and `clientAlgoId`s follow `qd_<role>_<symbol>_<ms timestamp>_<seq>`, with roles `e` entry, `x` exit (strategy exits, and closes after a failed stop), `s` stop, `k` kill switch — e.g. `qd_s_BTCUSDT_1759480000123_01` (29 of the 36 allowed characters). A retry reuses the original ID.
- **Why:** After a restart the engine can tell what each open order is for from the exchange alone (D-06), logs read clearly, and reusing the ID on retry is what makes D-20's lookup possible. Random IDs would need a local record to recover each order's role.
- **Date:** 2026-10-03

## D-22 Position size from a fixed risk amount

- **Decision:** Quantity = risk amount ÷ |entry price − stop price|, where the risk amount is a configured fraction of the day's base balance (D-23). Strategies supply a stop price with every entry signal. The quantity is rounded down to `stepSize`, an entry below `MIN_NOTIONAL` is skipped, and the daily-limit entry gate (D-24) and the position caps (D-25) trim it further.
- **Why:** Each stop-out then loses about the same amount, so the daily loss limit and kill switch can be reasoned about as a number of stop-outs. Every entry needs a stop anyway (D-19), so asking the strategy for it costs nothing. Rounding down keeps the risk at or below the configured amount.
- **Caveat:** Realized losses can exceed the risk amount through stop slippage, mark-price trigger lag (D-18), and fees.
- **Note:** The entry price is estimated with the mark price, which Binance also uses for a market order's `MIN_NOTIONAL` check. A symbol takes one entry per position; adding to an open position is out of scope for now.
- **Date:** 2026-10-03

## D-23 One base balance per UTC day; risk per trade is a fraction of it

- **Decision:** Risk limits are computed from the day's base balance: the wallet balance of the margin asset (USDT) at 00:00 UTC (09:00 KST), fixed until the next 00:00 UTC. Every risk-management day runs from 00:00 UTC. At startup the engine rebuilds the base from the exchange: the current wallet balance minus every income record since 00:00 UTC (income history). D-22's risk amount is a configured fraction of the base; testnet starts at 1%.
- **Why:** One number per day keeps the arithmetic readable: daily limit ÷ risk fraction = stop-outs per day (D-24). The wallet balance doesn't swing with unrealized PnL as the margin balance does, and doesn't shrink as margin gets tied up as the available balance would once several symbols trade (D-15). The base can be rebuilt from the exchange after a restart (D-06). UTC matches the logs (D-12) and Binance timestamps, and the day turns at 09:00 KST, when the user is usually awake.
- **Alternatives:** The current wallet, margin, or available balance; days starting at 00:00 KST.
- **Date:** 2026-10-10

## D-24 Daily loss limit counts realized PnL, fees, and funding; entries are capped by the room left

- **Decision:**
  - Today's loss is the net of realized PnL, commissions, and funding fees since 00:00 UTC (D-23): gains offset losses, and transfers and unrealized PnL don't count. The engine rebuilds it from income history at startup.
  - The limit is a configured fraction of the base; testnet starts at 3%, three stop-outs at D-23's 1%. When today's loss reaches it, the kill switch fires (D-27).
  - Entry gate: an entry's risk amount is the smaller of D-23's amount and the room left under the limit. If that leaves a quantity below `MIN_NOTIONAL`, the entry is skipped.
- **Why:** Fees and funding are real losses; counting realized PnL alone understates them. Unrealized PnL is left out because every open position's loss is already bounded by its exchange-side stop (D-19), and the gate counts that risk before the position opens, so the limit holds as long as the stops work; the kill switch backs it up against slippage and bugs. Counting unrealized PnL would need a mark price stream, couldn't be rebuilt after a restart (the unrealized PnL at 00:00 UTC is gone by then), and would fire the kill switch at nearly the moment a stop fires.
- **Caveat:** Stop slippage, mark-price trigger lag (D-18), and fees can still push a day's loss past the limit.
- **Alternatives:** A fixed USDT amount; a multiple of the risk amount; an equity-based loss that includes unrealized PnL.
- **Note (2026-10-16):** The counted income types also include `SPECIAL_FUNDING_FEE`, `INSURANCE_CLEAR` (liquidation clearance fee), and `POSITION_LIMIT_INCREASE_FEE`, all costs of trading. Rebates (`COMMISSION_REBATE`, `FEE_RETURN`, ...) are not counted, so they can't make room for more losses. A type missing from the docs' list is logged as a WARNING and not counted.
- **Limitation:** The room left doesn't count the risk of positions already open on other symbols. With one symbol (D-15) and one entry per position (D-22) there are none; this must change before a second symbol trades.
- **Date:** 2026-10-10

## D-25 Position cap 2× the base, leverage 5× capped at 10×, stop within half the liquidation distance

- **Decision:**
  - An entry's notional is capped at a configured multiple of the base (testnet: 2×) and at the available balance × leverage, so its initial margin fits. A trimmed entry risks less than the risk amount.
  - Leverage per symbol comes from configuration (D-16); testnet starts at 5×. A code constant caps it at 10× (D-28).
  - Before every entry, the stop must sit no farther from the entry price than half the distance to the estimated liquidation price; otherwise the entry is skipped with a WARNING.
- **Why:** D-22 sizing grows as the stop tightens: 1% risk with a 0.25% stop is a notional of 4× the base. With isolated margin and an exchange-side stop, leverage doesn't change the planned loss (quantity × stop distance). It sets two other things: the liquidation distance (about 1/leverage) and the margin, which is what a failed stop can lose. At 5×, liquidation sits about 20% away, leaving room for stops up to about 10%, and a full 2× position ties up about 40% of the base. Keeping the stop within half the liquidation distance leaves room for maintenance margin, fees, and mark/last price divergence.
- **Revisit:** The leverage, once the first strategy's typical stop distance is known.
- **Date:** 2026-10-10

## D-26 Emergency stop where the loss equals one risk amount

- **Decision:** A position found at startup without a stop (D-19) gets its emergency stop at entry price ∓ (risk amount ÷ position quantity), using D-23's risk amount. There is no separate distance setting. D-19's checks (already crossed, beyond liquidation, can't be placed → close the position) apply unchanged.
- **Why:** For a position the engine sized under D-22, this lands near its original stop. An oversized position gets a proportionally tighter stop, so its loss still stays at one risk amount, and if the price is already past that point the position is closed. A fixed percentage would let an oversized position lose several risk amounts; an ATR-based distance would need candle history and a timeframe in an emergency path.
- **Date:** 2026-10-10

## D-27 The kill switch halts trading until the user lifts it

- **Decision:**
  - When it fires (D-24), the engine cancels all orders, algo orders included, closes every position with `reduceOnly` market orders, and enters a halted state: it keeps running and serving queries and monitoring, but places no new orders.
  - The halt lasts until the user lifts it with an explicit command. Lifting it on the same UTC day changes nothing, because the entry gate (D-24) has no room left.
  - The halt survives restarts. At startup the engine rebuilds today's loss from the exchange and starts halted if it is at the limit. The kill switch also writes a local record that stays until the user lifts the halt, so the engine also starts halted on later days. If that record is lost, the engine resumes on the next UTC day.
  - Safety principle 7 now ends with "halt trading until the user lifts the halt" instead of "stop the engine".
- **Why:** The kill switch fires on a bad day or a bug; requiring the user to look before trading resumes keeps a bug from repeating every day. A running engine still shows positions and logs to the GUI and can receive the lift command; an exited one shows nothing, and nothing restarts it yet. Rebuilding from the exchange keeps a same-day restart halted even without the local record.
- **Alternatives:** Resume automatically at 00:00 UTC; exit the process.
- **Note:** The local record is a JSON file (D-29).
- **Note (2026-10-16):** Per symbol, the kill switch closes the position first and cancels orders (`DELETE /fapi/v1/allOpenOrders` and `/fapi/v1/algoOpenOrders`) only once it is flat, so the stop protects the position until it is gone. It retries a few times; a symbol it still can't flatten keeps its stop and gets a CRITICAL log asking for manual action. A halted engine at startup flattens every symbol again.
- **Date:** 2026-10-10

## D-28 Strategy and risk settings in `engine/config.toml`, bounded by code constants

- **Decision:** `.env` keeps secrets and environment settings (keys, network, log level). Strategy and risk settings — per-symbol leverage, the risk fraction, the daily loss limit, the position cap — go in `engine/config.toml`, read with the stdlib `tomllib` by `quantdeck_engine.config` at startup. The real file is gitignored; `engine/config.example.toml` is committed with the same keys. Code constants bound each risk value (risk fraction ≤ 2%, daily limit ≤ 10%, position cap ≤ 5× the base, leverage ≤ 10×); a missing, misspelled, or out-of-bounds value stops the engine. Margin type isn't a setting: it has one allowed value (D-16).
- **Why:** Per-symbol tables (D-15, D-16) are awkward as environment variables, and `tomllib` adds no dependency. Keeping the real file out of git keeps the user's values out of the public repo, as with `.env`. The bounds catch typos such as `1` for `0.01`, the same way an invalid `BINANCE_TESTNET` already stops the engine; raising one takes a code change.
- **Alternatives:** More `.env` variables; SQLite edited from the GUI (premature without a GUI).
- **Date:** 2026-10-10

## D-29 The kill-switch halt record is a JSON file in `engine/state/`

- **Decision:**
  - D-27's local record is `engine/state/halt.json` (gitignored), located from the package like `engine/logs/` and `engine/config.toml`, so it doesn't depend on where the engine is started. It holds when the kill switch fired and why.
  - The file exists only while halted: the kill switch writes it, and the user's lift command deletes it. The lift is logged.
  - Writes go to a temporary file first, then `os.replace` swaps it in, so a crash never leaves a half-written record.
  - A file that exists but can't be read counts as halted, and the engine logs an ERROR.
  - If the write fails (on Windows, `os.replace` can fail while another program has the file open, as in D-12), the kill switch still closes positions and cancels orders, the running engine stays halted in memory, and it logs CRITICAL.
- **Why:** The record is one small piece of state, and SQLite's contents and schema are still undecided (D-04); tying the kill switch to them would pull that whole design into the risk-management work. Losing the file is tolerable: a same-day restart is halted anyway because today's loss is rebuilt from the exchange (D-27), so the file only carries the halt into later days. Treating an unreadable file as halted and never letting a failed write block the close keep both failures on the safe side.
- **Alternatives:** A SQLite table — reconsider when SQLite is introduced; moving the record means changing only its read and write functions.
- **Date:** 2026-10-16

## D-30 Today's loss is re-read from income history whenever it is needed

- **Decision:** The engine never accumulates today's loss from stream events. Each time it needs the number, it reads income history since 00:00 UTC and recomputes it: before every entry (the gate), and in a check that fires the kill switch. The trading loop is to run that check after a position closes, after an `ACCOUNT_UPDATE` with reason `FUNDING_FEE`, after every stream (re)connect, and about once a minute.
- **Why:** The exchange stays the only source (D-06): no events missed while the stream is disconnected, no double counting when a resync replays events, no separate handling for commission assets or funding events. The cost is weight 30 per read (one page holds 1000 records), small next to the 2400 per minute limit at a few reads a minute.
- **Trade-off:** If income records appear some time after a fill, a check right after a stop-out can miss it; the periodic check catches it. The trial measures the delay: 1.1 s on testnet (2026-10-16, one sample, `binanceNotes.md`), so the periodic check backs up the post-close check rather than being its main path.
- **Alternatives:** Add `rp` and `n` from `ORDER_TRADE_UPDATE` and funding from `ACCOUNT_UPDATE` as events arrive, with income history only at startup and after reconnects.
- **Date:** 2026-10-16

## D-31 `quantdeck-engine run` is the long-running engine, built before any strategy

- **Decision:**
  - `quantdeck-engine run` is the engine process. It runs the startup checks for each symbol (D-16, D-19), follows the user data stream with resyncs (D-13), runs the risk guard's checks at the points D-30 lists, and serves the WebSocket clients (D-32). It is built with the engine ↔ GUI connection, before a strategy exists, so it places no entries yet; the strategy later adds its entry logic to this loop.
  - The user starts it by hand from a terminal for now. Running it as a service and restarting it after a crash are still open.
  - Only one `run` process may run at a time: a second one refuses to start.
- **Why:** The WebSocket server has to live in a long-running process, and a GUI built against a stub would meet the real engine only later. Building the loop now also puts D-30's checks in place while positions can still exist (an adopted position, a manual test). Two engine processes on one account would each act on the same positions and orders (D-14).
- **Note (2026-10-16, implementation):**
  - The single-process rule is an OS file lock on `engine/state/engine.lock`, released by the OS even after a crash. `trial` takes the same lock.
  - `run` refuses mainnet until the mainnet prerequisites are settled (activeContext.md).
  - The engine re-measures the clock offset hourly, since signed timestamps must stay within `recvWindow` for days.
  - Event-triggered risk checks (a position closed, `FUNDING_FEE`, a reconnect) run about 3 s after the event, because income records appeared 1.1 s after a fill (D-30). Requests close together share one check.
- **Date:** 2026-10-16

## D-32 Clients connect to `127.0.0.1` with a per-start token; the GUI connects from its main process

- **Decision:**
  - The engine's WebSocket server listens on `127.0.0.1` on a port the OS assigns. At each start the engine writes the port and a new random token to a connection file in `engine/state/` (gitignored, written like the halt record in D-29), and deletes it on a clean shutdown. A client reads the file, connects, and sends the token in its first message; a wrong or missing token closes the connection.
  - Connections that carry an `Origin` header are refused. Browsers always send one; the GUI's main process and the bot don't.
  - In the GUI, the Electron main process holds the connection and relays state, logs, and commands to the renderer over IPC through the preload script. The renderer never sees the token or the connection file.
- **Why:** Any web page open in the user's browser can open a WebSocket to `127.0.0.1`, so listening only on localhost isn't enough. The token is in a file rather than `.env`, so the GUI never reads the file that holds the API keys (D-08). A new token and port on every start mean a token left over from an old run is useless, and there's no port to configure or collide. The renderer shows web content, so keeping the token out of it limits what a renderer bug could do. The token goes in the first message because Node's built-in `WebSocket` can't set request headers, and a query string can end up in logs.
- **Alternatives:** A fixed port and token in `.env`; the renderer connecting directly.
- **Date:** 2026-10-16

## D-33 The engine sends the whole state on every change; GUI commands are the kill switch and lifting the halt

- **Decision:**
  - Messages are JSON objects with a `type`. Prices, quantities, balances, and PnL are strings, as Binance sends them, so no client parses them as floats.
  - After a client authenticates, the engine sends the whole state, then sends the whole state again whenever it changes, with changes close together sent once. The state includes positions, open orders and stops, balances, today's loss and limit, the halt, the network, and the mark price and unrealized PnL per position. The mark price comes from a mark price stream used only for display; risk checks don't use it (D-24).
  - Log records at INFO and above go to clients as they happen, and a client gets the most recent ones (about 200, kept in memory) when it connects.
  - Commands carry an `id` that the engine echoes in its result. Two commands for now:
    - Emergency close fires the kill switch (D-27), with a reason saying the user fired it. There is no separate "close and keep trading" command.
    - Lifting the halt does what `quantdeck-engine lift-halt` does and also lifts the halt in the running engine at once. The CLI command stays. On the same UTC day the entry gate still blocks entries while today's loss is at the limit (D-27).
- **Why:** With one symbol the state is small. Sending all of it means a client never rebuilds state from deltas, so reconnects and missed messages can't leave it out of step, the same reasoning as resyncing from the exchange (D-06, D-13). Keeping recent logs in memory lets the GUI show context without deciding whether logs go in SQLite (D-04, D-12). One emergency command that also halts avoids a second close path with its own rules.
- **Alternatives:** An initial snapshot followed by delta events; pausing and resuming entries (no effect without a strategy, so it waits for one).
- **Date:** 2026-10-16

