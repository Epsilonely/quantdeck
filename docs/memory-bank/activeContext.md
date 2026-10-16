# Active Context

_Last updated: 2026-10-16_

## Current focus

The engine ↔ GUI WebSocket connection (GitHub issue #5), the next roadmap item. Its design was settled on 2026-10-16:

- `quantdeck-engine run`, a long-running engine with no strategy yet (D-31). Done, including the client WebSocket server (D-32, D-33; message shapes in `techContext.md`). It is testnet-only for now.
- The GUI side is done (2026-10-16): the main-process client with reconnects, preload IPC, and one screen with the state, logs, and the commands. Tested against `run` on testnet; see `progress.md`.
- Added at the user's request (2026-10-16): the GUI starts the engine when none is running and can stop it (D-34).
- What's left on the issue: trying emergency close from the GUI on testnet, which fires the real kill switch and writes the halt record, then lifting the halt from the GUI. Both need the user's OK. When they pass, close #5 and check off the roadmap item.
- Connection and authentication: `127.0.0.1`, an OS-assigned port and per-start token in a connection file, browser `Origin`s refused, the GUI connecting from its main process (D-32).
- Messages: the whole state on every change, recent and live logs, and two commands, emergency close (the kill switch) and lifting the halt (D-33).

Risk management (GitHub issue #4) is done and passed `quantdeck-engine trial` on testnet; the re-run on 2026-10-16 measured a 1.1 s income-history delay (D-30). Work items are tracked as GitHub issues.

## Open questions

Roughly in the order they're needed:

- **Engine operation:** the GUI starts `run` when none is running and stops it on request (D-34). Still open: running it without the GUI (a Windows service or scheduled task) and what restarts it after a crash.
- **Storage:** what SQLite records, whether logs go there (D-12, D-33 keeps recent logs in memory for now), where historical candles come from, and how Parquet files are laid out. The halt record is settled (D-29).
- **Strategy:** the first strategy (symbol, timeframe, logic) and the interface live and backtest share (D-07). Every entry signal carries a stop price (D-22); whether the strategy manages an adopted position is open (D-19); adding to an open position is out of scope for now (D-22). Pausing and resuming entries from the GUI waits for the strategy (D-33). Backtest fill model: slippage, fees, funding.
  - Take profit, discussed 2026-10-16 and undecided: an optional take-profit price on the entry signal, placed like the stop as a `TAKE_PROFIT_MARKET` `closePosition` algo order. `TRAILING_STOP_MARKET` can't use `closePosition` (D-18), so trailing is deferred.
- **Later:** GUI `sandbox: false`, Discord bot stack, which alerts it sends, and whether its commands need a narrower permission than the GUI's (safety principle 9), mainnet prerequisites (Ed25519 per D-11, IP whitelist, go-live criteria, mainnet risk values — D-23 to D-25 set testnet starting values only).

## Next steps

1. Finish issue #5: with the user's OK, fire emergency close from the GUI on testnet (ideally with a small position open, so a real close and bulk cancel happen; see To verify in `binanceNotes.md`), then lift the halt from the GUI. When both pass, the GUI roadmap item (monitoring, start/stop, emergency close) is done as well.
2. How a packaged GUI finds `engine/` (`techContext.md`, GUI notes).
3. More income-history delay samples are optional; `trial` needs the user's OK each time since it places orders.
4. Backfill klines missed during stream reconnects (see Known issues in `progress.md`). A GitHub issue for this was drafted but the user put filing it on hold (2026-10-03).
5. Remaining Binance checks are listed under To verify in `binanceNotes.md`; several need a stop to actually trigger on testnet.
