# Decisions

Design decisions and why they were made. Add new entries at the bottom. When a decision is reversed, mark the old entry `Superseded by D-XX` instead of deleting it.

The backtest and the strategy search (D-37 to D-41, D-44 to D-46, D-52, D-57) are in `strategyResearch.md`. The two files share one numbering: a new entry takes the next number after the highest in either file and goes at the bottom of the file for its area.

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
- **Note (2026-10-29, GitHub issue #39):** The cancel that follows a closed position on the user data stream runs as a task of its own. When the engine closes the position itself, the close still holds the symbol's lock, and awaiting the cancel there had held back every symbol's events until the close ended (most of a kill switch). The cancel still checks under the lock that the symbol is flat, so an entry made meanwhile keeps its stop.
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
- **Note (2026-10-22, GitHub issue #25):** At the user's request, the GUI's today's loss card also shows, on a smaller line, every open position's unrealized PnL and the loss with it counted, so the distance to the limit can be seen while positions are open. Display only, as the user agreed: the limit, the entry gate, and the kill switch still count realized results, for the reasons above. Both come from the display-only mark prices (D-33; fields in `techContext.md`). Today's loss is re-read a few seconds after a position closes (D-30, D-31 note), so for those seconds the closed position's unrealized part is gone while its realized result hasn't arrived yet.
- **Date:** 2026-10-10

## D-25 Position cap 2× the base, leverage 5× capped at 10×, stop within half the liquidation distance

- **Decision:**
  - An entry's notional is capped at a configured multiple of the base (testnet: 2×) and at the available balance × leverage, so its initial margin fits (replaced by the note of 2026-10-26, which leaves room for Binance's full opening cost). A trimmed entry risks less than the risk amount.
  - Leverage per symbol comes from configuration (D-16); testnet starts at 5×. A code constant caps it at 10× (D-28).
  - Before every entry, the stop must sit no farther from the entry price than half the distance to the estimated liquidation price; otherwise the entry is skipped with a WARNING.
- **Why:** D-22 sizing grows as the stop tightens: 1% risk with a 0.25% stop is a notional of 4× the base. With isolated margin and an exchange-side stop, leverage doesn't change the planned loss (quantity × stop distance). It sets two other things: the liquidation distance (about 1/leverage) and the margin, which is what a failed stop can lose. At 5×, liquidation sits about 20% away, leaving room for stops up to about 10%, and a full 2× position ties up about 40% of the base. Keeping the stop within half the liquidation distance leaves room for maintenance margin, fees, and mark/last price divergence.
- **Revisit:** The leverage, once the first strategy's typical stop distance is known.
- **Note (2026-10-19):** Revisited with the first strategy: testnet leverage is now 2× (D-41). Its stops are often wider than 5× allows, and the reasoning above (leverage sets the liquidation distance and the margin, not the planned loss) is unchanged.
- **Note (2026-10-26, GitHub issue #32):** The available-balance cap is now the available balance ÷ the cost of opening one unit of notional, (1 + 0.3%) ÷ leverage + 0.3% + the 0.05% taker fee, not the available balance × leverage. Binance's margin check prices a market order at the last price + 0.15% and adds a long's open loss (`binanceNotes.md`, Cost to open a position), so an entry at the old cap was always refused with `-2019`: at 2× with nothing else open that cap equals the position cap, and two ETHUSDT entries were refused that day. The 0.3% doubles Binance's 0.15% for the last price sitting above the mark price that sizing uses and for the move before the order arrives; the fee is counted because the FAQ doesn't say it isn't. The cap shrinks by about 1% at 2×, 2% at 5×, and 4% at 10×. The backtest sizes with the same code (D-37), so it now trims the same entries.
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
- **Note (2026-10-29, GitHub issue #40):** At startup the kill switch's own startup check (D-27) stands in for the trading loop's first check, so today's loss is read once rather than twice before the stream connects, and clients see it from the first state. The check after the stream connects still runs, since a fill can come between the startup read and the connection.
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
- **Note (2026-10-22):** A second whole-list message, `trades`, came with D-51: the last 7 days' trades, sent only when they change.
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
  - **Settings (superseded by D-47):** one top-level `[strategy]` table in `engine/config.toml` applied to every symbol. Each symbol now has its own strategy in `symbols.json`, checked as this table was: a registered name, a kline stream interval, the backtest's parameter checks, and the warm-up's code bound.
  - **Warm-up:** at startup the engine loads the strategy's `warmup` closed candles from REST (D-42), feeds them to it, and carries out the signal of the most recent one. Later candles come from the closed-candle feed.
  - **Each candle:** the engine reads the position from REST (D-06), retrying while REST fails as the feed does (D-42), passes the candle and the position to the strategy, and carries out an entry or exit through the live paths (D-17, D-18, D-22).
  - **Record:** every candle the strategy decided on appends a row to a CSV in `engine/data/forward/` (one file per symbol and strategy setting since D-47): the candle, the position it saw, the action and stop price, and the outcome. A failed write logs a WARNING and changes nothing else.
  - **Clients:** per symbol, the state carries the last candle decided on, the last signal with its outcome, and why the strategy stopped, if it did. The protocol version stays 1. The top-level `strategy` this also added, shown in the GUI header, gave way to per-symbol settings in D-47.
  - **Failures:** while the account or the symbol is halted (`TradingHalted`; D-16, D-19, D-20, D-27), an entry signal is logged and skipped; exits always run. A request error or an unclear order outcome (D-20) is logged as an ERROR, and the strategy goes on with the next candle. Any other error stops that symbol's strategy with a CRITICAL log; the engine keeps running, so the kill switch, monitoring, and exchange-side stops carry on.
- **Why:**
  - One table for every symbol (superseded by D-47, which keeps this as an operating rule): D-39 runs the chosen parameters unchanged on every coin, and per-symbol settings would invite tuning each one.
  - Acting on the latest closed candle at startup: the GUI stops and starts the engine (D-34), and an exit signal on a candle that closed while it was down would otherwise be lost, leaving open a position the backtest would have closed. The strategy decides from candles and the exchange's position only (D-36), so the restart gives the same decision.
  - Retrying the position read instead of skipping the candle: the strategy's indicators need every candle (D-42), and deciding without the position could miss an exit.
  - The record is what a forward test is checked against: replaying its candles through the backtest must give the same signals (D-36, D-38). It sits with the downloaded data in `engine/data/` (gitignored). It is a record, not state: nothing reads it back (D-06).
  - A request error is usually transient, and the next candle re-reads the position, so a failed order costs one candle. Any other error is a bug; stopping only the strategy keeps the protection running, whereas a stopped engine no longer watches the daily loss limit (D-34).
- **Limitations:** A restart inside a candle repeats that candle's decision and records it twice; if a stop closed that candle's entry before the restart, the repeat enters again, which the backtest wouldn't. An entry carried out at startup fills at the current price, not at the candle's next open (D-40).
- **Alternatives:** per-symbol strategy tables; ignoring the last warm-up candle's signal (loses exits across restarts); stopping the engine on a strategy error.
- **Date:** 2026-10-19
- **Note (2026-10-20):** Superseded in part by D-47, as marked above: each symbol has its own strategy, kept in `symbols.json` and changed by clients while the engine runs. The warm-up, deciding at startup, the record's contents, and the failure handling stand.

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
- **Limitations:** A symbol halted by D-16, D-19, or D-20 stays halted through a settings change; removing and adding it again clears the halt. A strategy stopped by an error (D-43) restarts only with a new setting or an engine restart. Two checkouts can still put two engines on one account (GitHub issue #27; until it is fixed, CLAUDE.md keeps engines out of worktrees).
- **Alternatives:** Writing changes into `config.toml` (rewrites the user's file and puts risk values within a client's reach); SQLite (D-04, still open); changing a symbol with an open position; a new strategy that waits for the next closed candle.
- **Date:** 2026-10-20
- **Note (2026-10-22):** A symbol's entry may also carry the user's pause, `paused_at` (D-50).

## D-48 GUI symbol settings: the engine sends a strategy catalog, and the main process confirms each change with the copy it sends

- **Decision:**
  - **Catalog:** the engine's `welcome` carries what `set_symbol` accepts: the leverage and symbol-count bounds, the kline intervals, and every registered strategy with its default interval and each parameter's name, type (`int`, `decimal`, `bool`), and default as text. It is built from the strategy registry. The protocol version stays 1. Connected to an engine that sends no catalog, the GUI turns the settings off and asks for a restart on new code.
  - **Confirmation:** `set_symbol` and `remove_symbol` go through the native dialog in the main process, like the other commands (GitHub issue #19). The main process checks only the command's shape: a known name, the expected keys and types, and text limited to characters that can't break the dialog's lines. It writes the dialog from that copy against the last state it received (an addition, or each value before → after) and sends that same copy. A new or changed strategy's dialog says it decides on the latest closed candle at once and can enter at the current price (D-47), and that the forward record moves to a new file. Bounds stay the engine's check.
  - **Screen:** a panel under the symbol table, opened by an add button or a row's change button, filled with the current values, or the chosen strategy's defaults. While the symbol has a position, the panel disables apply and remove, since the engine refuses them. A success closes the panel and shows the engine's answer in the result banner; a failure stays in the panel with the reason.
- **Why:**
  - Catalog from the engine: the parameters and their defaults are the strategies' own code (D-07, D-36), so a copy in the GUI would drift when a strategy or a default changes. In `welcome` rather than the state, because it comes from code and can't change while the engine runs, and the state goes out about once a second (D-33).
  - Confirmation in the main process: a strategy set on a symbol can enter at once (D-47), so a renderer bug must not send a change unconfirmed, nor show one setting and send another. Writing the dialog from the copy that is sent makes it say exactly what goes out; the character limits keep a value from adding lines that pass for dialog text.
  - A panel on the main screen keeps positions and the log in view while a coin is set up.
  - Whether a setting enters with limit orders depends on its values (`trend-pullback`'s defaults do), so the catalog doesn't say; the engine's refusal explains it (D-45).
- **Relation to #7:** one function in the main process writes the confirmation of every command (eight since D-50), so #7 changes only how it is shown. Confirming in the renderer (#7's option B) would now also let a renderer bug send `set_symbol`.
- **Alternatives:** a strategy list hard-coded in the GUI; the catalog in the state; a separate tab or a modal form; confirming in the renderer.
- **Date:** 2026-10-20
- **Note (2026-10-26):** The native dialog gave way to a page the main process lays over the window (D-53); the main process still writes every confirmation from the command it sends.

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

## D-51 The GUI shows the last 7 days' trades, read from the exchange, one row per order, in their own message

- **Decision:**
  - **What:** every order filled in the last 7 × 24 hours, newest first, in a panel on the right of the GUI's main screen, under a heading per local date (GitHub issue #29). A row is one order with its fills together: the first fill's time, symbol, side, total quantity, average price (to the finest tick a fill had), realized PnL, fees by asset, and its role.
  - **Role:** from the client order ID (D-21): entry, exit, stop, kill switch, plus liquidation (`autoclose-…`) and ADL (`adl_autoclose`) from the exchange; anything else is "other". A stop fills through the order the exchange places when it triggers, so an order whose ID shows no role is looked up among the symbol's algo orders by `actualOrderId`, a week at a time back to the 90 days the exchange keeps orders.
  - **Source:** the exchange only (D-06): `GET /fapi/v1/userTrades` per symbol for the fills, `GET /fapi/v1/allOrders` once per order for its client ID, `GET /fapi/v1/allAlgoOrders` for a stop's. The symbols read are the engine's own, plus other symbols traded in the window (one removed since, D-47), found at startup from the window's `COMMISSION` income records and later from fills on the user data stream. Roles are kept in memory, since they never change; nothing is written to disk.
  - **When:** at startup, every 5 minutes, and after a stream reconnect for every symbol; for one symbol, 3 s after a fill on it arrives on the user data stream (`ORDER_TRADE_UPDATE` with execution type `TRADE`). A failed read logs a WARNING and keeps that symbol's last fills; nothing is shown until each of the engine's symbols has been read once.
  - **Protocol:** a new engine → client message `trades` with the whole list and its totals, sent after `state` when a client connects and again whenever the list changes. The protocol version stays 1. Shape in `techContext.md`.
  - **GUI:** a header button hides or shows the panel. The window opens 1400 px wide (at most the screen's work area) so the symbol table fits beside it; below 960 px the panel goes under the main column.
- **Why:**
  - The user asked (2026-10-22) to see trades in the GUI: at first today's, then up to 3 months, then settled on the last 7 days in a side panel, newest on top, since a month was too much.
  - 7 days is one `userTrades` query per symbol (its limit), so a full read is one request per symbol, and the window needs no day boundary: neither the risk day (D-23) nor local midnight decides what is listed; only the headings use the local date.
  - Rebuilt from the exchange rather than added up from stream events, as D-30 does for today's loss: a restart, including one the GUI makes (D-34), or a stream gap loses nothing, and no local record can drift (D-06). Income history alone lacks side, quantity, price, and order ID; the stream alone loses what happens while the engine is down.
  - One row per order: a market close can fill in 18 trades (testnet, 2026-10-16), which would bury the list.
  - Its own message rather than the state: the state goes out about once a second (D-33 note), and a week of the ETHUSDT engine exercise can hold hundreds of orders, while the list changes only when an order fills. Sending the whole list each time keeps D-33's reasoning: a client never rebuilds it from deltas.
  - The engine sends the totals, so the GUI never adds amounts (D-33).
- **Relation to D-04:** trade records in SQLite stay undecided. This list reads the exchange, so it can't show anything older than the exchange keeps (`userTrades`: 3 months), which is more than the 7 days shown.
- **Limitations:** on testnet the order a triggered stop places carries the stop's `clientAlgoId` as its own client ID (2026-10-26, `binanceNotes.md`), so a stop's fill gets its role from its own ID and the `actualOrderId` lookup only backs that up. Should the lookup be needed, a stop resting more than 90 days, or an algo order missing from a week's page of 1000, shows as other. An order whose client ID can't be read yet shows as other until a later read finds it.
- **Alternatives:** today only (by the risk day or local midnight); up to 3 months with a day picker and a query command; the list inside `state`; a query command the GUI sends when the panel opens; one row per fill; accumulating `ORDER_TRADE_UPDATE` events; SQLite.
- **Date:** 2026-10-22

## D-53 Confirmations show on a page of their own, which the main process lays over the main window

- **Decision:**
  - **Page:** every command's confirmation (D-48) shows on a separate page, laid over the main window's whole content area as a `WebContentsView`, in place of the native message box (GitHub issue #7). It dims the window behind it and shows the question, the detail, and two buttons: the command's and 취소. The confirm button is red for the two commands that close positions, `emergency_close` and `close_symbol`.
  - **Who confirms:** the main process still writes the confirmation from the command it will send (D-48), and sends the command only on that page's answer, which arrives on the view's own IPC with the id of the request it answers. The page runs sandboxed, with a preload of its own that can only receive a request and answer it; the main window's preload has no confirmation call.
  - **Lifetime:** the page loads with the window and stays hidden between confirmations. One at a time: a command sent while one is open is refused without asking. A page that can't be shown, crashes, or reloads while open sends nothing, and the renderer gets a failed result saying so; a window closed meanwhile counts as cancelled.
  - **Input, as the native box had it:** 취소 has the focus, Enter presses the focused button, Esc cancels, and a click outside the card does nothing. Tab and the arrow keys move between the two buttons only. Clicks in the first 400 ms after it shows are ignored, and so are a held key's repeats.
  - **Look:** main.css's colors in both themes (D-35); a 150 ms fade and a 200 ms rise, off under `prefers-reduced-motion`.
- **Why:**
  - The user wanted a modal that matches the app instead of a native box, and chose this way among #7's options (2026-10-26). #7's option B, a React modal in the main page, would let a renderer bug send any of the eight commands without the user's answer, `set_symbol` and `close_symbol` included. On a page of its own, the main page's code can't press its button, change what it shows, or draw over it, which keeps D-48's guarantee.
  - A view inside the window rather than #7's separate transparent window: it moves and resizes with the window, needs no window placement or focus handling of its own, and avoids transparent windows on Windows.
  - Loaded in advance, so it shows at once instead of after a page load.
  - The click guard and the repeat filter: the page shows a few tens of milliseconds after the click or key press that asked, so a double click's second click, or an Enter held down, would otherwise land on it. Focus on 취소 already makes a stray Enter cancel.
  - A reload loses the request the page was showing, and a page that can't answer would leave an empty view over the whole window, taking every click.
- **Alternatives:** #7's option A as written (a frameless transparent window over the main one); option B (a React modal in the main page); keeping the native box.
- **Date:** 2026-10-26

## D-54 The GUI charts each strategy's candles, which the engine sends from the strategy's own feed

- **Decision:**
  - **Screen:** one chart under the symbol table shows one of the engine's symbols, picked by a tab per symbol or a click on its table row (GitHub issue #34). Its interval is the symbol's strategy's; a symbol without a strategy gets a note instead. On it: the candles, the position's entry price (a solid line in the long or short color), each exchange-side stop (a dashed line), and a small dot per fill from the last 7 days' trades (D-51) at its price on the candle it fell in, of one size at every zoom, drawn over the candles and lines. Each role has a bright color in a hue the candles don't use, not the trades panel's badge color. Hovering a candle lists its fills: time, role, side, quantity, price, and realized PnL. The library is lightweight-charts, bundled in the renderer, with its attribution logo on, as its Apache-2.0 license asks. A plain mouse wheel scrolls the page; Ctrl+wheel (a touchpad pinch arrives as one) zooms the time around the pointer, dragging pans, dragging an axis zooms that axis, and buttons show the latest 50 or 100 candles or all of them. The price axis fits the entry and stop lines along with the candles in view. Switching to another symbol, or a range button, resets the price axis to fit.
  - **Source:** the strategy's closed-candle feed (D-42) fills a chart per symbol. Its warm-up loads 300 closed candles, or the strategy's warm-up if more, in the same request; each candle it hands out joins them; and it keeps the kline stream's latest update of the forming candle, which the strategy never sees, and drops it when the stream stops.
  - **Protocol:** a new engine → client message `candles`, one per symbol with a strategy: its whole list of closed candles, sent on connect and whenever that list changes (a close, a new strategy). The forming candle rides in the state as `strategy.forming_candle`, updated at most once a second. Shapes in `techContext.md`; the protocol version stays 1.
  - **Display only:** nothing reads the chart back, and the GUI turns prices into numbers only to draw them.
- **Why:**
  - The user asked (2026-10-27) for charts of their own coins only, on the interval each is registered with, showing their entry price.
  - Candles from the engine: the GUI makes no exchange calls (D-08), and testnet prices differ from mainnet's by up to about 0.1% (`binanceNotes.md`), so a chart from elsewhere wouldn't line up with the entry and stop lines.
  - From the strategy's feed rather than a stream of the chart's own: no second kline connection per symbol, and the chart's closed candles are exactly the ones the strategy decided on.
  - A whole list per symbol, as the trades are sent (D-51), so a reconnect or a missed message can't leave a client out of step (D-33); the list changes once a candle, so sending it whole costs little. The forming candle changes several times a second, and the state already goes out about once a second.
  - One chart with tabs: up to 10 symbols would leave each of a grid too small to read its price lines.
  - The wheel: the chart sits in the scrolling main column, and a chart that zoomed on the wheel trapped the page's scroll (browser pane, 2026-10-28), so only Ctrl+wheel reaches it. The user found zooming by dragging the axes alone awkward (2026-10-28).
  - The price fit: the user dragged the price axis mostly to bring a stop or entry line into view, and a dragged axis stays unfitted until reset.
  - The fills: labeled arrows above and below the candle, the first version, didn't show the price a fill got, and the user disliked their look (2026-10-28). The library's own dots are at least 11 px and grow with the zoom, and in the badge colors the exit and kill dots disappeared into candles of their color, so the GUI draws its own. A ring of the background around them still didn't catch the eye in the GUI; the user asked for smaller dots in bright colors.
- **Limitations:** the candles are last-price candles while stops trigger on the mark price (D-18), so a candle can touch a stop line without the stop firing, and the reverse; the legend's tooltip says so. A stop far from the candles in view squeezes them, since the price axis keeps it on screen. Fill dots reach back 7 days only. A strategy stopped by an error (D-43) stops its chart too. After a reconnect near a close, a closed candle can reach the chart up to a minute late (D-42), while the forming candle already shows the next one.
- **Alternatives:** a kline stream per symbol for the chart alone, which would also chart symbols without a strategy; the GUI reading Binance's public klines; a grid of small charts; the closed candles in the state; the strategies' indicator lines (Donchian channel, Bollinger bands), left for later since the engine would have to send their values; zooming on a plain wheel; zooming to a box dragged over the chart, which the library lacks and which would clash with dragging to pan; a short tick at each fill's price with a price label, whose labels would overlap where fills crowd; hollow rings, whose role colors read weaker; larger dots ringed in the background.
- **Date:** 2026-10-28
- **Note (2026-10-28):** The strategies' lines came with D-56, and the warm-up now also loads the candles they need before the first one shown.

## D-55 The engine log shows in a window of its own, opened from the header, which counts the warnings missed

- **Decision:**
  - **Window:** the engine log leaves the bottom of the main screen for a separate window (GitHub issue #35), opened by a 엔진 로그 button in the header beside the trades button. Pressing it while the window is open brings that window to the front, restoring it if minimized; closing the main window closes it. It shows the last 1000 records the main process keeps, follows new ones while scrolled to the bottom as the old view did, and shows the engine connection, since records stop while disconnected. Its colors follow the theme chosen in the main window (D-35).
  - **Page:** a third electron-vite page with a preload of its own, sandboxed like the confirmation page (D-53), that can only read the records and the connection. The main process sends that window only those two, and the main window no longer gets the records.
  - **Count:** while the log window is closed or minimized, the main process counts the WARNING and higher records that arrive, and the button shows the count: red if any was ERROR or CRITICAL, amber otherwise, 99+ above 99. Opening or restoring the window clears it. The backlog an engine sends on each connection (D-33) isn't counted.
- **Why:**
  - The user asked (2026-10-28) to open the log like the trades panel, on a page of its own or over the window, and chose a separate window: it can sit on another monitor at its own size while the symbol table and chart stay in view, where a panel over the window would hide them while the log is read. The log took the bottom of the main column, under the chart.
  - Its own sandboxed preload: the window only displays, so it gets no command call and no state.
  - The count: with the log out of view, a warning or error would go unseen. Counted in the main process, which knows whether the log window is open, so a reload of the main window's page keeps it. The backlog repeats records from before the connection, so counting it would show warnings again at every reconnect.
- **Limitations:** a log window that is open but behind other windows counts as seen. Its size and place aren't remembered between openings.
- **Alternatives:** a panel laid over the main window; showing and hiding the log section like the trades panel; counting in the main window's page; counting the backlog.
- **Date:** 2026-10-28
- **Note (2026-10-30):** Closing the main window now hides it in the tray (D-58), and the log window stays open; quitting the GUI from the tray closes both.

## D-56 The chart draws the levels a strategy decides by in the position's current state, computed by the strategy's own code

- **Decision:**
  - **What:** each strategy names the levels it decides by and the position states it uses each in: flat for entries, long or short for exits (GitHub issue #36). The chart (D-54) draws only the current state's lines, across the whole chart as it draws the entry and stop lines:
    - Donchian: the entry channel while flat; while holding, only the exit channel's side that closes the position.
    - Bollinger: both bands while flat; the middle band while holding.
    - Donchian with ATR stop and ADX filter: the Donchian's lines, plus, while flat and with the filter on, the ADX and its minimum in a pane under the candles.
    - Daily-open breakout: its two levels while flat (the upper only when long only), none while holding, since it exits at the day's end.
    - Move fade and trend pullback: none; they decide on bars built from the candles.
  - A legend button hides and shows them; the legend names each line with its parameters.
  - **Computed by the strategy:** for each candle, the strategy gives the levels it compared that candle against, from the candles before it, through the same functions its decisions use. A level it doesn't use on a candle is left out (the daily breakout's once the day's entry has come). For the forming candle go the levels the closed candles already fix (the channels); Bollinger's bands take the candle's own close, so they end at the last closed candle.
  - **Protocol:** `candles` carries `lines`, with one value per candle and the forming candle's (`techContext.md`). The protocol version stays 1. Connected to an engine that sends none, the GUI draws no lines and says a restart brings them.
  - **History:** the chart keeps the strategy's warm-up of candles before the first one shown, so the warm-up request asks for 300 candles plus that many, capped at one request (1500); past the cap the first candles have no lines. Each candle's levels are computed once, when it joins the chart.
- **Why:**
  - The user asked (2026-10-28) to see the strategies' period settings on the chart, with different lines before and after an entry: once in a position, the lines used only to enter go. Of drawing the current state across the whole chart and switching lines at the entry candle, they chose the first. The second needs each candle's position, and the engine knows when the current position opened only from the last 7 days' fills (D-51).
  - Computed by the strategy in the engine: a line is there to show why the strategy entered or didn't, so it must be exactly what the close was compared against. The Donchian channel here leaves out the current candle, unlike the usual chart indicator, which no close could ever break. A copy in the GUI would drift from the strategy, as a copy of the catalog would (D-48).
  - Backtests are unchanged (D-07): the old and new strategy code decided identically on every candle of BTCUSDT 4h from 2020 and ETHUSDT 15m from 2024-07, at random positions, for every strategy with lines (2026-10-28).
  - Labels come from the engine because they carry the parameters. Lines are drawn as steps because a level holds for its whole candle.
- **Limitations:**
  - Stretches of past positions show the current state's lines too.
  - Stops placed once at entry (the ATR stop, Bollinger's stop width) show only as the stop line.
  - The levels come from last-price candles, while stops trigger on the mark price (D-18).
  - With the ADX filter on, filling 300 candles' levels takes about 0.1–0.2 s on the development PC at the default `adx_period` 14 and about 0.9 s at 90, the most the engine allows (measured 2026-10-29); the other strategies take about 10 ms. It happens at startup and whenever the symbol's strategy is set, in a worker thread since GitHub issue #38 so the event loop keeps running, though Python's GIL still shares the CPU with it.
- **Alternatives:** the GUI computing the indicators; switching lines at the entry candle; every line in every state; smooth lines.
- **Note (2026-10-29, GitHub issue #40):** The Donchian channels are now kept by a window updated as each candle arrives, rather than found by a pass over the channel every candle; the chart's levels run the same window over the candles before each one, so they still come from the code decisions use. The ADX keeps each candle's directional movement and redoes only the smoothing. Backtests are unchanged: before and after, 17 runs on BTCUSDT 15m, 1h, and 4h and ETHUSDT 15m across every strategy gave the same trades, balances, and equity, and the same decisions at random positions and chart levels over 40,000 candles. Donchian 880/320 on BTCUSDT 15m from 2020 went from 110 s to 3 s, 55/20 from 8 s to 3 s, and `donchian-atr-adx` at its defaults from 11 s to 5 s; with `adx_period` 90, still mostly smoothing, from 136 s to 85 s.
- **Date:** 2026-10-28

## D-58 Alarms show as popups the GUI draws itself, decided in the main process; the GUI stays in the tray

- **Decision:**
  - **Popups:** small windows of the GUI's own in the bottom-right corner of the primary screen, newest at the bottom, at most four (a fifth closes the oldest), which never take the focus (GitHub issue #42). Each has a tone (its color), an icon for its kind, a title, two lines, and the time. They stay 5, 10, or 30 s, or until closed, with the countdown paused while the pointer is over them; a click opens the main window. The same alarm coming again while shown (engine errors, a price alarm) updates that popup with a count. The sound is made with Web Audio, a few notes per tone. Each page runs sandboxed with a preload that can only read its popup and say it was clicked or is done.
  - **What can fire:** fills from the last 7 days' trades (D-51), by role: entry; exit (strategy exits, the user's close, and other); stop; kill switch, liquidation, or ADL. From the state: the account halt, a symbol's halt, and a strategy that stopped. A lost connection, except within 60 s after the user confirmed `stop_engine`. ERROR and CRITICAL log records. Today's loss reaching a set percent of the limit, once until it falls back below. Price alarms on the display-only mark price (D-33) of the engine's symbols: a one-time alarm fires whenever its condition holds and then turns itself off; a repeating one fires each time the price moves into its condition. More than three new fills at once show as one popup.
  - **Starting point:** what already holds when the GUI starts, or when the settings are saved, fires nothing, except a one-time price alarm. The last state and trades are kept across a lost connection, so a fill or halt that came meanwhile fires once the GUI reconnects.
  - **Settings:** a header button opens a dialog in the main window; the main process saves them as `alarms.json` in the userData folder, like the theme (D-35), checks every value it saves or loads, and fills fields a newer version added from the defaults. By default every event is on and the loss warning is at 70%. Amounts are compared as exact decimals, never floats (D-33).
  - **Tray:** closing the main window hides it in the tray; the tray's menu opens the window, turns every popup on or off, and quits the GUI. Only one GUI runs at a time; a second one brings up the first one's window and quits.
  - The engine and the protocol are unchanged.
- **Why:**
  - The user asked (2026-10-30) for popups in the corner like KakaoTalk's, settings changed in a dialog from a header button, and a design of the app's own, like KakaoTalk's or Discord's. Windows notifications fix their colors, fonts, and layout, so the user chose popups the GUI draws. They would also have needed an AppUserModelID matching a Start Menu shortcut, and the GUI's (`com.electron`) differs from electron-builder's `appId` (`com.electron.app`).
  - In the main process, from what it already receives: the GUI makes no exchange calls (D-08), and the engine needed no change. Alarms only display.
  - Never taking the focus: a popup that shows while the user types elsewhere must not take the keys.
  - The starting point: the engine sends whole lists and states (D-33, D-51), so without one a GUI restart, including the start that also starts the engine (D-34), would replay the week's fills.
  - A plain dialog rather than the confirmation page: the settings never reach the engine, so D-53's reason (a renderer bug must not send a command) doesn't apply. The main process still checks what it saves.
  - The tray: closing the window would otherwise end the alarms. It was proposed with the popups and taken with them.
- **Limitations:** popups come only while the GUI runs; away from the PC that is the Discord bot's job. They don't reach Windows' notification center and don't follow its do-not-disturb setting; the dialog's and the tray's switch turn them off. A price alarm only works on the engine's symbols, on the testnet mark price (up to about 0.1% from mainnet's, `binanceNotes.md`), checked at each state (about once a second). An order whose role isn't read yet shows as other (D-51 Limitations). The primary display only.
- **Alternatives:** Windows notifications (Electron's `Notification`, with `toastXml` for buttons and images); one window for the whole stack; alarms decided in the engine; confirming alarm settings like commands; closing the window quitting the GUI, as before.
- **Date:** 2026-10-30
- **Note (2026-10-30, GitHub issue #43):** A popup's window now shows only once its page says the popup is drawn, at opacity 0, and then fades in and slides from the right as a whole over 0.2 s; when Windows asks for fewer animations it shows at once. A page that hasn't drawn within 5 s is closed. Shown at `ready-to-show`, the user saw an empty box before the popup slid in, since the entrance was animated inside a window already on screen.
- **Note (2026-10-30, GitHub issue #44):** At the user's request the popups move more naturally. A new popup rises 40 px into the bottom place as it fades in, in place of the slide from the right, and the popups above glide up one place at the same time; when one closes, the ones above glide down. A popup takes its place only once it shows, so no gap waits for a page still drawing. Moves take 0.24 s, eased out; with fewer animations asked for, popups jump to their places.
