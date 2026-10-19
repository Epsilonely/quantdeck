# Active Context

_Last updated: 2026-10-19_

## Current focus

The first strategy is the Donchian breakout 55/20 on BTCUSDT 4h at 2× testnet leverage, picked by backtest (D-41). The evidence is positive but weak, which is enough for a testnet forward test and not for mainnet (D-41).

The strategy is now connected to `run` (GitHub issue #11, D-43, 2026-10-19): with a `[strategy]` table in `engine/config.toml`, each symbol's strategy warms up from REST, decides on the latest closed candle at startup and then on every closed candle from the feed (D-42), and every decision is appended to a CSV record in `engine/data/forward/`. The GUI shows the strategy and each symbol's last candle and signal. This was tested with fakes only; it hasn't run on testnet yet. The user's `engine/config.toml` already has the `[strategy]` table for D-41, so the next `run`, including one the GUI starts on its own (D-34), trades on testnet.

Next is the testnet forward test itself. Work items are tracked as GitHub issues.

## Open questions

Roughly in the order they're needed:

- **Forward test:** what counts as passing (how many trades or weeks, how close to the backtest's signals), and the tool that replays the recorded candles through the backtest and compares the signals (D-36, D-38). Pausing and resuming entries from the GUI (D-33) is still open. An engine started with a BTCUSDT position opened under 5× refuses to trade the symbol until it is closed (D-16).
- **Strategy:** adding to an open position is out of scope for now (D-22). Take profit and trailing stops are deferred: the chosen strategy exits on its channel (D-36), and `TRAILING_STOP_MARKET` can't use `closePosition` (D-18).
- **Engine operation:** the GUI starts `run` when none is running and stops it on request (D-34). Still open: running it without the GUI (a Windows service or scheduled task) and what restarts it after a crash.
- **Storage:** what SQLite records, and whether logs go there (D-12; D-33 keeps recent logs in memory for now). Historical candles (D-38), the halt record (D-29), and the forward test record (D-43) are settled.
- **GUI confirmation dialogs (#7):** the user wants the native confirmation boxes for the three commands replaced by an in-app looking modal, and is still weighing the approach (2026-10-16):
  - A separate frameless, transparent modal window that the main process lays over the GUI. The confirmation stays in a page the main window's renderer can't touch, so a renderer bug still can't send a command unconfirmed, as with the native box today. More work: its own page (a second electron-vite entry), preload, and window management; creating the window can take 0.1–0.3 s, which a hidden pre-created window would avoid.
  - A React modal in the main window. Simplest, but the renderer then confirms on its own and the main process can't tell whether the user did, so a renderer bug could send `emergency_close` or `stop_engine` unconfirmed.
- **Later:** GUI `sandbox: false`, Discord bot stack, which alerts it sends, and whether its commands need a narrower permission than the GUI's (safety principle 9), mainnet prerequisites (Ed25519 per D-11, IP whitelist, go-live criteria, mainnet risk values — D-23 to D-25 and D-41 set testnet values only).

## Next steps

1. Start the testnet forward test: the user starts `run` (it places orders now), then check the startup decision in the log, the GUI's strategy columns, and the CSV record, and later the first 4h closes. The roadmap item is checked off once `run` has traded the strategy on testnet (issue #11).
2. The replay-and-compare tool for the forward test (Open questions).
3. How a packaged GUI finds `engine/` (`techContext.md`, GUI notes).
4. More income-history delay samples are optional; `trial` needs the user's OK each time since it places orders.
5. Remaining Binance checks are listed under To verify in `binanceNotes.md`; several need a stop to actually trigger on testnet, and the kill switch's bulk cancels have only run on an empty book.
