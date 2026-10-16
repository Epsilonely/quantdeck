# Tech Context

Build and run commands are in CLAUDE.md. Exact package versions are in `gui/package.json`.

## Stack

| Component | Technology | Status |
|---|---|---|
| GUI | Electron, React, TypeScript, electron-vite, electron-builder | Scaffolded (template) |
| Charts | lightweight-charts | Planned, not installed |
| Engine | Python 3.12+ (3.13 pinned locally), uv, pytest, ruff (D-09) | Settings, logging, order execution with exchange-side stops, daily loss limit and kill switch, `run` without a strategy (D-31) |
| Binance client | In-house, asyncio on httpx + websockets (D-10) | REST including orders and algo orders, kline stream, user data stream |
| Discord bot | Undecided | Not started |
| Storage | SQLite (trades, orders, logs), Parquet (historical candles) | Planned; diagnostic logs go to files (D-12), and the kill-switch halt record is `engine/state/halt.json` (D-29) |
| Engine ↔ clients | WebSocket on `127.0.0.1`, token from a connection file (D-32) | Designed, not implemented |
| Exchange | Binance USDⓈ-M Futures, HMAC keys (D-11) | Testnet; orders placed only by `trial` so far |

## Requirements

- Node.js 22+, npm 11+
- Python 3.12+ and uv (engine). On the development machine uv was installed with winget; a shell opened before that install needs a restart to find `uv`.
- Development machine: Windows 11
- GitHub CLI (`gh`) for issues, installed with winget on 2026-10-03 and logged in as the repo owner. Like `uv`, a shell opened before the install needs a restart to find it; until then use `C:\Program Files\GitHub CLI\gh.exe`.

## Layout

```
quantdeck/
├─ gui/      Electron + React + TypeScript app (electron-vite)
├─ engine/   Python trading engine — uv project, src layout, package `quantdeck_engine`
├─ bot/      Discord bot — not created yet
└─ docs/     Design docs and this memory bank
```

## Environment variables

Defined in the root `.env` (gitignored); names are listed in `.env.example`. Never record values here. Real environment variables override `.env`. The engine reads them only through `quantdeck_engine.config`. Strategy and risk settings live in `engine/config.toml` instead (D-28): gitignored, created by copying `engine/config.example.toml`. `check` doesn't need it; `trial` does.

| Variable | Used by | Notes |
|---|---|---|
| `BINANCE_API_KEY`, `BINANCE_API_SECRET` | Engine only (D-08) | No withdrawal permission; IP whitelist where possible |
| `BINANCE_TESTNET` | Engine | Unset or `true` → testnet; exactly `false` → mainnet; any other value (even `True`) stops the engine |
| `ENGINE_LOG_LEVEL` | Engine | `DEBUG`, `INFO`, `WARNING`, `ERROR` (case-insensitive); unset → `INFO`; anything else stops the engine. `DEBUG` adds one line per REST request |
| `DISCORD_BOT_TOKEN` | Bot | Optional |
| `DISCORD_ALLOWED_USER_ID` | Bot | Commands from any other user are ignored |

## Engine logs

- Written to the console and to `engine/logs/` (gitignored), wherever the engine is started from. Timestamps are UTC.
- File name: `engine_<UTC start time>_<index>.log`, index from `000`. Past 10 MB the next record opens index + 1; a new UTC date opens a new start time at `000` (D-12).
- Files last written more than 30 days ago are deleted at startup.

## Engine ↔ GUI / bot protocol

Decided in D-32 (connection and authentication) and D-33 (messages and commands); not implemented yet. Record the message types, their direction, and payload shapes here as they are implemented.

## GUI notes

- `BrowserWindow` is created with `sandbox: false` (the electron-vite template default). Revisit when hardening the GUI.
