# Active Context

_Last updated: 2026-10-20_

## Current focus

The first strategy is the Donchian breakout 55/20 on BTCUSDT 4h at 2× testnet leverage, picked by backtest (D-41). The evidence is positive but weak, which is enough for a testnet forward test and not for mainnet (D-41).

The strategy runs in `run` (GitHub issue #11, D-43): each symbol's strategy warms up from REST, decides on the latest closed candle at startup and then on every closed candle from the feed (D-42), and every decision is appended to a CSV record in `engine/data/forward/`. The user's `engine/config.toml` has the `[symbols]` and `[strategy]` tables for D-41, so every `run`, including one the GUI starts on its own (D-34), trades on testnet.

The testnet forward test started 2026-10-19 at 11:23 KST. The engine died before the 13:00 KST close when the user closed the terminal running the GUI (GitHub issue #12); the GUI launcher now starts the engine outside its process tree, and the engine has run again since 13:40 KST, deciding the missed candle at startup. The user can't keep it running around the clock, so the forward test will have gaps; they don't invalidate the recorded decisions (D-43). The user keeps it going by leaving the GUI open. Work items are tracked as GitHub issues.

The current track is trading several coins, each with its own strategy, set from the GUI (decided 2026-10-19). The GUI can add and remove coins and change a coin's strategy, parameters, and leverage, but not while that coin has an open position. Done so far:
- The entry gate counts every open position's stop (GitHub issue #13, D-24 note).
- The engine side (GitHub issue #18, D-47, 2026-10-20): `engine/state/symbols.json`, the `set_symbol` and `remove_symbol` commands, and per-symbol changes without a restart. The GUI symbol table shows each symbol's leverage and strategy. It is on the branch `worktree-per-symbol-settings`, not yet merged; tests pass, nothing has run on testnet.

Deploying it changes the running forward test, so it needs the user's OK:
- After the merge, the running engine keeps the old code until it restarts; the GUI started from the new code still shows it.
- On its first start the new engine creates `symbols.json` from the user's `config.toml` (BTCUSDT, 2×, Donchian 55/20 4h) and decides on the latest closed candle, as any restart does (D-43).
- The forward record continues in `BTCUSDT_4h_donchian_entry_period=55_exit_period=20.csv`; the rows before it stay in `BTCUSDT_4h.csv`, and the replay tool must read both.
- After that, `[symbols]` and `[strategy]` can be deleted from `config.toml`; until then each start logs a WARNING that they are ignored.

A strategy goes on a coin only after it has held up on several coins (D-39); nothing enforces it in code (D-47). The work is done in a git worktree while the forward test runs from the main checkout; see Known issues in `progress.md` for what must not run there.

## Open questions

Roughly in the order they're needed:

- **Forward test:** what counts as passing (how many trades or weeks, how close to the backtest's signals), and the tool that replays the recorded candles through the backtest and compares the signals (D-36, D-38). Pausing and resuming entries from the GUI (D-33) is still open. An engine started with a BTCUSDT position opened under 5× refuses to trade the symbol until it is closed (D-16).
- **Strategy:** adding to an open position is out of scope for now (D-22). Take profit and trailing stops are deferred: the chosen strategy exits on its channel (D-36), and `TRAILING_STOP_MARKET` can't use `closePosition` (D-18).
- **15m strategies:** the user wanted one, Donchian or not. The search (GitHub issue #15, D-44) found none worth forward-testing:
  - the move fade lost everywhere;
  - the daily-open breakout passed its holdout only nominally, with an edge that can't be told from zero.

  The 15m holdout (2024 onward) has now been used. A new 15m idea needs its own fixed grid, and its result counts as one more test on the same history. Maker entries didn't rescue anything: on the conservative fill model (GitHub issue #16, D-45), two 1h limit-entry families failed their pre-set rules (GitHub issue #17, D-46), so the engine gets no limit orders for now. A limit changes which trades are taken more than what they cost, so it would need a signal with an edge before costs.
- **Engine operation:** the GUI starts `run` when none is running and stops it on request (D-34). Still open: running it without the GUI (a Windows service or scheduled task) and what restarts it after a crash.
- **Storage:** what SQLite records, and whether logs go there (D-12; D-33 keeps recent logs in memory for now). Historical candles (D-38), the halt record (D-29), the forward test record (D-43), and the symbol settings (D-47) are settled.
- **GUI settings screen:** how the GUI learns each strategy's parameters and defaults (hard-coded in the GUI, or a list the engine sends), and how `set_symbol` and `remove_symbol` are confirmed. The main process asks before every command today, and a settings change can enter at once (D-47), so it ties into #7.
- **GUI confirmation dialogs (#7):** the user wants the native confirmation boxes for the three commands replaced by an in-app looking modal, and is still weighing the approach (2026-10-16):
  - A separate frameless, transparent modal window that the main process lays over the GUI. The confirmation stays in a page the main window's renderer can't touch, so a renderer bug still can't send a command unconfirmed, as with the native box today. More work: its own page (a second electron-vite entry), preload, and window management; creating the window can take 0.1–0.3 s, which a hidden pre-created window would avoid.
  - A React modal in the main window. Simplest, but the renderer then confirms on its own and the main process can't tell whether the user did, so a renderer bug could send `emergency_close` or `stop_engine` unconfirmed.
- **Later:** GUI `sandbox: false`, Discord bot stack, which alerts it sends, and whether its commands need a narrower permission than the GUI's (safety principle 9), mainnet prerequisites (Ed25519 per D-11, IP whitelist, go-live criteria, mainnet risk values — D-23 to D-25 and D-41 set testnet values only).

## Next steps

1. Keep the forward test going: the next 4h closes, then the first entry and exit in the GUI and the CSV record. The roadmap item is checked off once `run` has traded the strategy on testnet (issue #11).
2. Merge issue #18, then, with the user's OK, restart the engine on the new code and check on testnet: `symbols.json` created, BTCUSDT unchanged, and `set_symbol`/`remove_symbol` on a second coin from a client script (Current focus).
3. The GUI settings screen on top of #18 (Open questions).
4. The replay-and-compare tool for the forward test (Open questions); it reads both forward record files.
5. How a packaged GUI finds `engine/` (`techContext.md`, GUI notes).
6. More income-history delay samples are optional; `trial` needs the user's OK each time since it places orders.
7. Remaining Binance checks are listed under To verify in `binanceNotes.md`; several need a stop to actually trigger on testnet, and the kill switch's bulk cancels have only run on an empty book.
