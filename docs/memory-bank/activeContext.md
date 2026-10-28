# Active Context

_Last updated: 2026-10-28_

## Current focus

**Forward test (GitHub issue #11).** The first strategy is the Donchian breakout 55/20 on BTCUSDT 4h at 2× testnet leverage, picked by backtest (D-41): positive but weak evidence, enough for a testnet forward test and not for mainnet. Since 2026-10-19 it runs in `run` (D-43): each symbol's strategy warms up from REST, decides on the latest closed candle at startup and then on every closed candle (D-42), and every decision is appended to a CSV record in `engine/data/forward/`. BTCUSDT's record is `BTCUSDT_4h_donchian_entry_period=55_exit_period=20.csv` from 2026-10-20 and `BTCUSDT_4h.csv` before it; the replay tool must read both (D-47). BTCUSDT has had no entry yet. The user keeps the engine running by leaving the GUI open, not around the clock, so the record has gaps; they don't invalidate the recorded decisions (D-43). Every `run`, including one the GUI starts on its own (D-34), trades the user's `symbols.json` on testnet.

**Engine exercise on ETHUSDT.** Since 2026-10-21 the user's `symbols.json` also holds ETHUSDT with a short-timeframe strategy, since 2026-10-26 the Bollinger reversion at 5×, on 15m since that afternoon (1m before). It is an engine exercise, not a strategy: many entries, stops, and exits on testnet within days, where the 4h strategy takes weeks per trade, plus real signals for the replay tool; such short timeframes lose after costs (D-44 note). It made the first strategy entries and exits by `run`, on 2026-10-21, and the first stop-outs, on 2026-10-26. On 1m its stops sat 0.1–0.3% away, so every entry was trimmed to the position cap and a round trip's taker fees came to about 30–80% of its planned risk. It shares the account with BTCUSDT, so a day of its losses can fire the kill switch, which also closes BTCUSDT and halts both until the user lifts it; an emergency close of it on 2026-10-21 halted BTCUSDT too, the reason for the per-symbol pause (D-50). Apart from this exercise, a strategy goes on a coin only after it has held up on several coins (D-39); nothing enforces it in code (D-47), and the settings panel only reminds the user.

**Several coins, each with its own strategy, set from the GUI** (decided 2026-10-19): coins are added and removed, and their strategy, parameters, and leverage changed, but not while the coin has an open position. Done: the entry gate counts every open position's stop (GitHub issue #13), the engine side (#18, D-47), and the GUI settings panel (#19, D-48), both checked on testnet. The user's `config.toml` holds risk values only. Per-symbol pause, close-and-pause, and resume (#24, D-50) is merged; pause and resume have run on testnet, the close not yet (below).

Engine work is done in a git worktree while the forward test runs from the main checkout; CLAUDE.md's working rules say what must not run there.

## Not yet verified

Merged work not yet seen against a real engine; most of it needs the engine restarted on the current code:

- Per-symbol close (#24): close the ETHUSDT exercise's position while BTCUSDT keeps trading, and restart once while paused. Pause and resume have run.
- The recent trades panel (#29, D-51) on screen: each role's row, a fill showing within seconds, and the 1400 px window. The engine's list has matched the account's orders, stop fills included.
- The unrealized PnL line on the today's loss card (#25).
- The parameter help tips in the settings panel (#23), and how the panel shows an engine's refusal of a change.
- The engine uptime in the connection tag (#14).
- The `donchian-atr-adx` strategy (D-52) on a running engine: setting it from the panel, its 154-candle warm-up with the ADX filter on, and its decisions and record file. Tested in backtests and with unit tests only.
- The symbol table's display on a running engine (#31, #33): the mark price and a signal's stop on the tick, each signal result's label, the ROI under the unrealized PnL, and the warning when a strategy's next decision is overdue. Checked only with a fake API.
- The entry gate's count of every open position's stop: tested with fakes only.
- An entry trimmed by the available-balance cap filling on testnet (#32, D-25 note of 2026-10-26). At 2× with nothing else open that cap sits just under the position cap, so any entry big enough to be capped at 2× would show it; at 5×, where ETHUSDT trades now, the position cap binds first. Tested against Binance's documented cost formula only.
- The chart (#34, D-54) on a running engine: a 15m and a 4h symbol's candles, the forming candle moving and closing, the entry and stop lines, the fill markers, and a strategy change switching the chart's interval. Checked only with a fake API in the browser pane.
- How the GUI reports an engine's stop or failure since it starts the engine through a starter (D-34 note).
- The in-app confirmation (#7, D-53) in the GUI itself: in `npm run dev`, where the page comes from the dev server; with a real mouse and keyboard, including that clicks don't reach the main page under it; and in a packaged build. Checked only in a test harness with the real overlay over a fake API.
- Switching the Windows app mode while the GUI runs (D-35).

Exchange behavior not yet seen is under To verify in `binanceNotes.md`.

## Open questions

Roughly in the order they're needed:

- **Forward test:** what counts as passing (how many trades or weeks, how close to the backtest's signals), and the tool that replays the recorded candles through the backtest and compares the signals (D-36, D-38). An engine started with a BTCUSDT position opened under 5× refuses to trade the symbol until it is closed (D-16).
- **Strategy:** adding to an open position is out of scope for now (D-22). Take profit and trailing stops are deferred: the chosen strategy exits on its channel (D-36), and `TRAILING_STOP_MARKET` can't use `closePosition` (D-18). The turtle-style ATR stop and ADX filter didn't beat the plain breakout consistently (D-52); they can be set on a symbol, and whether to forward-test them on one is the user's call.
- **15m strategies:** the user wanted one, Donchian or not. The search found none worth forward-testing (D-44), and maker entries rescued none (D-45, D-46), so the engine gets no limit orders for now. The 15m holdout (2024 onward) has been used: a new 15m idea needs its own fixed grid and counts as one more test on the same history. The daily-open breakout was held back partly because one strategy traded per account; per-symbol strategies remove that reason, but its holdout edge still can't be told from zero.
- **Engine operation:** running `run` without the GUI (a Windows service or scheduled task), and what restarts it after a crash (D-34).
- **Storage:** what SQLite records, and whether logs go there (D-12; D-33 keeps recent logs in memory for now). Historical candles (D-38), the halt record (D-29), the forward test record (D-43), and the symbol settings (D-47) are settled. The GUI's last 7 days of trades read the exchange, not a local record (D-51).
- **Later:** GUI `sandbox: false`, Discord bot stack, which alerts it sends, and whether its commands need a narrower permission than the GUI's (safety principle 9), mainnet prerequisites (Ed25519 per D-11, IP whitelist, go-live criteria, mainnet risk values — D-23 to D-25 and D-41 set testnet values only).

## Next steps

1. Keep the forward test going until the first entry and exit show in the GUI and the CSV record. The roadmap item is checked off once `run` has traded the strategy on testnet (#11).
2. The replay-and-compare tool for the forward test (Open questions); it reads both BTCUSDT record files.
3. Once the user restarts the engine on the current code: the checks under Not yet verified, #24 first.
4. The account-wide instance lock (#27).
5. How a packaged GUI finds `engine/` (`techContext.md`, GUI notes).
6. Remaining Binance checks under To verify in `binanceNotes.md`; several need a stop to actually trigger on testnet. `trial` needs the user's OK each time since it places orders.
