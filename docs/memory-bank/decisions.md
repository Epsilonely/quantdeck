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
- **Note (2026-10-18):** Parquet for historical candles is deferred by D-38: candles stay in Binance's downloaded zip files until the data outgrows them. The SQLite part stands.

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
- **Note (2026-10-18):** The interface the two share is D-36; the backtest also reuses the live sizing and risk code (D-37).

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
- **Note (2026-10-16):** At the user's request, log timestamps, file names, and the date rollover use the machine's local time zone (KST on the development machine), and every timestamp carries its UTC offset, e.g. `2026-10-16T16:41:50.908+09:00`. The user reads logs in Korean time, Binance's web pages show local time, and the API's times are epoch milliseconds with no zone. Files written before the change are named and stamped in UTC. The risk day stays at 00:00 UTC (D-23), and the client protocol keeps UTC times (D-33); the GUI shows them in local time.
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
- **Note (2026-10-20):** Symbols are now added and removed while the engine runs (D-47). The entry gate counts every open position (D-24 note of 2026-10-19).

## D-16 Isolated margin, applied by the engine at startup

- **Decision:** Positions use isolated margin, a code constant. Leverage per symbol comes from engine configuration. At startup the engine reads the account: if the symbol has no position, it applies the configured values; if a position exists under different settings, it refuses to trade and warns. The leverage values and the liquidation check are set in D-25. `check` stays read-only.
- **Why:** The biggest risk in automated trading is a bug such as a missing stop or a wrong quantity. Isolated margin caps that loss at the position's margin; cross margin would put the whole balance at risk. Keeping the values in configuration means the account can't silently drift from what the engine assumes.
- **Consequence:** Liquidation sits closer to entry than with cross margin, so before every entry the engine checks that the stop price is safely inside the liquidation price.
- **Date:** 2026-10-03

## D-17 Market orders for entries and strategy exits, to start

- **Decision:** Entries and strategy-signal exits use market orders (exits with `reduceOnly`). Limit orders are added when costs or a strategy call for them, so the order code keeps the order type open for extension. Stop-loss orders are decided separately.
- **Why:** A market order fills at once, which keeps the entry → exchange-side stop → exit flow simple while it is being proven. The cost is taker fees and unbounded slippage in fast markets.
- **Note (2026-10-19):** The backtest can fill limit entries (D-45). The engine still places market entries only and refuses a strategy that uses limit entries.
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
- **Settled (2026-10-18):** The strategy manages an adopted position like any other position on its symbol (D-36).
- **Date:** 2026-10-03

## D-20 Unknown order outcomes are resolved by lookup, never by blind retry

- **Decision:** When an order request times out, fails at the network level, or gets a 5xx, the engine waits briefly and looks the order up by its client ID with a REST query. Found: carry on with it. Not found: resend once with the same client ID. Still unclear: stop trading that symbol and resync positions and orders from REST, which brings D-19 into play for any position left without a stop. For a stop, D-19's check of the open algo orders does this job, because the docs give no "not found" code for algo orders.
- **Why:** Binance enforces client-ID uniqueness only among open orders, and a market order closes the moment it fills, so blindly resending an entry can fill it twice. A 5xx means the outcome is unknown, not that the order failed.
- **Date:** 2026-10-03

## D-21 Client order IDs encode the order's role

- **Decision:** Client order IDs and `clientAlgoId`s follow `qd_<role>_<symbol>_<ms timestamp>_<seq>`, with roles `e` entry, `x` exit (strategy exits, and closes after a failed stop), `s` stop, `k` kill switch — e.g. `qd_s_BTCUSDT_1759480000123_01` (29 of the 36 allowed characters). A retry reuses the original ID.
- **Why:** After a restart the engine can tell what each open order is for from the exchange alone (D-06), logs read clearly, and reusing the ID on retry is what makes D-20's lookup possible. Random IDs would need a local record to recover each order's role.
- **Date:** 2026-10-03
- **Note (2026-10-22):** The user's close of one symbol (D-50) uses `x` too.

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
- **Note (2026-10-16):** The logs now use local time (D-12 note), so the reason "UTC matches the logs" no longer holds; the day still starts at 00:00 UTC, shown as 09:00 in the logs and the GUI.
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
- **Note (2026-10-19, GitHub issue #13):** The room left also subtracts what every open position on the account loses if its stop fires: the loss to the stop that fires first, plus the taker fee to close there (0.05%). Positions and stops are read from REST at each entry (D-06, D-30), so a position counts whichever symbol it is on and whichever trader opened it (D-14). A position without a stop blocks entries until its emergency stop is placed (D-19), since its loss has no bound. Entries on all symbols take turns: each holds one account-wide slot from the gate until its stop is placed, so two entries at the same candle close can't both count on the same room. Assuming every stop fires together, coins that move together can't push the day past the limit either; the caveat above still applies. This replaces the original limitation, under which the room ignored positions on other symbols; harmless with one symbol and one entry per position (D-15, D-22), but it had to go before a second symbol trades.
- **Note (2026-10-22, GitHub issue #25):** At the user's request, the GUI's today's loss card also shows, on a smaller line, every open position's unrealized PnL and the loss with it counted, so the distance to the limit can be seen while positions are open. Display only, as the user agreed: the limit, the entry gate, and the kill switch still count realized results, for the reasons above. The engine computes both from the display-only mark prices (D-33) and sends none until every position's price has arrived, since a partial total would mislead. Today's loss is re-read a few seconds after a position closes (D-30, D-31 note), so for those seconds the closed position's unrealized part is gone while its realized result hasn't arrived yet.
- **Date:** 2026-10-10

## D-25 Position cap 2× the base, leverage 5× capped at 10×, stop within half the liquidation distance

- **Decision:**
  - An entry's notional is capped at a configured multiple of the base (testnet: 2×) and at the available balance × leverage, so its initial margin fits. A trimmed entry risks less than the risk amount.
  - Leverage per symbol comes from configuration (D-16); testnet starts at 5×. A code constant caps it at 10× (D-28).
  - Before every entry, the stop must sit no farther from the entry price than half the distance to the estimated liquidation price; otherwise the entry is skipped with a WARNING.
- **Why:** D-22 sizing grows as the stop tightens: 1% risk with a 0.25% stop is a notional of 4× the base. With isolated margin and an exchange-side stop, leverage doesn't change the planned loss (quantity × stop distance). It sets two other things: the liquidation distance (about 1/leverage) and the margin, which is what a failed stop can lose. At 5×, liquidation sits about 20% away, leaving room for stops up to about 10%, and a full 2× position ties up about 40% of the base. Keeping the stop within half the liquidation distance leaves room for maintenance margin, fees, and mark/last price divergence.
- **Revisit:** The leverage, once the first strategy's typical stop distance is known.
- **Note (2026-10-19):** Revisited with the first strategy: testnet leverage is now 2× (D-41). Its stops are often wider than 5× allows, and the reasoning above (leverage sets the liquidation distance and the margin, not the planned loss) is unchanged.
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
- **Note (2026-10-20):** Per-symbol leverage and the strategy moved to the engine-owned `engine/state/symbols.json` (D-47), checked against the same bounds. `config.toml` keeps the risk values; its `[symbols]` and `[strategy]` only start that file.

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
- **Note (2026-10-16):** The GUI now starts `run` when none is running and stops it with a command (D-34). Running it without the GUI and restarting it after a crash are still open.
- **Note (2026-10-19):** The strategy runs in `run` when `config.toml` names one (D-43).
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
- **Note (2026-10-16, implementation):**
  - The engine builds the state every 0.25 s and sends it when it differs from the last one sent, so no change source needs its own hook. With the mark price updating each second, a client gets about one state message a second.
  - Lifting the halt is refused while today's loss is still at the limit: the next risk check would fire the kill switch again within a minute. The CLI `lift-halt` has no exchange access and doesn't check.
  - The client server's failures are logged and don't stop the engine; it keeps protecting positions without clients.
  - Message shapes are in `techContext.md`.
- **Note (2026-10-16):** A third command, `stop_engine`, was added with D-34.
- **Note (2026-10-20):** Commands may carry an `args` object; `set_symbol` and `remove_symbol` came with D-47. Clients get each symbol's settings in the state.
- **Note (2026-10-20):** `welcome` also carries the catalog of what `set_symbol` accepts (D-48).
- **Note (2026-10-22):** Pausing entries came per symbol with D-50 (`pause_symbol`, `close_symbol`, `resume_symbol`); there is still no account-wide pause.
- **Date:** 2026-10-16

## D-34 The GUI starts the engine when none is running and can stop it

- **Decision:**
  - When the GUI starts, it tries to connect (D-32). If no engine answers, it starts one: the virtualenv's `pythonw` runs `python -m quantdeck_engine run` in `engine/`, hidden and detached, with no stdin, stdout, or stderr. If an engine is running, the GUI leaves it as it is. While disconnected, the screen also offers a start button.
  - That first attempt is the only time the GUI starts the engine by itself. If the engine stops or crashes later, the user starts it again with the button. When an engine the GUI started exits with a failure, the GUI shows the exit code and the last ERROR or CRITICAL line of the engine's log file.
  - A new client command, `stop_engine`, stops the engine as Ctrl+C does, once any kill switch in progress has finished. The GUI asks for confirmation first. While the engine is stopped, open positions keep their exchange-side stops, but nothing watches the daily loss limit.
  - Closing the GUI never stops the engine (D-01).
- **Why:** The user wanted opening the GUI to be enough to run the system. Starting only on the first attempt keeps an engine that crashes at startup from being restarted in a loop against Binance. `pythonw` has no console: when a detached `uv run` started Python, Windows opened a new Windows Terminal window for it (2026-10-16). A hidden engine can't receive Ctrl+C, so it needs a stop command. The instance lock (D-31) refuses a second engine, so a race between the GUI's start and another start is harmless; the loser exits, and the GUI shows why.
- **Trade-offs:** Starting with the venv's `pythonw` skips `uv run`'s sync, so the venv must be kept up to date with `uv sync`. Output that never reaches the log file, such as an import error before logging starts, is lost.
- **Alternatives:** A visible console window stopped with Ctrl+C; the user chose the hidden engine with a stop button. A Windows service or scheduled task, still open for running without the GUI.
- **Note (2026-10-19, GitHub issue #12):** The GUI now starts the engine through a short-lived starter (Electron run as Node) that launches it, reports its pid, and exits, so the engine never has a living parent in the GUI's process tree. Closing the terminal that runs `npm run dev` had ended the engine with the rest of that tree (`techContext.md`, GUI notes). The cost: the GUI is no longer the engine's parent and can't read its exit code. It checks the pid every second instead, and tells a stop on request from a failure by the engine's log (the stop line, or the latest ERROR or CRITICAL line).
- **Date:** 2026-10-16

## D-35 GUI theme: system, light, or dark, applied by the main process through `nativeTheme`

- **Decision:**
  - The GUI offers three themes: system (follows the Windows app mode), light, and dark. The default is system.
  - The control is a toggle in the header with icons instead of text, at the user's request: a monitor, a sun, and a moon. The selection slides between them, and the page's colors fade for 250 ms during a switch. Both animations are off when Windows asks for reduced motion (`prefers-reduced-motion`).
  - The main process saves the choice in Electron's userData folder and applies it with `nativeTheme.themeSource`. The renderer only picks colors from `prefers-color-scheme` in CSS and calls `window.api.theme` to read or change the choice.
- **Why:** The user chose three options with system as the default. With `nativeTheme` one setting drives both the page and the Windows title bar, and system mode follows a change of the Windows app mode without code in the renderer. Saving in the main process keeps one setting for dev and packaged builds, whose renderer origins (`localhost` and `file://`) would each have their own `localStorage`. The theme is a GUI preference, so it stays out of the engine's files.
- **Alternatives:** Light and dark only; dark by default; text labels (the first version); renderer `localStorage` with a `data-theme` attribute, which leaves the title bar on the Windows setting.
- **Date:** 2026-10-16

## D-36 A strategy sees closed candles and the exchange's position, and returns an entry with a stop, an exit, or nothing

- **Decision:**
  - A strategy is plain synchronous code with no I/O. For each closed candle of its symbol and timeframe it gets the candle and the current position (none, or its side, size, and entry price), and returns one of: enter (side and stop price), exit (close the whole position), or nothing. Live, the engine carries these out through the existing entry and close paths (D-17, D-18, D-22); in the backtest, the simulator fills them (D-07, D-37).
  - Only closed candles are passed in, never a forming one.
  - Its decisions depend only on recent candles and the position. Any state it keeps, such as indicators, must be rebuildable from candles: each strategy declares how many past candles it needs, and the engine loads that many from REST at startup before acting on its output.
  - So it manages every position on its symbol, including one adopted at startup (D-14, D-19).
  - The stop is set once at entry and never moved. No take profit or trailing stop yet. At most one action per candle: an exit and an opposite entry never happen on the same candle.
- **Why:** The backtest must run the same code (D-07). With closed candles as the only input, live and backtest runs see identical inputs, so a forward test can be checked by replaying its candles through the backtest and comparing the signals. Deciding from candles and the exchange's position means a restart (the GUI stops and starts the engine, D-34) doesn't change a decision, in line with D-06, and settles D-19's open question without a local record of which positions are the strategy's. Order handling, sizing, and risk stay outside the strategy, so the safety code lives in one place.
- **Consequences:** Strategies that act inside a forming candle (an intrabar breakout such as a volatility breakout) or need to remember when they entered (exit after N candles) don't fit; revisit when one is wanted. A missed closed candle changes the signals, so the live kline feed must backfill the gap after every reconnect before a strategy trades.
- **Settled (2026-10-19):** The closed-candle feed backfills gaps after every reconnect and holds candles back while it can't (D-42).
- **Note (2026-10-19):** An entry may carry a limit price, valid for the next candle only, so the strategy still never tracks a pending order (D-45).
- **Alternatives:** Strategies driven by ticks or forming candles; strategies that keep their own record of their positions.
- **Date:** 2026-10-18

## D-37 The backtest reuses the live sizing and risk rules

- **Decision:**
  - The backtest sizes entries with the live code (D-22, D-25): the risk amount from the day's base balance, `stepSize` rounding, `MIN_NOTIONAL`, the notional caps, and the liquidation-distance check. It also applies the daily loss limit and the entry gate (D-23, D-24).
  - When the simulated day's loss reaches the limit, the backtest closes the position and takes no entries for the rest of that UTC day, then resumes the next UTC day as if the user had lifted the halt (D-27). The report counts how often this happened.
- **Why:** The results then include what the live engine would really skip or trim, such as entries below `MIN_NOTIONAL` or entries capped by the room left under the limit. How often the limit fires is one of the criteria for choosing a strategy (D-39). Live, a halt waits for the user; a backtest has no user, so it lifts at the next day boundary, the earliest a lift changes anything (D-27).
- **Note:** The rest of the fill model is D-40.
- **Date:** 2026-10-18

## D-38 Backtest candles come from Binance's mainnet archive and REST, kept as the downloaded zip files

- **Decision:**
  - Backtests run on mainnet data: monthly zips from data.binance.vision (`futures/um`), daily zips for the current month, and REST `GET /fapi/v1/klines` for the days not yet archived. Every zip is checked against its `.CHECKSUM`, and loaded candles are checked for gaps and duplicates. Mark price klines and funding rates come from the same archive when the fill model needs them.
  - To check a forward test (D-36), the backtest replays the testnet candles the engine actually saw, fetched from testnet REST, not mainnet data.
  - The zip files are kept as downloaded in `engine/data/` (gitignored) and read directly, with no conversion step. Parquet (D-04) waits until the data outgrows this, such as years of 1m candles or trade data.
- **Why:** Testnet prices and volume differ from mainnet's, and there is no testnet archive (`binanceNotes.md`), so mainnet data is the closest to the market a strategy would eventually trade. The archive is free, needs no key, and comes with checksums; REST fills in the days not archived yet. Keeping the original files means they can always be re-verified against Binance's checksums. Seven years of BTCUSDT 1h candles is about 60,000 rows, small enough to read from the zips on every run, so Parquet would add a large dependency (pyarrow or similar) and a conversion step for no gain yet.
- **Alternatives:** Parquet from the start (D-04); REST only (slow paging, rate limits, no checksums); testnet data.
- **Date:** 2026-10-18

## D-39 The first strategy is picked by backtest

- **Decision:** The candidates are a Donchian channel breakout (trend following) and a Bollinger Band mean reversion, both on closed candles (D-36), tested on BTCUSDT first (D-15). Both run through the backtest with costs, and the first strategy is the one that does better on: expectancy per trade after fees, slippage, and funding; maximum drawdown and longest losing streak; and how often the daily loss limit would have fired (D-37). If neither holds up, the candidates change; nothing goes to the forward test on a guess. The chosen strategy is then run on two or three other major coins (ETHUSDT and the like) with the BTCUSDT parameters unchanged; parameters are not tuned per coin.
- **Why:** Which strategy is profitable can't be known before measuring it. Win rate alone doesn't decide it: a trend follower loses often and wins big, a mean reverter wins often and loses big, and either can end up ahead or behind after costs. The two cover both styles, have few parameters, and derive their stop price from their own rules (D-22). The daily limit is a small number of stop-outs (three on testnet, D-24), so long losing streaks mean halts, which is why they are a criterion. A strategy that works only on the coin it was chosen on has likely been fitted to that coin's history; tuning parameters per coin would make that more likely, not less. BTCUSDT comes first for its liquidity (backtest fills closest to real ones) and its archive back to 2020. Trading several coins at once is a separate step (D-15, D-24 note of 2026-10-19).
- **Not a candidate for now:** A volatility breakout, which enters inside a forming candle and exits on time; it doesn't fit D-36.
- **Date:** 2026-10-18
- **Note (2026-10-19):** Both first candidates lost on BTCUSDT 1h after costs, so the candidates changed as this entry says; the choice is D-41.

## D-40 Backtest fill model: next open with slippage, mark-price stops, taker fees, archived funding

- **Decision:**
  - A strategy's entry or exit fills at the next candle's open, moved against the trade by a market slippage (default 0.02%). Entries are sized at that candle's mark price open.
  - Stops trigger when a candle's mark price range reaches them (D-18). They fill at the stop, or at the candle's last price open if the mark price opened past the stop, moved against the trade by a stop slippage (default 0.05%). A fill past the estimated liquidation price is cut at that price.
  - Every fill pays the taker fee (default 0.05%, the regular tier).
  - Funding from the archive settles while a position is open: quantity × mark price × rate. A funding at a candle's open time comes before that candle's fills.
  - Symbol rules and leverage brackets are the configured network's current values (testnet by default); risk settings come from `engine/config.toml` (D-37). The starting balance defaults to 5000 USDT, the period to 2020-01-01 through yesterday, and the report breaks results down by year.
  - All costs are command-line options.
- **Why:** The live engine sends its market order right after a candle closes (D-17, D-36), so the next open is where it fills, and funding at that instant settles first. Triggering on the mark price matches the live stop; mark price candles are in the archive (D-38). Slippage and fees are set on the pessimistic side of a small BTCUSDT order, since an edge that only survives optimistic costs isn't one. The testnet rules and brackets match the forward test's conditions.
- **Limitations:** With hourly candles, where the last price traded when a mark price stop fired is unknown, so the stop price stands in for it. Candles longer than the funding interval settle a funding inside a candle at its open. Current rules and brackets are applied to all of history.
- **Note (2026-10-19):** Limit entries have their own fill model, with the maker fee (D-45). Everything above still applies to market entries, exits, and stops.
- **Date:** 2026-10-19

## D-41 First strategy: Donchian breakout 55/20 on BTCUSDT 4h, at 2× leverage on testnet

- **Decision:** The first strategy for the testnet forward test is the Donchian breakout with entry period 55 and exit period 20 on 4h candles, trading BTCUSDT. Testnet leverage goes from 5× to 2× (D-25). The Bollinger mean reversion is dropped.
- **How it was chosen (D-39), all with D-40 costs and the testnet risk values:**
  - On BTCUSDT 1h with default parameters, both first candidates lost: Donchian 20/10 −27.0% (−0.010 R per trade), Bollinger −99.5% (−0.321 R). Donchian was ahead before costs; Bollinger lost even before them.
  - A grid fixed in advance, Donchian {1h, 4h} × {20/10, 55/20}, was chosen on 2020–2023 and checked on 2024-01-01 to 2026-10-17. 1h was near zero or negative in both periods. On 2020–2023, 4h 55/20 led 4h 20/10 on expectancy (+0.455 vs +0.442 R), drawdown (10.4% vs 11.1%), and losing streak (5 vs 9). On the holdout it stayed positive but weaker: +9.2%, +0.141 R over 68 trades (t ≈ 0.8).
  - The same parameters on other coins, full history through 2026-10-17, at 2×: ETHUSDT +0.080 R (179 trades), XRPUSDT +0.201 R (159), SOLUSDT +0.289 R (151); BTCUSDT +0.199 R (172). Max drawdowns 8–14%. The daily limit never fired in any run.
  - At 5×, the D-25 check skipped more entry signals than were traded on the altcoins (ETH 326, XRP 240, SOL 509) and 106 on BTC; a breakout repeats its signal each candle while flat, so these count signals, not distinct trades. The 20-candle exit channel is often more than the ~10% from entry that 5× allows. At 2× BTC skipped none, with similar results, so the 5× results came from a filtered subset of the strategy's trades.
- **Why:** It was positive in the selection period, the holdout, and on three other coins with unchanged parameters, so the result isn't fitted to one coin's history. The forward test on testnet costs nothing, and its job is to show the live engine trades as the backtest does (D-36). Lower leverage doesn't change the planned loss per trade (D-25); it moves the liquidation price to about 50% away and ties up more margin, which is fine with one symbol.
- **Limits of the evidence:** Each result alone is not statistically clear (t between about 0.8 and 2), coins move together so the four don't count as four independent tests, and the holdout expectancy was a third of the selection period's. That is enough for a forward test, not for mainnet; go-live criteria are still open. Funding cost more than fees in most 4h runs, since positions are held for days.
- **Date:** 2026-10-19
- **Note (2026-10-21):** The selection and holdout figures above for BTCUSDT (+0.455 R, and +0.141 R over 68 trades) were run at 5×, where the liquidation-distance check skipped 89 and 17 entry signals. At the 2× the strategy trades, BTCUSDT 55/20 gives +0.325 R over 96 trades (+33.8%) on 2020–2023 and +0.068 R over 74 trades (+4.5%) on 2024-01-01 to 2026-10-17, with no skips; that is the expectancy to hold the forward test against.
- **Note (2026-10-21), robustness grid:** fixed before the runs, 4h with entry {20, 30, 40, 55, 70, 85, 100} × exit {10, 15, 20, 30, 40} (exit ≤ entry, 32 settings) on BTCUSDT, ETHUSDT, SOLUSDT, and XRPUSDT at 2×, with D-40 costs (SOLUSDT and XRPUSDT at 0.04% slippage and 0.10% on stops). On 2020–2023 every setting was positive on every coin (BTCUSDT +0.23 to +0.64 R). On 2024 to 2026-10-20 (seen before), 27, 32, 30, and 32 of the 32 were positive, but smaller: around 55/20 on BTCUSDT +0.01 to +0.08 R. So 55/20 sits in a broad positive region rather than on a lucky peak, and the parameters stay. The longer exit (40) did better on BTCUSDT and ETHUSDT in the seen holdout, which is no reason to switch. Win rates were 27–53%; BTCUSDT 55/20 on 2020–2023 won 37.5% of trades, averaging +1.82 R on wins and −0.57 R on losses, since most exits come on the 20-candle channel before the stop. The period held strong trends (2020–21 rally, 2022 crash), which favors any trend follower.

## D-42 The strategy reads closed candles through a feed that fills gaps from REST and waits rather than skip

- **Decision:**
  - At startup the feed loads the strategy's warm-up candles from REST. After every stream (re)connect, when a closed candle arrives after a gap, and when the stream hasn't sent the next close by the time REST's copy can be trusted, it fills the missing candles from REST before handing out anything newer.
  - REST's copy of a candle is used only once the candle closed at least a minute ago. The stream's closed message is used at once.
  - While REST fails, the feed retries every 5 s and hands out nothing, so the strategy waits instead of deciding on a series with a hole in it. Open positions keep their exchange-side stops meanwhile (D-19).
  - A candle REST doesn't have either is treated as a real gap once a later closed candle exists: logged and passed over, as the backtest's data loader does (D-38).
- **Why:** The strategy decides on closed candles only (D-36), so one lost candle makes it act differently from the backtest. The stream loses whatever is sent while it reconnects, and Binance drops every connection within 24 hours. On testnet REST still showed a smaller volume, once a different close, for 10 s or more after a close the stream had already sent in full (`binanceNotes.md`). A minute leaves room for that; the cost is a candle up to a minute late after a reconnect near a close, nothing for a 4h strategy. The user chose waiting over deciding with a gap.
- **Alternatives:** Keep deciding through a gap with a warning; trust REST right after the close.
- **Date:** 2026-10-19

## D-43 `run` trades one strategy from `config.toml`, acts on the latest closed candle at startup, and records every decision

- **Decision:**
  - **Settings:** one top-level `[strategy]` table in `engine/config.toml` (`name`, `interval`, and `[strategy.params]`) applies to every configured symbol, each with its own instance. Without the table, `run` places no entries, as before. The name must be in the strategy registry, the interval a kline stream interval, and the parameters pass the same checks as in the backtest; the warm-up has a code bound. Anything else stops the engine at startup (D-28).
  - **Warm-up:** at startup the engine loads the strategy's `warmup` closed candles from REST (D-42), feeds them to it, and carries out the signal of the most recent one. Later candles come from the closed-candle feed.
  - **Each candle:** the engine reads the position from REST (D-06), retrying while REST fails as the feed does (D-42), passes the candle and the position to the strategy, and carries out an entry or exit through the live paths (D-17, D-18, D-22).
  - **Record:** every candle the strategy decided on appends a row to `engine/data/forward/<SYMBOL>_<interval>.csv`: the candle, the position it saw, the action and stop price, and the outcome. A failed write logs a WARNING and changes nothing else.
  - **Clients:** the state gains a top-level `strategy` (name, interval, parameters) and, per symbol, the last candle decided on, the last signal with its outcome, and why the strategy stopped, if it did. The protocol version stays 1. The GUI shows the strategy in the header and two columns in the symbol table.
  - **Failures:** while the account or the symbol is halted (`TradingHalted`; D-16, D-19, D-20, D-27), an entry signal is logged and skipped; exits always run. A request error or an unclear order outcome (D-20) is logged as an ERROR, and the strategy goes on with the next candle. Any other error stops that symbol's strategy with a CRITICAL log; the engine keeps running, so the kill switch, monitoring, and exchange-side stops carry on.
- **Why:**
  - One table for every symbol: D-39 runs the chosen parameters unchanged on every coin, and per-symbol settings would invite tuning each one.
  - Acting on the latest closed candle at startup: the GUI stops and starts the engine (D-34), and an exit signal on a candle that closed while it was down would otherwise be lost, leaving open a position the backtest would have closed. The strategy decides from candles and the exchange's position only (D-36), so the restart gives the same decision.
  - Retrying the position read instead of skipping the candle: the strategy's indicators need every candle (D-42), and deciding without the position could miss an exit.
  - The record is what a forward test is checked against: replaying its candles through the backtest must give the same signals (D-36, D-38). It sits with the downloaded data in `engine/data/` (gitignored). It is a record, not state: nothing reads it back (D-06).
  - A request error is usually transient, and the next candle re-reads the position, so a failed order costs one candle. Any other error is a bug; stopping only the strategy keeps the protection running, whereas a stopped engine no longer watches the daily loss limit (D-34).
- **Limitations:** A restart inside a candle repeats that candle's decision and records it twice; if a stop closed that candle's entry before the restart, the repeat enters again, which the backtest wouldn't. An entry carried out at startup fills at the current price, not at the candle's next open (D-40).
- **Alternatives:** per-symbol strategy tables; ignoring the last warm-up candle's signal (loses exits across restarts); stopping the engine on a strategy error.
- **Date:** 2026-10-19
- **Note (2026-10-20):** Superseded in part by D-47: each symbol has its own strategy, kept in `symbols.json` and changed by clients while the engine runs, and the record is named after the strategy setting. The warm-up, deciding at startup, the record's contents, and the failure handling stand.

## D-44 15m candidates: the move fade is dropped; the daily-open breakout passed its holdout only nominally and stays backtest-only

- **Decision:** Of the two 15m candidates (GitHub issue #15), the fade of large 1h/2h moves is dropped. The daily-open breakout, long only with k = 0.8, passed the checks fixed before the runs, but it isn't forward-tested: its holdout edge can't be told from zero. The 4h Donchian (D-41) stays the forward test's strategy. Both new strategies stay in the registry for backtests, as the Bollinger reversion does.
- **The strategies (D-36 interface):**
  - Daily-open breakout: O is the 00:00 UTC open and R the previous UTC day's high minus low. The day's first candle to close at or beyond O ± k·R enters that way, with the stop at O, and the position closes on the day's last candle. A day takes at most one entry.
  - Move fade: 1h or 2h bars built from the 15m candles on UTC boundaries. A bar return of at least k·σ against the previous 120 bars' returns enters the other way, with a stop that allows the same move again, and the position closes when the next bar closes.
  - Both exit on clock time read from the candle, never on time since entry, so they fit D-36.
- **How they were tested** (D-40 costs, with SOLUSDT and XRPUSDT at 0.04% slippage and 0.10% on stops; 2× leverage; testnet risk values; the grid and pass rules were fixed in the issue before any run):
  - **Baselines,** BTCUSDT 15m, 2020–2023, after costs (before costs in brackets):
    - Donchian 20/10: −0.153 R per trade (+0.007 R)
    - Donchian 55/20: −0.081 R (+0.014 R)
    - Bollinger: −1.008 R (+0.023 R)

    A 15m decision with a tight stop can't carry about 0.1–0.16 R of costs per trade.
  - **Selection,** 2020–2023 (SOLUSDT from 2020-10) on BTC, ETH, SOL, and XRP, with 7 settings. A setting passed with positive expectancy on BTC and on two of the other three coins, and at least 30 trades on each.
    - The move fade lost on every coin in all three settings, −0.03 to −0.11 R.
    - The daily breakout passed in three of its four settings. Long only with k = 0.8 had the best mean, +0.103 R: BTC +0.143, ETH +0.175, SOL +0.068, XRP +0.025, with 239–300 trades each.
    - Much of that came from 2020. On BTC it made +34% that year and +2–6% in each later year.
  - **Holdout,** 2024-01-01 to 2026-10-18, long only with k = 0.8:
    - BTC +0.011 R (228 trades, t ≈ 0.2), ETH +0.050 R (t ≈ 0.7), SOL +0.023 R (t ≈ 0.4), XRP −0.007 R.
    - That is positive on three of four coins, the rule for passing, but every t is under 0.7, and 2025 lost on BTC, ETH, and SOL.
    - Stops were a median 2–3% from entry, so fees took about 0.04–0.06 R per trade. Max drawdowns were 11–17%, and the daily limit never fired.
- **Why:** The holdout expectancy fell to about a tenth of the selection period's, and the selection result leaned on one bull year in a long-only setting. Passing a rule set before the run is reason to keep the strategy, not to put it on the account in place of the 4h strategy, whose holdout was clearer (+0.141 R over 68 trades, D-41). One strategy trades per account (D-43), so forward-testing it would mean pausing the 4h test.
- **Revisit:** Once per-coin strategies exist, or after more out-of-sample data. Maker (limit) entries would cut its costs, but they need a fill model first (D-17, D-40).
- **Note (2026-10-19):** The fill model exists now (D-45). The two limit-entry families tested on it failed (D-46); this strategy wasn't among them.
- **Date:** 2026-10-19
- **Note (2026-10-21), Donchian 15m grid:** at the user's request, fixed before the runs: entry {20, 55, 110, 220, 440, 880} × exit {10, 20, 40, 80, 160, 320} (exit ≤ entry, 26 settings), the same coins, costs, and 2× as the 4h grid in D-41's note. On 2020–2023, 15, 17, 12, and 13 of the 26 settings were positive on BTCUSDT, ETHUSDT, SOLUSDT, and XRPUSDT, so the grid fails the rule (a majority on BTCUSDT and two other coins). The split is by time span, not by coin: channels of hours lose on every coin (BTCUSDT 20/10: 3,523 trades, 25.7% won, −0.153 R, −99.2%, the daily limit hit on 34 days), and only channels of several days are positive. 880/320, the same span as 4h 55/20, matched the 4h strategy without beating it consistently (BTCUSDT +0.297 against +0.325 R on 2020–2023, +0.016 against +0.068 R on 2024 onward) with 20–35% more trades. The edge is in multi-day trends; deciding every 15 minutes adds nothing, so the 4h strategy stays. The 2024+ 15m data had been seen before (above), so this counts as one more test on it.

## D-45 Limit entries in the backtest: post-only for one candle, filled only when traded through

- **Decision:**
  - **Interface (extends D-36):** an entry may carry a limit price. The order is placed at the next candle's open, as live would send it right after the close, and cancelled at that candle's close if it hasn't filled; the strategy then decides again. An entry without a limit price stays a market order. Exits stay market orders and stops stay `STOP_MARKET` `closePosition` orders (safety principles 5 and 6). Each strategy declares whether it uses limit entries.
  - **Fill model (extends D-40):**
    - The limit is rounded to the tick away from the market (down for a long, up for a short), and the entry is sized at it with the live sizing, gate, and caps (D-22, D-24, D-25, D-37).
    - A buy limit at or above the next open, or a sell limit at or below it, is rejected, as Binance rejects a post-only (`GTX`) order that would trade at once (`binanceNotes.md`).
    - Otherwise it fills only if the last price trades through it by at least one tick within that candle: a buy when the low reaches the limit minus a tick, a sell when the high reaches the limit plus a tick. A touch doesn't fill.
    - A fill is at the limit, with the maker fee (default 0.02%, the regular tier, `--maker-fee`) and no slippage.
    - If the fill candle's mark price range also reaches the stop, the stop fires in that candle after the fill: at the stop price with the stop slippage, even if the mark price opened past it.
    - The report counts limit entries placed, filled, rejected as crossing, and left unfilled.
  - **Live:** the engine can't place limit entries yet. Loading `config.toml` (D-28) refuses a `[strategy]` that uses them, so `run` doesn't start. If a strategy returns one anyway, the strategy runner stops that symbol's strategy without placing an order (D-43).
- **Why:** The 15m search (D-44) changed only the fee and kept the market entry's time and price. That misses what a limit entry really does: a better price, but also adverse selection (it fills when the price runs through it, against the trade at first) and missed fills (no fill when the price moves away, often the best trades). An edge must survive those before any engine work. Each choice sits on the pessimistic side:
  - One candle only: an order that lived across candles would make the strategy remember it, and a restart could then decide differently (D-36).
  - A trade-through: a touch says nothing about the order's place in the queue, but once the price trades past the limit, every order resting there has filled.
  - The same-candle stop takes the worst order within the candle, since candles can't tell it (D-40 Limitations).
  - Sizing at the limit matches the fill price, so a stop-out still loses about one risk amount (D-22).
  - Refusing live: otherwise a strategy chosen on maker fills would trade at market with taker costs and different fills, which isn't the strategy that was backtested (D-07).
- **Unchanged:** market-entry results are identical. BTCUSDT 4h Donchian 55/20 and 15m Bollinger, 2020–2023, gave the same report and trade list before and after (2026-10-19).
- **Limitations:** No partial fills, queue position, or spread. The fill uses only the last price candle; the mark price only triggers the stop.
- **Later, if a limit-entry strategy survives (out of scope here):** the engine's limit orders (`GTX`, expired by `GTD` or cancelled by the engine at the next close), partial fills, and counting a pending entry's risk at the entry gate (D-24).
- **Alternatives:** Changing only the fee (what D-44 measured); an order that rests until filled or replaced; filling on a touch; 1m candles to order events within a candle.
- **Note (2026-10-19):** No limit-entry strategy survived the first test of this model (D-46), so the engine work above waits.
- **Note (2026-10-20):** Since D-47 the same check refuses such a strategy in `symbols.json` at startup and in a client's `set_symbol`.
- **Date:** 2026-10-19

## D-46 Limit-entry candidates: neither family passed, so no limit-entry strategy goes forward

- **Decision:** Two limit-entry families were tested on the D-45 fill model (GitHub issue #17). The Bollinger reversion with a deeper limit is dropped, and the trend pullback's chosen setting failed its holdout. No limit-entry strategy goes to a forward test, the engine gets no limit orders for now, and the grid isn't widened. Both stay in the registry for backtests.
- **The strategies (D-36 interface, 1h candles, each limit valid for one candle):**
  - Bollinger with a limit offset: the existing signal (a close outside the 20-candle mean ± 2σ), entered with a limit a·σ beyond the close and the stop s·σ beyond the limit. a = 0 is the existing market entry.
  - Trend pullback: 4h bars built from the 1h candles. The trend is the last bar's close against the mean of the last 55 bar closes, and ATR is the mean true range of the last 14 bars. While flat, every candle enters with the trend, with a limit k·ATR beyond its close and the stop m·ATR beyond the limit. The position closes when a bar closes across the mean. k = 0 enters at market.
- **How they were tested** (rules fixed in the issue before any run; D-40 costs with the 0.02% maker fee, SOLUSDT and XRPUSDT at 0.04% slippage and 0.10% on stops; 2× leverage; testnet risk values):
  - **Selection,** 2020–2023 (SOLUSDT from 2020-10) on BTC, ETH, SOL, and XRP: 8 limit settings, a or k ∈ {0.5, 1} × s or m ∈ {2, 3}, each against the market control with the same stop. A setting passed with positive expectancy on BTC and two of the other three coins (at least 30 trades each) and with a higher expectancy than its control on BTC and two of the other three.
  - **Bollinger:** the limit cut its control's loss by a fifth to a third (means −0.077 to −0.103 R against −0.114 and −0.157 R), but every setting lost on every coin (t −1.4 to −4.1). Before costs, BTC was about zero across all six settings (−0.020 to +0.029 R): a limit saves costs, but there was no edge to save them for.
  - **Trend pullback:** all four limit settings passed both rules (means +0.129 to +0.236 R; controls +0.105 and +0.156 R). k = 1, m = 2 had the highest mean, +0.236 R: BTC +0.156, ETH +0.013, SOL +0.517, XRP +0.260 (180–303 trades, t 0.1–1.5). SOL carried the mean, and ETH was flat.
  - **Fills:** with k = 1, 1–2% of the limit orders filled (about 8% at k = 0.5), since a new order went out every hour while flat. None was rejected as crossing: a 1h candle opens next to the previous close.
  - **Holdout,** 2024-01-01 to 2026-10-18, k = 1, m = 2: positive on ETH only (+0.058 R); BTC −0.076, SOL −0.029, XRP −0.022 (176–228 trades, every |t| under 1). The rule needed three of four. Its market control was positive on all four but weak (mean +0.077 R, t 0.1–0.8), so the limit's lead from the selection period reversed.
- **Why:** The rules set before the runs decide it: the Bollinger family failed the selection, and the trend pullback failed its holdout. Deep limit fills in a trend look adversely selected: they catch the dips that keep going and miss the trends that never pull back. A limit changes which trades are taken more than what they cost, so it can't create an edge the signal lacks.
- **By-product, not a candidate:** the trend pullback's market controls were positive on every coin in both periods, weakly in the holdout. They are a 4h trend follower, like the strategy already in the forward test (D-41). They weren't candidates here, and the holdout has now been seen, so a test of them would count as one more on the same history.
- **Revisit:** Limit entries only with a signal that has an edge before costs, or if slippage on a larger account makes market entries costly.
- **Date:** 2026-10-19

## D-47 Each symbol's leverage and strategy live in an engine-owned file and change while the engine runs

- **Decision:**
  - **Settings file:** each symbol's leverage and strategy (name, interval, every parameter) live in `engine/state/symbols.json` (gitignored), which only the engine writes, atomically like the halt record (D-29). Risk values stay in the hand-edited `config.toml` (D-28). The first `run` or `trial` without the file creates it from `config.toml`'s `[symbols]` and `[strategy]`, the strategy on every symbol as D-43 had it; after that those tables are ignored with a WARNING. A file that can't be read or holds a value out of bounds stops the engine at startup. Parameters are saved with their defaults filled in, as strings.
  - **Commands:** clients send `set_symbol` (add a symbol, or change its leverage and strategy) and `remove_symbol`, with an `args` object (GitHub issue #18). The engine checks every value against the same bounds as `config.toml`: leverage 1–10, a registered strategy and interval, the warm-up bound, no limit entries (D-45). It also refuses a symbol that isn't a trading perpetual margined in USDT, an 11th symbol, and any change to a symbol with an open position. The protocol version stays 1.
  - **Applying:** one change at a time, holding the symbol's order lock while the exchange shows it flat, so no entry can run between the check and the change. The order is checks, then the exchange (margin type and leverage) and the strategy's warm-up, then saving the file, then the engine's state. If the save fails, the leverage is put back and nothing else has changed. Only the changed symbol is touched: its trader, strategy, account state, and kill-switch coverage; the display-only mark price stream resubscribes. A change of leverage alone keeps the running strategy.
  - **New strategy:** a strategy set on a symbol warms up and decides on the latest closed candle at once, as at startup (D-43), so an entry signal there fills at the current price.
  - **Forward record:** one CSV per symbol and strategy setting, named after the strategy and every parameter (`BTCUSDT_4h_donchian_entry_period=55_exit_period=20.csv`), so a replay never mixes two settings. The record written before this, `BTCUSDT_4h.csv`, stays as it is.
  - **Margin asset:** fixed at USDT. With no symbols the engine still runs and watches the daily loss limit.
  - **State:** each symbol carries its leverage and strategy setting next to D-43's last candle and signal; the top-level `strategy` is gone. The GUI shows them in the symbol table; its settings screen is D-48.
- **Why:**
  - The user decided (2026-10-19) that the GUI adds and removes coins and changes a coin's strategy, parameters, and leverage, but not while that coin has an open position.
  - A file of its own instead of `config.toml`: the engine saves a change without rewriting the user's hand-edited file and comments, and risk values stay where no client or engine bug can change them.
  - The engine checks the values because the GUI only sends commands (safety principle 4), and a GUI bug must not get past the bounds.
  - Only while flat: otherwise a position opened under one setting would be managed under another (sized and stopped by the old strategy, exited by the new one), and a leverage change would move an open position's liquidation price (D-16, D-25).
  - Saving before acting, and putting the leverage back on failure, keep what the engine trades equal to what a restart would trade.
  - Deciding at once was the user's choice: it matches a restart, and a new strategy doesn't wait up to a candle.
  - USDT fixed: the daily loss limit covers one asset (D-23), and adding a symbol must not change it.
- **Supersedes:** D-43's one `[strategy]` for every symbol. D-39's rule stands as an operating rule the code doesn't enforce: a strategy goes on a coin only after it held up on several coins, with parameters not tuned per coin.
- **Limitations:** A symbol halted by D-16, D-19, or D-20 stays halted through a settings change; removing and adding it again clears the halt. A strategy stopped by an error (D-43) restarts only with a new setting or an engine restart. Two checkouts can still put two engines on one account (`progress.md` Known issues).
- **Alternatives:** Writing changes into `config.toml` (rewrites the user's file and puts risk values within a client's reach); SQLite (D-04, still open); changing a symbol with an open position; a new strategy that waits for the next closed candle.
- **Date:** 2026-10-20
- **Note (2026-10-22):** A symbol's entry may also carry the user's pause, `paused_at` (D-50).

## D-48 GUI symbol settings: the engine sends a strategy catalog, and the main process confirms each change with the copy it sends

- **Decision:**
  - **Catalog:** the engine's `welcome` carries what `set_symbol` accepts: the leverage and symbol-count bounds, the kline intervals, and every registered strategy with its default interval and each parameter's name, type (`int`, `decimal`, `bool`), and default as text. It is built from the strategy registry. The protocol version stays 1. Connected to an engine that sends no catalog, the GUI turns the settings off and asks for a restart on new code.
  - **Confirmation:** `set_symbol` and `remove_symbol` go through the native dialog in the main process, like the other commands (GitHub issue #19). The main process checks the command's shape only: a known name, exactly the expected keys, a symbol in the engine's pattern, a whole-number leverage, a strategy that is null or a name, interval, and text parameters, and text limited to characters that can't break the dialog's lines. It writes the dialog from that copy against the last state it received (an addition, or each value before → after) and sends that same copy. A new or changed strategy's dialog says it decides on the latest closed candle at once and can enter at the current price (D-47), and that the forward record moves to a new file. Bounds stay the engine's check.
  - **Screen:** a panel under the symbol table, opened by an add button or a row's change button: symbol (when adding), leverage, strategy, interval, and the parameters, filled with the current values, or the strategy's defaults when another strategy is chosen, with a reset to defaults. While the symbol has a position, the panel says the engine refuses and disables apply and remove; apply is also off while disconnected, while a command is in progress, when nothing changed, and on a typing mistake (format only). A success closes the panel and shows the engine's answer in the result banner; a failure stays in the panel with the reason.
- **Why:**
  - Catalog from the engine: the parameters and their defaults are the strategies' own code (D-07, D-36), so a copy in the GUI would drift when a strategy or a default changes. In `welcome` rather than the state, because it comes from code and can't change while the engine runs, and the state goes out about once a second (D-33).
  - Confirmation in the main process: a strategy set on a symbol can enter at once (D-47), so a renderer bug must not send a change unconfirmed, nor show one setting and send another. Writing the dialog from the copy that is sent makes it say exactly what goes out; the character limits keep a value from adding lines that pass for dialog text.
  - A panel on the main screen keeps positions and the log in view while a coin is set up.
  - Whether a setting enters with limit orders depends on its values (`trend-pullback`'s defaults do), so the catalog doesn't say; the engine's refusal explains it (D-45).
- **Relation to #7:** one function in the main process writes the confirmation of every command (eight since D-50), so #7 changes only how it is shown. Confirming in the renderer (#7's option B) would now also let a renderer bug send `set_symbol`.
- **Alternatives:** a strategy list hard-coded in the GUI; the catalog in the state; a separate tab or a modal form; confirming in the renderer.
- **Date:** 2026-10-20

## D-49 GUI fonts: Pretendard and JetBrains Mono, bundled with the app

- **Decision:**
  - The GUI's text font is Pretendard (the variable version), for Hangul and Latin alike. Values, IDs, and logs, which already used the monospace font, use JetBrains Mono; Hangul in them falls back to Pretendard.
  - Both come from npm packages (`pretendard`, `@fontsource-variable/jetbrains-mono`, OFL-1.1) installed as devDependencies, and the renderer bundle carries their woff2 files, about 2.1 MB.
  - Digits are tabular everywhere (`font-variant-numeric: tabular-nums`), and ligatures are off wherever the monospace font is used.
- **Why:**
  - Pretendard's Latin is based on Inter and its Hangul on Source Han Sans, drawn to match, so mixed Korean and English text keeps one weight and height. It has tabular figures (`tnum`), which keep numbers aligned in columns and still while they update.
  - JetBrains Mono tells 0 from O and 1 from l and I at a glance, which matters for prices and order IDs.
  - Bundled rather than referenced by name as installed fonts, at the user's choice (2026-10-21): the GUI then looks the same on a machine without them and in a packaged app, and no system font change alters it. The CSP (`default-src 'self'`) rules out a font CDN. The npm packages keep font files out of git; as devDependencies, electron-builder doesn't pack the whole Pretendard package (98 MB unpacked) into the app, and Vite copies only the woff2 files the CSS uses.
  - Ligatures off: JetBrains Mono joins `->`, `>=`, and `!=` into single glyphs (→, ≥, ≠), so logs and values would no longer show the characters as sent (seen in the browser pane, 2026-10-21).
- **Alternatives:** the system fonts (Segoe UI, Malgun Gothic, Cascadia Mono), used until now; the installed fonts by name; the IBM Plex family (Sans KR, Sans, Mono); Pretendard with tabular figures in the symbol table instead of the monospace font (not tried).
- **Date:** 2026-10-21

## D-50 The user pauses one symbol, or closes its position and pauses it; the pause is saved with the symbol

- **Decision:**
  - **Pause:** a per-symbol state the user sets, separate from the engine's own halt of a symbol (D-16, D-19, D-20) and from the account halt (D-27). Like them it refuses entries only: the position, its exchange-side stop, the strategy's exit signals, the daily loss limit, and the kill switch carry on. The other symbols keep trading.
  - **Commands** (GitHub issue #24), each with `args` `{symbol}`: `pause_symbol` pauses; `close_symbol` pauses first, then closes the whole position with `reduceOnly` market orders (role `x`, D-21) and cancels the leftover stop (D-18); `resume_symbol` lifts the user's pause only, never the engine's halt. A pause or resume that is already in place answers `ok: true` and changes nothing; an unknown symbol is refused. They take turns with `set_symbol` and `remove_symbol`, one symbol command at a time, so their saves of `symbols.json` never race.
  - **Saved:** as `paused_at` (ISO 8601 UTC) in the symbol's entry in `symbols.json` (D-47), so a restart, including one the GUI starts on its own (D-34), keeps it; the engine applies it before any strategy decides. It isn't a setting: `set_symbol` neither takes nor clears it, a settings change keeps it, and removing the symbol forgets it.
  - **Failures:** a pause or resume that can't be saved changes nothing. For `close_symbol` the pause holds in memory and the close goes ahead (as D-29); the result then says `ok: false` with what was closed and that a restart would trade the symbol again.
  - **GUI:** the status column of the symbol table shows the pause next to the engine's halt, with a pause or resume button, and a close-and-pause button while the symbol has a position. The confirmation is written in the main process from the command it sends and the last state (D-48). The account-wide emergency close stays in the header.
- **Why:**
  - The user asked (2026-10-21) to close one coin without halting the others: the emergency close of the ETHUSDT 1m engine exercise had also halted the BTCUSDT 4h forward test.
  - Saved, as the user chose: an engine started after a pause would otherwise decide on its latest closed candle at once (D-43) and could enter on the symbol the user stopped.
  - Pause without a close, as the user chose: it is the same state minus the close, and it is the GUI's entry pause that D-33 left open.
  - Resume lifts the user's pause only, as the user chose: the engine halts a symbol for a missing stop, an unclear order outcome, or a leverage mismatch, which need a look before trading resumes; removing and re-adding the symbol still clears those (D-47).
  - Pause before the close: no entry can start between the close and the pause. The pause also waits for an entry already holding the symbol's lock, so once `pause_symbol` answers, nothing enters.
- **Discord bot (safety principle 9):** `pause_symbol` and `close_symbol` are stop and close actions; `resume_symbol` starts trading again, so it is not.
- **Limitations:** a close that fails (a request error or an unclear outcome, D-20) leaves the pause in place; the stop still protects the position, and the user can press it again. The close isn't retried as the kill switch's is (D-27). `trial` ignores the pause; it runs only with the user's OK each time.
- **Alternatives:** a pause in memory only; resume also lifting the engine's halt; one command with a `close` flag; reusing the engine's halt for the user's pause, which would make resume lift both.
- **Date:** 2026-10-22
