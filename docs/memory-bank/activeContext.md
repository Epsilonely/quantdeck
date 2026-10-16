# Active Context

_Last updated: 2026-10-16_

## Current focus

Risk management is done (GitHub issue #4): today's loss from income history (D-24, D-30), the entry gate, and the kill switch with its halt record (D-27, D-29); `quantdeck-engine lift-halt` lifts a halt. It passed the extended `quantdeck-engine trial` on testnet on 2026-10-16: the kill switch closed the position and had the symbol clear of orders within about 0.6 s, refused the next entry, and a restarted guard came up halted. The trial's income-delay measurement bug (income times are whole seconds; see `binanceNotes.md`) is fixed; a re-run on 2026-10-16 measured a 1.1 s delay. Work items are tracked as GitHub issues.

There is still no trading loop, so nothing calls the guard's check on events or on a timer yet. D-30 lists the calls the loop must make.

## Open questions

Roughly in the order they're needed:

- **Storage:** what SQLite records, whether logs go there (D-12), where historical candles come from, and how Parquet files are laid out. The halt record is settled (D-29).
- **Engine operation:** who starts the engine (manually, the GUI, a Windows service or scheduled task) and what restarts it after a crash.
- **Engine ↔ GUI protocol:** message types and schemas, localhost authentication, the initial state sent on connect, and which commands the GUI may send (lifting a kill-switch halt is one, D-27). Lifting is a CLI command for now; a running engine doesn't notice it until restarted.
- **Strategy:** the first strategy (symbol, timeframe, logic) and the interface live and backtest share (D-07). Every entry signal carries a stop price (D-22); whether the strategy manages an adopted position is open (D-19); adding to an open position is out of scope for now (D-22). Backtest fill model: slippage, fees, funding.
  - Take profit, discussed 2026-10-16 and undecided: an optional take-profit price on the entry signal, placed like the stop as a `TAKE_PROFIT_MARKET` `closePosition` algo order. `TRAILING_STOP_MARKET` can't use `closePosition` (D-18), so trailing is deferred.
- **Later:** GUI `sandbox: false`, Discord bot stack and which alerts it sends, mainnet prerequisites (Ed25519 per D-11, IP whitelist, go-live criteria, mainnet risk values — D-23 to D-25 set testnet starting values only).

## Next steps

1. Next roadmap item: the engine ↔ GUI WebSocket connection. It needs the protocol decisions under Open questions first. Open a GitHub issue for it.
2. The income-history delay is measured (1.1 s on testnet, one sample; D-30). More samples are optional, and `trial` needs the user's OK each time since it places orders.
3. Backfill klines missed during stream reconnects (see Known issues in `progress.md`). A GitHub issue for this was drafted but the user put filing it on hold (2026-10-03).
4. Remaining Binance checks are listed under To verify in `binanceNotes.md`; several need a stop to actually trigger on testnet.
