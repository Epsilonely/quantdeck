# Active Context

_Last updated: 2026-10-19_

## Current focus

The first strategy is the Donchian breakout 55/20 on BTCUSDT 4h at 2× testnet leverage, picked by backtest (D-41). The evidence is positive but weak, which is enough for a testnet forward test and not for mainnet (D-41).

The strategy is now connected to `run` (GitHub issue #11, D-43, 2026-10-19): with a `[strategy]` table in `engine/config.toml`, each symbol's strategy warms up from REST, decides on the latest closed candle at startup and then on every closed candle from the feed (D-42), and every decision is appended to a CSV record in `engine/data/forward/`. The GUI shows the strategy and each symbol's last candle and signal. The user's `engine/config.toml` has the `[strategy]` table for D-41, so every `run`, including one the GUI starts on its own (D-34), trades on testnet.

The testnet forward test started 2026-10-19 at 11:23 KST. The engine died before the 13:00 KST close when the user closed the terminal running the GUI (GitHub issue #12); the GUI launcher now starts the engine outside its process tree, and the engine has run again since 13:40 KST, deciding the missed candle at startup. The user can't keep it running around the clock, so the forward test will have gaps; they don't invalidate the recorded decisions (D-43). Work items are tracked as GitHub issues.

The next track (decided 2026-10-19) is trading several coins, each with its own strategy, set from the GUI. The user decided that the GUI can add and remove coins and change a coin's strategy, parameters, and leverage, but not while that coin has an open position. The path, proposed by Claude and accepted:
- The GUI only sends commands to the engine.
- The engine checks each value against the code bounds (D-28).
- It saves the coins and their strategies to a new file only the engine writes. Risk values stay in the hand-edited `config.toml`, so no GUI or engine bug can rewrite them.
- It applies the change to that coin alone, without restarting the engine.

A strategy goes on a coin only after it has held up on several coins (D-39). The first step, an entry gate that counts every open position's stop, is done (GitHub issue #13, D-24 note). The work is done in a git worktree while the forward test runs from the main checkout; see Known issues in `progress.md` for what must not run there.

## Open questions

Roughly in the order they're needed:

- **Forward test:** what counts as passing (how many trades or weeks, how close to the backtest's signals), and the tool that replays the recorded candles through the backtest and compares the signals (D-36, D-38). Pausing and resuming entries from the GUI (D-33) is still open. An engine started with a BTCUSDT position opened under 5× refuses to trade the symbol until it is closed (D-16).
- **Strategy:** adding to an open position is out of scope for now (D-22). Take profit and trailing stops are deferred: the chosen strategy exits on its channel (D-36), and `TRAILING_STOP_MARKET` can't use `closePosition` (D-18).
- **15m strategies:** the user wanted one, Donchian or not. The search (GitHub issue #15, D-44) found none worth forward-testing:
  - the move fade lost everywhere;
  - the daily-open breakout passed its holdout only nominally, with an edge that can't be told from zero.

  The 15m holdout (2024 onward) has now been used. A new 15m idea needs its own fixed grid, and its result counts as one more test on the same history. Maker entries are the cost lever left (D-44 Revisit): the backtest now fills limit entries conservatively (GitHub issue #16, D-45), but no strategy uses them yet. Limit entries suit mean reversion; breakouts get adversely selected. The engine gets limit orders only if a limit-entry strategy survives the backtest.
- **Engine operation:** the GUI starts `run` when none is running and stops it on request (D-34). Still open: running it without the GUI (a Windows service or scheduled task) and what restarts it after a crash.
- **Storage:** what SQLite records, and whether logs go there (D-12; D-33 keeps recent logs in memory for now). Historical candles (D-38), the halt record (D-29), and the forward test record (D-43) are settled.
- **GUI confirmation dialogs (#7):** the user wants the native confirmation boxes for the three commands replaced by an in-app looking modal, and is still weighing the approach (2026-10-16):
  - A separate frameless, transparent modal window that the main process lays over the GUI. The confirmation stays in a page the main window's renderer can't touch, so a renderer bug still can't send a command unconfirmed, as with the native box today. More work: its own page (a second electron-vite entry), preload, and window management; creating the window can take 0.1–0.3 s, which a hidden pre-created window would avoid.
  - A React modal in the main window. Simplest, but the renderer then confirms on its own and the main process can't tell whether the user did, so a renderer bug could send `emergency_close` or `stop_engine` unconfirmed.
- **Later:** GUI `sandbox: false`, Discord bot stack, which alerts it sends, and whether its commands need a narrower permission than the GUI's (safety principle 9), mainnet prerequisites (Ed25519 per D-11, IP whitelist, go-live criteria, mainnet risk values — D-23 to D-25 and D-41 set testnet values only).

## Next steps

1. Keep the forward test going: the next 4h closes, then the first entry and exit in the GUI and the CSV record. The roadmap item is checked off once `run` has traded the strategy on testnet (issue #11).
2. Per-coin settings in the engine: the engine-owned settings file, commands to set and remove a coin, and applying a change to one coin while the others keep running (Current focus). Record it as a new decision; it revises D-43's single `[strategy]` table.
3. The GUI settings screen on top of step 2.
4. Limit-entry candidates on the D-45 fill model, with the grid and pass rules fixed before any run (Open questions, 15m strategies).
5. The replay-and-compare tool for the forward test (Open questions).
6. How a packaged GUI finds `engine/` (`techContext.md`, GUI notes).
7. More income-history delay samples are optional; `trial` needs the user's OK each time since it places orders.
8. Remaining Binance checks are listed under To verify in `binanceNotes.md`; several need a stop to actually trigger on testnet, and the kill switch's bulk cancels have only run on an empty book.
