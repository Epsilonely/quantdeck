# Active Context

_Last updated: 2026-10-11_

## Current focus

Order execution is done (GitHub issue #3): the engine sizes entries, places market orders and `closePosition` stops, protects any position it finds, and resolves unknown outcomes. The testnet-only `quantdeck-engine trial` drives it, since there is no strategy or trading loop yet; it passed on 2026-10-10. Next is the risk management milestone: the daily loss limit, the entry gate, and the kill switch (D-24, D-27). Work items are tracked as GitHub issues.

## Open questions

Roughly in the order they're needed:

- **Storage:** where the kill-switch halt record lives (D-27) — needed for the kill switch. Also what SQLite records, whether logs go there (D-12), where historical candles come from, and how Parquet files are laid out.
- **Engine operation:** who starts the engine (manually, the GUI, a Windows service or scheduled task) and what restarts it after a crash.
- **Engine ↔ GUI protocol:** message types and schemas, localhost authentication, the initial state sent on connect, and which commands the GUI may send (lifting a kill-switch halt is one, D-27).
- **Strategy:** the first strategy (symbol, timeframe, logic) and the interface live and backtest share (D-07). Every entry signal carries a stop price (D-22); whether the strategy manages an adopted position is open (D-19); adding to an open position is out of scope for now (D-22). Backtest fill model: slippage, fees, funding.
- **Later:** GUI `sandbox: false`, Discord bot stack and which alerts it sends, mainnet prerequisites (Ed25519 per D-11, IP whitelist, go-live criteria, mainnet risk values — D-23 to D-25 set testnet starting values only).

## Next steps

1. Risk management: today's loss from income history and the entry gate that caps each entry's risk at the room left (D-24); the kill switch — cancel all orders and algo orders (`DELETE /fapi/v1/allOpenOrders` and `/fapi/v1/algoOpenOrders`), close every position, and the halted state that survives restarts (D-27). Settle where the halt record lives first. Open a GitHub issue for it.
2. Backfill klines missed during stream reconnects (see Known issues in `progress.md`). A GitHub issue for this was drafted but the user put filing it on hold (2026-10-03).
3. Remaining Binance checks are listed under To verify in `binanceNotes.md`; several need a stop to actually trigger on testnet.
