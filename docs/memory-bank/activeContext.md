# Active Context

_Last updated: 2026-10-16_

## Current focus

Issue #5, the engine ↔ GUI WebSocket connection, is done (2026-10-16): `quantdeck-engine run` (D-31), the client server and the GUI client (D-32, D-33), and the GUI starting and stopping the engine (D-34). Emergency close and lifting the halt were tried from the GUI on testnet. The GUI roadmap item (monitoring, start/stop, emergency close) was finished along the way; strategy settings and charts wait for a strategy.

The next roadmap items are the backtest module and the first strategy. Both need the strategy decisions under Open questions first. Work items are tracked as GitHub issues.

## Open questions

Roughly in the order they're needed:

- **Engine operation:** the GUI starts `run` when none is running and stops it on request (D-34). Still open: running it without the GUI (a Windows service or scheduled task) and what restarts it after a crash.
- **Storage:** what SQLite records, whether logs go there (D-12, D-33 keeps recent logs in memory for now), where historical candles come from, and how Parquet files are laid out. The halt record is settled (D-29).
- **Strategy:** the first strategy (symbol, timeframe, logic) and the interface live and backtest share (D-07). Every entry signal carries a stop price (D-22); whether the strategy manages an adopted position is open (D-19); adding to an open position is out of scope for now (D-22). Pausing and resuming entries from the GUI waits for the strategy (D-33). Backtest fill model: slippage, fees, funding.
  - Take profit, discussed 2026-10-16 and undecided: an optional take-profit price on the entry signal, placed like the stop as a `TAKE_PROFIT_MARKET` `closePosition` algo order. `TRAILING_STOP_MARKET` can't use `closePosition` (D-18), so trailing is deferred.
- **Later:** GUI `sandbox: false`, Discord bot stack, which alerts it sends, and whether its commands need a narrower permission than the GUI's (safety principle 9), mainnet prerequisites (Ed25519 per D-11, IP whitelist, go-live criteria, mainnet risk values — D-23 to D-25 set testnet starting values only).

## Next steps

1. Decide the first strategy and the interface live and backtest share (D-07); see Strategy under Open questions. Then file an issue for the backtest module.
2. How a packaged GUI finds `engine/` (`techContext.md`, GUI notes).
3. More income-history delay samples are optional; `trial` needs the user's OK each time since it places orders.
4. Backfill klines missed during stream reconnects (see Known issues in `progress.md`). A GitHub issue for this was drafted but the user put filing it on hold (2026-10-03).
5. Remaining Binance checks are listed under To verify in `binanceNotes.md`; several need a stop to actually trigger on testnet, and the kill switch's bulk cancels have only run on an empty book.
