# Active Context

_Last updated: 2026-10-21_

## Current focus

The first strategy is the Donchian breakout 55/20 on BTCUSDT 4h at 2× testnet leverage, picked by backtest (D-41). The evidence is positive but weak, which is enough for a testnet forward test and not for mainnet (D-41).

The strategy runs in `run` (GitHub issue #11, D-43): each symbol's strategy warms up from REST, decides on the latest closed candle at startup and then on every closed candle from the feed (D-42), and every decision is appended to a CSV record in `engine/data/forward/`. The user's `engine/state/symbols.json` holds BTCUSDT at 2× with the D-41 strategy, so every `run`, including one the GUI starts on its own (D-34), trades on testnet. Since 2026-10-20 the record is `BTCUSDT_4h_donchian_entry_period=55_exit_period=20.csv`; the rows before it are in `BTCUSDT_4h.csv`, and the replay tool must read both (D-47).

The testnet forward test started 2026-10-19 at 11:23 KST. The engine died before the 13:00 KST close when the user closed the terminal running the GUI (GitHub issue #12); the GUI launcher now starts the engine outside its process tree, and the engine has run again since 13:40 KST, deciding the missed candle at startup. The user can't keep it running around the clock, so the forward test will have gaps; they don't invalidate the recorded decisions (D-43). The user keeps it going by leaving the GUI open. Work items are tracked as GitHub issues.

The current track is trading several coins, each with its own strategy, set from the GUI (decided 2026-10-19). The GUI can add and remove coins and change a coin's strategy, parameters, and leverage, but not while that coin has an open position. Done so far:
- The entry gate counts every open position's stop (GitHub issue #13, D-24 note).
- The engine side (GitHub issue #18, D-47, merged 2026-10-20): `engine/state/symbols.json`, the `set_symbol` and `remove_symbol` commands, and per-symbol changes without a restart. The GUI symbol table shows each symbol's leverage and strategy.
  - The user restarted the engine on it at 11:05 KST. It created `symbols.json` from `config.toml`, decided on the latest closed candle (no signal) into the new record file, and logged no warnings. The `[symbols]` and `[strategy]` tables are then deleted from the user's `config.toml`, which now holds risk values only.
  - Testnet check, 11:09 KST, from a client script: `set_symbol` added ETHUSDT at 2× without a strategy in 0.6 s (margin type set to ISOLATED, leverage 2×, saved, mark price stream resubscribed), and `remove_symbol` took it out in 0.1 s. BTCUSDT and its strategy were untouched, and the GUI stayed connected.
- The GUI settings panel (GitHub issue #19, D-48): adding and removing coins and changing their leverage, strategy, and parameters from a panel under the symbol table, with the engine's catalog in `welcome` and the main process's confirmation written from the command it sends. The user restarted the engine on it at 13:49 KST (one start in between was refused by the instance lock while the old engine's streams closed, the known issue in `progress.md`), then from the GUI added ETHUSDT at 3× with the Bollinger reversion on 1h, changed its `width` from 2 to 4, and removed it, between 13:50 and 14:18 KST. Every step was logged without warnings, no signal came, and BTCUSDT was untouched. The change and the removal took 7–9 s, waiting for the old strategy's kline stream to close. With the 2 s stream close timeout (GitHub issue #20), the user restarted the engine at 16:12 KST and repeated the three steps: 1.0 s, 2.6 s, and 2.3 s. ETHUSDT keeps isolated 3× on the testnet account.

A strategy goes on a coin only after it has held up on several coins (D-39); nothing enforces it in code (D-47), and the panel only reminds the user. One deliberate exception, as an engine exercise rather than a strategy: on 2026-10-21 at 15:21 KST the user added ETHUSDT from the GUI with Donchian 20/10 on 5m at 5×, then changed it to 3m and, at 16:13, to 1m with 30/10. It entered long at 16:21 KST, and the user fired the emergency close at 16:24 (`progress.md`), so the account is halted until the user lifts it; lifting is the user's call alone. The 15m grid (D-44 note) shows such short channels lose after costs; the point is many entries, stops, and exits on testnet within days, where the 4h strategy takes weeks per trade, plus real signals for the replay tool. It shares the account with BTCUSDT, so a day of its losses can fire the kill switch, which also closes BTCUSDT and halts both until the user lifts it. Engine work is done in a git worktree while the forward test runs from the main checkout; see Known issues in `progress.md` for what must not run there.

## Open questions

Roughly in the order they're needed:

- **Forward test:** what counts as passing (how many trades or weeks, how close to the backtest's signals), and the tool that replays the recorded candles through the backtest and compares the signals (D-36, D-38). Pausing and resuming entries from the GUI (D-33) is still open. An engine started with a BTCUSDT position opened under 5× refuses to trade the symbol until it is closed (D-16).
- **Per-symbol close and halt (asked for by the user on 2026-10-21):** a button on a symbol's row that closes that symbol's position at market (`reduceOnly`) and halts that symbol alone, while the others keep trading. The account-wide emergency close (D-33) stays as it is. To decide:
  - whether the halt survives a restart, as the account halt does through its record (D-29), and how it is lifted; removing and re-adding a symbol already clears the engine's own per-symbol halts (D-47);
  - whether a pause without a close (entries off, exits still on signal) comes with it, which is the GUI pause above (D-33);
  - its close counts toward today's loss like any other (D-24), and its confirmation goes through the main process like every command (D-48).
- **Strategy:** adding to an open position is out of scope for now (D-22). Take profit and trailing stops are deferred: the chosen strategy exits on its channel (D-36), and `TRAILING_STOP_MARKET` can't use `closePosition` (D-18).
- **15m strategies:** the user wanted one, Donchian or not. The search (GitHub issue #15, D-44) found none worth forward-testing:
  - the move fade lost everywhere;
  - the daily-open breakout passed its holdout only nominally, with an edge that can't be told from zero.

  The 15m holdout (2024 onward) has now been used. A new 15m idea needs its own fixed grid, and its result counts as one more test on the same history. Maker entries didn't rescue anything: on the conservative fill model (GitHub issue #16, D-45), two 1h limit-entry families failed their pre-set rules (GitHub issue #17, D-46), so the engine gets no limit orders for now. A limit changes which trades are taken more than what they cost, so it would need a signal with an edge before costs.
- **Engine operation:** the GUI starts `run` when none is running and stops it on request (D-34). Still open: running it without the GUI (a Windows service or scheduled task) and what restarts it after a crash.
- **Storage:** what SQLite records, and whether logs go there (D-12; D-33 keeps recent logs in memory for now). Historical candles (D-38), the halt record (D-29), the forward test record (D-43), and the symbol settings (D-47) are settled.
- **GUI confirmation dialogs (#7):** the user wants the native confirmation boxes for the commands replaced by an in-app looking modal, and is still weighing the approach (2026-10-16):
  - A separate frameless, transparent modal window that the main process lays over the GUI. The confirmation stays in a page the main window's renderer can't touch, so a renderer bug still can't send a command unconfirmed, as with the native box today. More work: its own page (a second electron-vite entry), preload, and window management; creating the window can take 0.1–0.3 s, which a hidden pre-created window would avoid.
  - A React modal in the main window. Simplest, but the renderer then confirms on its own and the main process can't tell whether the user did, so a renderer bug could send `emergency_close` or `stop_engine` unconfirmed.

  It now covers five commands. The main process writes every confirmation from the command it sends (D-48), so either approach changes only how it is shown; the React modal would also let a renderer bug send `set_symbol`, which can enter at once.
- **Later:** GUI `sandbox: false`, Discord bot stack, which alerts it sends, and whether its commands need a narrower permission than the GUI's (safety principle 9), mainnet prerequisites (Ed25519 per D-11, IP whitelist, go-live criteria, mainnet risk values — D-23 to D-25 and D-41 set testnet values only).

## Next steps

1. Keep the forward test going: the next 4h closes, then the first entry and exit in the GUI and the CSV record. The roadmap item is checked off once `run` has traded the strategy on testnet (issue #11).
2. The replay-and-compare tool for the forward test (Open questions); it reads both forward record files.
3. Per-symbol close and halt from the GUI (Open questions, GitHub issue #24).
4. How a packaged GUI finds `engine/` (`techContext.md`, GUI notes).
5. More income-history delay samples are optional; `trial` needs the user's OK each time since it places orders.
6. Remaining Binance checks are listed under To verify in `binanceNotes.md`; several need a stop to actually trigger on testnet, and the kill switch's bulk cancels have only run on an empty book.
