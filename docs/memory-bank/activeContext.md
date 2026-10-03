# Active Context

_Last updated: 2026-10-03_

## Current focus

The engine connects to testnet read-only, logs to `engine/logs/`, and receives the user data stream (GitHub issue #2). Order-execution design is settled (D-14 to D-22); the risk-limit decisions it depends on come next, then the order execution milestone. Work items are tracked as GitHub issues.

## Open questions

Order-execution design was settled on 2026-10-03 (D-14 to D-22). Still open, roughly in the order they're needed:

- **Risk limits (next):** daily loss limit (amount or percentage; day boundary UTC vs KST; whether unrealized PnL, fees, and funding count), maximum position size and leverage cap, how the engine restarts after the kill switch, the risk fraction per trade (D-22), and the emergency stop distance (D-19).
- **Where settings live:** strategy parameters and risk limits in `.env` or a separate config file.
- **Engine operation:** who starts the engine (manually, the GUI, a Windows service or scheduled task) and what restarts it after a crash.
- **Engine ↔ GUI protocol:** message types and schemas, localhost authentication, the initial state sent on connect, and which commands the GUI may send.
- **Storage:** what SQLite records; whether logs also go there (D-12); where historical candles come from and how Parquet files are laid out.
- **Strategy:** the first strategy (symbol, timeframe, logic) and the interface live and backtest share (D-07). Every entry signal carries a stop price (D-22); whether the strategy manages an adopted position is open (D-19). Backtest fill model: slippage, fees, funding.
- **Later:** GUI `sandbox: false`, Discord bot stack and which alerts it sends, mainnet prerequisites (Ed25519 per D-11, IP whitelist, go-live criteria).

## Next steps

1. Settle the risk-limit decisions (first item under Open questions). Order sizing (D-22), the emergency stop (D-19), and the leverage setting (D-16) need their values.
2. Order execution on testnet: entry, `reduceOnly` close, and exchange-side stop orders (algo orders). Round prices and quantities to the symbol filters. Keep local order/position state from user data events, resyncing from REST on every `UserStreamConnected` (D-13), and check real event payloads against `binanceNotes.md`. Parse `ALGO_UPDATE` along with the stop orders.
3. Backfill klines missed during stream reconnects (see Known issues in `progress.md`). A GitHub issue for this was drafted but the user put filing it on hold (2026-10-03).
