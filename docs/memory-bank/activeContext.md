# Active Context

_Last updated: 2026-10-16_

## Current focus

The engine ↔ GUI WebSocket connection (GitHub issue #5), the next roadmap item. Its design was settled on 2026-10-16:

- `quantdeck-engine run`, a long-running engine with no strategy yet (D-31). Done, including the client WebSocket server (D-32, D-33; message shapes in `techContext.md`). It is testnet-only for now.
- What's left on the issue: the GUI main-process client and IPC, and trying `emergency_close` on testnet (planned from the GUI). Ctrl+C was checked on 2026-10-16.
- Connection and authentication: `127.0.0.1`, an OS-assigned port and per-start token in a connection file, browser `Origin`s refused, the GUI connecting from its main process (D-32).
- Messages: the whole state on every change, recent and live logs, and two commands, emergency close (the kill switch) and lifting the halt (D-33).

Risk management (GitHub issue #4) is done and passed `quantdeck-engine trial` on testnet; the re-run on 2026-10-16 measured a 1.1 s income-history delay (D-30). Work items are tracked as GitHub issues.

## Open questions

Roughly in the order they're needed:

- **Engine operation:** running `run` as a Windows service or scheduled task, and what restarts it after a crash. For now the user starts it by hand (D-31).
- **Storage:** what SQLite records, whether logs go there (D-12, D-33 keeps recent logs in memory for now), where historical candles come from, and how Parquet files are laid out. The halt record is settled (D-29).
- **Strategy:** the first strategy (symbol, timeframe, logic) and the interface live and backtest share (D-07). Every entry signal carries a stop price (D-22); whether the strategy manages an adopted position is open (D-19); adding to an open position is out of scope for now (D-22). Pausing and resuming entries from the GUI waits for the strategy (D-33). Backtest fill model: slippage, fees, funding.
  - Take profit, discussed 2026-10-16 and undecided: an optional take-profit price on the entry signal, placed like the stop as a `TAKE_PROFIT_MARKET` `closePosition` algo order. `TRAILING_STOP_MARKET` can't use `closePosition` (D-18), so trailing is deferred.
- **Later:** GUI `sandbox: false`, Discord bot stack, which alerts it sends, and whether its commands need a narrower permission than the GUI's (safety principle 9), mainnet prerequisites (Ed25519 per D-11, IP whitelist, go-live criteria, mainnet risk values — D-23 to D-25 set testnet starting values only).

## Next steps

1. Continue issue #5 with the GUI: the Electron main process reads the connection file, connects with Node's built-in `WebSocket` (check that Electron's Node also sends no `Origin`), reconnects, and relays state, logs, and the two commands to the renderer over preload IPC; a minimal screen shows the connection and the state.
2. More income-history delay samples are optional; `trial` needs the user's OK each time since it places orders.
3. Backfill klines missed during stream reconnects (see Known issues in `progress.md`). A GitHub issue for this was drafted but the user put filing it on hold (2026-10-03).
4. Remaining Binance checks are listed under To verify in `binanceNotes.md`; several need a stop to actually trigger on testnet.
