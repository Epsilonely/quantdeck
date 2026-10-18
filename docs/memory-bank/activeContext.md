# Active Context

_Last updated: 2026-10-18_

## Current focus

The backtest module (GitHub issue #8). On 2026-10-18 the user and Claude settled the strategy interface live and backtest share (D-36), the backtest's reuse of the live sizing and risk rules (D-37), and where its candles come from (D-38). The first strategy will be picked by backtest from two candidates, a Donchian breakout and a Bollinger Band mean reversion (D-39); nothing goes to the testnet forward test on a guess.

Done before this: the engine ↔ GUI connection and the GUI's monitoring, start/stop, and emergency close (#5, 2026-10-16), and the GUI theme (#6, D-35). Work items are tracked as GitHub issues.

## Open questions

Roughly in the order they're needed:

- **Backtest fill model (#8):** entry fill price and slippage, how a stop triggers (mark price klines, D-18) and where it fills, fees (regular-tier taker 0.05% on mainnet; testnet charged 0.04%), funding from the archive, which symbol rules and leverage brackets to size with, the test period, and the report's form.
- **Strategy:** each candidate's parameters and timeframe come out of the backtest. Pausing and resuming entries from the GUI waits for the strategy (D-33). Adding to an open position is out of scope for now (D-22).
  - Take profit, discussed 2026-10-16 and undecided: an optional take-profit price on the entry signal, placed like the stop as a `TAKE_PROFIT_MARKET` `closePosition` algo order. `TRAILING_STOP_MARKET` can't use `closePosition` (D-18), so trailing is deferred. Neither candidate needs either (D-36).
- **Engine operation:** the GUI starts `run` when none is running and stops it on request (D-34). Still open: running it without the GUI (a Windows service or scheduled task) and what restarts it after a crash.
- **Storage:** what SQLite records, and whether logs go there (D-12; D-33 keeps recent logs in memory for now). Historical candles (D-38) and the halt record (D-29) are settled.
- **GUI confirmation dialogs (#7):** the user wants the native confirmation boxes for the three commands replaced by an in-app looking modal, and is still weighing the approach (2026-10-16):
  - A separate frameless, transparent modal window that the main process lays over the GUI. The confirmation stays in a page the main window's renderer can't touch, so a renderer bug still can't send a command unconfirmed, as with the native box today. More work: its own page (a second electron-vite entry), preload, and window management; creating the window can take 0.1–0.3 s, which a hidden pre-created window would avoid.
  - A React modal in the main window. Simplest, but the renderer then confirms on its own and the main process can't tell whether the user did, so a renderer bug could send `emergency_close` or `stop_engine` unconfirmed.
- **Later:** GUI `sandbox: false`, Discord bot stack, which alerts it sends, and whether its commands need a narrower permission than the GUI's (safety principle 9), mainnet prerequisites (Ed25519 per D-11, IP whitelist, go-live criteria, mainnet risk values — D-23 to D-25 set testnet starting values only).

## Next steps

1. Backtest module (#8): candle download and checks (D-38), the simulator (D-37) and its fill model, the two candidates on the D-36 interface, and the comparison report. Then pick the first strategy and check it on two or three other major coins with the same parameters (D-39).
2. Connect the strategy to `run` for the forward test: backfill klines missed during stream reconnects (Known issues in `progress.md`), load warm-up candles from REST at startup, and pass closed candles to the strategy (D-36). The backfill issue drafted on 2026-10-03 is still on hold at the user's request, but D-36 makes it required before a strategy trades.
3. How a packaged GUI finds `engine/` (`techContext.md`, GUI notes).
4. More income-history delay samples are optional; `trial` needs the user's OK each time since it places orders.
5. Remaining Binance checks are listed under To verify in `binanceNotes.md`; several need a stop to actually trigger on testnet, and the kill switch's bulk cancels have only run on an empty book.
