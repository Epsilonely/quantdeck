# Active Context

_Last updated: 2026-10-02_

## Current focus

Project setup is done: README, `.env` handling (`.gitignore`, `.env.example`), CLAUDE.md, and this memory bank. No feature work has started. Next up is the first engine milestone.

## Open questions

- **Does the GUI need Binance API keys at all?** Everything is meant to go through the engine, so the GUI may only need its WebSocket connection to the engine. If so, the roadmap item "move API key handling to the main process" becomes "the GUI never handles keys".
- **Python tooling for `engine/`:** dependency manager (uv, poetry, or pip + venv), test runner, and Binance client (official connector, ccxt, or raw REST/WebSocket) — undecided.
- **Engine ↔ GUI message protocol:** message types and schemas are undefined.
- **First strategy:** not chosen.

## Next steps

1. Answer the GUI API key question above.
2. Create `engine/` with the Python toolchain chosen above.
3. Engine: connect to Binance testnet — market data stream, position and balance queries.
