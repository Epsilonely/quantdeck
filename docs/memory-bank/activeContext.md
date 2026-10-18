# Active Context

_Last updated: 2026-10-19_

## Current focus

The backtest module is done (GitHub issue #8, 2026-10-19), and it picked the first strategy: the Donchian breakout 55/20 on BTCUSDT 4h, with testnet leverage lowered to 2× (D-41; the user's `engine/config.toml` was changed too). The evidence is positive but weak, which is enough for a testnet forward test and not for mainnet (D-41).

The closed-candle feed is done too (GitHub issue #9, D-42): warm-up from REST, gaps filled from REST after reconnects, and the strategy waiting while REST fails. It isn't used by `run` yet. Next is connecting the strategy to `run` for the testnet forward test. Work items are tracked as GitHub issues.

## Open questions

Roughly in the order they're needed:

- **Connecting the strategy to `run`:** where the strategy's settings live (`engine/config.toml` per D-28: strategy name, interval, parameters), whether a forward test records its signals and candles so they can be replayed through the backtest (D-36), and how the GUI shows the strategy. Pausing and resuming entries from the GUI waits for this (D-33). An engine started with a BTCUSDT position opened under 5× now refuses to trade the symbol until it is closed (D-16).
- **Strategy:** adding to an open position is out of scope for now (D-22). Take profit and trailing stops are deferred: the chosen strategy exits on its channel (D-36), and `TRAILING_STOP_MARKET` can't use `closePosition` (D-18).
- **Engine operation:** the GUI starts `run` when none is running and stops it on request (D-34). Still open: running it without the GUI (a Windows service or scheduled task) and what restarts it after a crash.
- **Storage:** what SQLite records, and whether logs go there (D-12; D-33 keeps recent logs in memory for now). Historical candles (D-38) and the halt record (D-29) are settled.
- **GUI confirmation dialogs (#7):** the user wants the native confirmation boxes for the three commands replaced by an in-app looking modal, and is still weighing the approach (2026-10-16):
  - A separate frameless, transparent modal window that the main process lays over the GUI. The confirmation stays in a page the main window's renderer can't touch, so a renderer bug still can't send a command unconfirmed, as with the native box today. More work: its own page (a second electron-vite entry), preload, and window management; creating the window can take 0.1–0.3 s, which a hidden pre-created window would avoid.
  - A React modal in the main window. Simplest, but the renderer then confirms on its own and the main process can't tell whether the user did, so a renderer bug could send `emergency_close` or `stop_engine` unconfirmed.
- **Later:** GUI `sandbox: false`, Discord bot stack, which alerts it sends, and whether its commands need a narrower permission than the GUI's (safety principle 9), mainnet prerequisites (Ed25519 per D-11, IP whitelist, go-live criteria, mainnet risk values — D-23 to D-25 and D-41 set testnet values only).

## Next steps

1. Connect the strategy to `run` through the closed-candle feed and start the testnet forward test (no issue yet); see Open questions.
2. How a packaged GUI finds `engine/` (`techContext.md`, GUI notes).
3. More income-history delay samples are optional; `trial` needs the user's OK each time since it places orders.
4. Remaining Binance checks are listed under To verify in `binanceNotes.md`; several need a stop to actually trigger on testnet, and the kill switch's bulk cancels have only run on an empty book.
