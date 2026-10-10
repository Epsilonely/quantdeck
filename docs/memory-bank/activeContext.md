# Active Context

_Last updated: 2026-10-10_

## Current focus

The engine connects to testnet read-only, logs to `engine/logs/`, and receives the user data stream (GitHub issue #2). The order-execution design (D-14 to D-22) and the risk limits (D-23 to D-28) are settled; next is the order execution milestone on testnet. Work items are tracked as GitHub issues.

## Open questions

Roughly in the order they're needed:

- **Engine operation:** who starts the engine (manually, the GUI, a Windows service or scheduled task) and what restarts it after a crash.
- **Engine ↔ GUI protocol:** message types and schemas, localhost authentication, the initial state sent on connect, and which commands the GUI may send (lifting a kill-switch halt is one, D-27).
- **Storage:** what SQLite records, including where the kill-switch halt record lives (D-27); whether logs also go there (D-12); where historical candles come from and how Parquet files are laid out.
- **Strategy:** the first strategy (symbol, timeframe, logic) and the interface live and backtest share (D-07). Every entry signal carries a stop price (D-22); whether the strategy manages an adopted position is open (D-19). Backtest fill model: slippage, fees, funding.
- **Later:** GUI `sandbox: false`, Discord bot stack and which alerts it sends, mainnet prerequisites (Ed25519 per D-11, IP whitelist, go-live criteria, mainnet risk values — D-23 to D-25 set testnet starting values only).

## Next steps

1. Order execution on testnet (GitHub issue #3):
   - Load `engine/config.toml` with its bounds (D-28), and apply margin type and leverage at startup (D-16).
   - Size entries from the base balance (D-22, D-23, D-25); market entries and `reduceOnly` exits (D-17); exchange-side stops as algo orders, including emergency stops for adopted positions (D-18, D-19, D-26); client IDs (D-21) and unknown-outcome lookups (D-20). Round prices and quantities to the symbol filters.
   - Keep local order/position state from user data events, resyncing from REST on every `UserStreamConnected` (D-13). Check real event payloads against `binanceNotes.md`, and parse `ALGO_UPDATE` along with the stop orders.
   - With no strategy yet, a testnet-only command runs entry → stop → `reduceOnly` exit → leftover stop cancel.
2. Risk management: today's loss from income history and the entry gate (D-24), and the kill switch with its halted state (D-27).
3. Backfill klines missed during stream reconnects (see Known issues in `progress.md`). A GitHub issue for this was drafted but the user put filing it on hold (2026-10-03).
