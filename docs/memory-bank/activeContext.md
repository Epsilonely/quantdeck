# Active Context

_Last updated: 2026-10-19_

## Current focus

The backtest module (GitHub issue #8) is built: the strategy interface (D-36), historical data from the archive and REST (D-38), the simulator with the live sizing and risk rules (D-37) and the fill model (D-40), both candidates, a report, and `quantdeck-engine backtest`.

First results, BTCUSDT 1h, 2020-01-01 to 2026-10-17, default parameters (Donchian 20/10; Bollinger 20, 2σ bands, stop 1σ beyond the close), default costs, 5000 USDT, testnet risk values (2026-10-19):

- Donchian breakout: −27.0%, 1538 trades, win rate 31.7%, −0.010 R per trade, max drawdown 56.0%, longest losing streak 22, daily limit never hit. Before fees (3585 USDT) and funding (−383 USDT) it was about +2600 USDT, so costs turned a small edge into a loss.
- Bollinger mean reversion: −99.5%, 2192 trades, win rate 29.2%, −0.321 R per trade, daily limit hit on 143 days. Its stops averaged about 0.9% from entry, so fees and slippage cost about 0.2–0.3 R per trade, and it lost even before costs. From 2023 its balance was too small for the minimum order.

Neither holds up, so under D-39 the candidates change before anything goes to the forward test. The user hasn't decided how yet.

## Open questions

Roughly in the order they're needed:

- **Next candidates (D-39):** what to change after the first results. Options raised: longer candles (4h) so stops are wider and costs a smaller share of each trade; Donchian's classic 55/20 periods; a wider Bollinger stop or a trend filter. Any search over parameters needs a holdout period (choose on early years, confirm on later ones) so the choice isn't fitted to history. 4h data isn't downloaded yet.
- **Strategy:** pausing and resuming entries from the GUI waits for the strategy (D-33). Adding to an open position is out of scope for now (D-22).
  - Take profit, discussed 2026-10-16 and undecided: an optional take-profit price on the entry signal, placed like the stop as a `TAKE_PROFIT_MARKET` `closePosition` algo order. `TRAILING_STOP_MARKET` can't use `closePosition` (D-18), so trailing is deferred. Neither candidate needs either (D-36).
- **Engine operation:** the GUI starts `run` when none is running and stops it on request (D-34). Still open: running it without the GUI (a Windows service or scheduled task) and what restarts it after a crash.
- **Storage:** what SQLite records, and whether logs go there (D-12; D-33 keeps recent logs in memory for now). Historical candles (D-38) and the halt record (D-29) are settled.
- **GUI confirmation dialogs (#7):** the user wants the native confirmation boxes for the three commands replaced by an in-app looking modal, and is still weighing the approach (2026-10-16):
  - A separate frameless, transparent modal window that the main process lays over the GUI. The confirmation stays in a page the main window's renderer can't touch, so a renderer bug still can't send a command unconfirmed, as with the native box today. More work: its own page (a second electron-vite entry), preload, and window management; creating the window can take 0.1–0.3 s, which a hidden pre-created window would avoid.
  - A React modal in the main window. Simplest, but the renderer then confirms on its own and the main process can't tell whether the user did, so a renderer bug could send `emergency_close` or `stop_engine` unconfirmed.
- **Later:** GUI `sandbox: false`, Discord bot stack, which alerts it sends, and whether its commands need a narrower permission than the GUI's (safety principle 9), mainnet prerequisites (Ed25519 per D-11, IP whitelist, go-live criteria, mainnet risk values — D-23 to D-25 set testnet starting values only).

## Next steps

1. Decide the next candidates (see Open questions), backtest them, and pick the first strategy; then check it on two or three other major coins with the same parameters (D-39). Issue #8 stays open until then.
2. Closed-candle feed (GitHub issue #9): backfill klines missed during stream reconnects (Known issues in `progress.md`) and load warm-up candles from REST at startup (D-36). Required before a strategy trades. Then connect the strategy to `run` for the forward test.
3. How a packaged GUI finds `engine/` (`techContext.md`, GUI notes).
4. More income-history delay samples are optional; `trial` needs the user's OK each time since it places orders.
5. Remaining Binance checks are listed under To verify in `binanceNotes.md`; several need a stop to actually trigger on testnet, and the kill switch's bulk cancels have only run on an empty book.
