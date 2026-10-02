# Decisions

Design decisions and why they were made. Add new entries at the bottom. When a decision is reversed, mark the old entry `Superseded by D-XX` instead of deleting it.

Safety rules (`reduceOnly`, exchange-side stops, kill switch, key handling, …) live in CLAUDE.md, not here.

Entries dated `initial` were carried over from README.md. Where README gives no reason, **Why** says _not recorded_ — fill it in when known.

## D-01 Engine and GUI run as separate processes

- **Decision:** The Python engine and the Electron GUI are independent processes.
- **Why:** Closing the GUI must not stop trading.
- **Date:** initial

## D-02 Only the engine makes trading decisions

- **Decision:** Strategy evaluation and order placement happen only in the engine. The GUI and Discord bot send commands to the engine and never call Binance order endpoints themselves.
- **Why:** _not recorded_
- **Date:** initial

## D-03 WebSocket on localhost between engine and clients

- **Decision:** The engine talks to the GUI and the Discord bot over WebSocket on localhost.
- **Why:** _not recorded_
- **Date:** initial

## D-04 SQLite for records, Parquet for historical candles

- **Decision:** Trades, orders, and logs go in SQLite. Historical candle data goes in Parquet files.
- **Why:** _not recorded_
- **Date:** initial

## D-05 One-way position mode

- **Decision:** The account runs in one-way position mode, not hedge mode.
- **Why:** _not recorded_
- **Date:** initial

## D-06 The exchange is the source of truth

- **Decision:** On restart, the engine restores positions, open orders, and balances from Binance, not from its local records.
- **Why:** Local records can drift from the exchange (a missed fill, a crash mid-update); acting on stale state is dangerous with leverage.
- **Date:** initial

## D-07 Backtest and live share strategy code

- **Decision:** The backtest module runs the exact strategy code the live engine runs.
- **Why:** Backtest results then reflect what the live code actually does.
- **Date:** initial
