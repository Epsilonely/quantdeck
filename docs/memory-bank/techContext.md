# Tech Context

Build and run commands are in CLAUDE.md. Exact package versions are in `gui/package.json`.

## Stack

| Component | Technology | Status |
|---|---|---|
| GUI | Electron, React, TypeScript, electron-vite, electron-builder | Scaffolded (template) |
| Charts | lightweight-charts | Planned, not installed |
| Engine | Python 3.12+ (3.13 pinned locally), uv, pytest, ruff (D-09) | Scaffolded |
| Binance client | In-house, asyncio on httpx + websockets (D-10) | Read-only REST and kline stream |
| Discord bot | Undecided | Not started |
| Storage | SQLite (trades, orders, logs), Parquet (historical candles) | Planned |
| Engine ↔ clients | WebSocket on localhost | Planned |
| Exchange | Binance USDⓈ-M Futures | Planned |

## Requirements

- Node.js 22+, npm 11+
- Python 3.12+ (engine)
- Development machine: Windows 11

## Layout

```
quantdeck/
├─ gui/      Electron + React + TypeScript app (electron-vite)
├─ engine/   Python trading engine — uv project, src layout, package `quantdeck_engine`
├─ bot/      Discord bot — not created yet
└─ docs/     Design docs and this memory bank
```

## Environment variables

Defined in the root `.env` (gitignored); names are listed in `.env.example`. Never record values here. Real environment variables override `.env`. The engine reads them only through `quantdeck_engine.config`.

| Variable | Used by | Notes |
|---|---|---|
| `BINANCE_API_KEY`, `BINANCE_API_SECRET` | Engine only (D-08) | No withdrawal permission; IP whitelist where possible |
| `BINANCE_TESTNET` | Engine | Unset or `true` → testnet; exactly `false` → mainnet; any other value (even `True`) stops the engine |
| `DISCORD_BOT_TOKEN` | Bot | Optional |
| `DISCORD_ALLOWED_USER_ID` | Bot | Commands from any other user are ignored |

## Engine ↔ GUI / bot protocol

Not designed yet. Once defined, record the message types, their direction, and payload shapes here.

## GUI notes

- `BrowserWindow` is created with `sandbox: false` (the electron-vite template default). Revisit when hardening the GUI.
