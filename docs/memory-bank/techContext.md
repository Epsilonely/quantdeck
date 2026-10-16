# Tech Context

Build and run commands are in CLAUDE.md. Exact package versions are in `gui/package.json`.

## Stack

| Component | Technology | Status |
|---|---|---|
| GUI | Electron, React, TypeScript, electron-vite, electron-builder | Connects to the engine (D-32) and shows its state and logs; sends the two commands |
| Charts | lightweight-charts | Planned, not installed |
| Engine | Python 3.12+ (3.13 pinned locally), uv, pytest, ruff (D-09) | Settings, logging, order execution with exchange-side stops, daily loss limit and kill switch, `run` without a strategy (D-31) serving clients (D-32, D-33) |
| Binance client | In-house, asyncio on httpx + websockets (D-10) | REST including orders and algo orders, kline stream, user data stream |
| Discord bot | Undecided | Not started |
| Storage | SQLite (trades, orders, logs), Parquet (historical candles) | Planned; diagnostic logs go to files (D-12), and the kill-switch halt record is `engine/state/halt.json` (D-29) |
| Engine ↔ clients | WebSocket on `127.0.0.1`, token from a connection file (D-32) | Engine server and GUI client done |
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

Decided in D-32 (connection and authentication) and D-33 (messages and commands). The engine side is implemented (protocol 1); the GUI client isn't yet.

**Connecting.** While `run` is serving, `engine/state/connection.json` holds `{"protocol": 1, "port": <int>, "token": "<string>"}`. The port and token change at every start; re-read the file before each reconnect. The server listens on `127.0.0.1` only and refuses any handshake that carries an `Origin` header (HTTP 403). Node's built-in `WebSocket` sends none (checked with Node 24 and Electron 39's main process, 2026-10-16).

Every message is a JSON object with a `type`. Amounts are strings in fixed-point notation (never `1E+3`); times are ISO 8601 UTC.

| Direction | `type` | Fields |
|---|---|---|
| client → engine | `hello` | `token`. Must be the first message, within 5 s; otherwise the engine closes with code 1008 |
| engine → client | `welcome` | `protocol` |
| engine → client | `logs` | `records`: list of `{time, level, logger, message}`. First the backlog (up to 200, INFO and above), then each new record as it is logged |
| engine → client | `state` | `state`: the whole state (below). Sent after `welcome`, then whenever it changes (checked every 0.25 s) |
| client → engine | `command` | `id` (any JSON value, echoed back), `name`: `emergency_close` or `lift_halt` |
| engine → client | `result` | `id`, `ok` (bool), `message` (text for the user) |

After `hello` the first three messages are `welcome`, `logs`, `state`, in that order.

**State.**

```
network      "testnet" | "mainnet"
halt         {halted: bool, reason: str | null, since: str | null}
daily_loss   null until the first check, then {asset, base, loss, limit}
balances     {asset: wallet balance}, nonzero only
symbols      [{symbol, halted: str | null (this symbol's own halt, D-19/D-20),
               mark_price: str | null,
               position: null | {amount (negative = short), entry_price, unrealized_pnl: str | null},
               orders: [{client_id, side, type, quantity, reduce_only}],
               stops:  [{client_algo_id, side, type, trigger_price, close_position}]}]
```

**Commands.** `emergency_close` fires the kill switch (D-27); `ok` is false if a symbol couldn't be flattened. `lift_halt` answers `ok: true` with "halt lifted" or "not halted", and `ok: false` while today's loss is still at the limit.

A client whose unsent messages pile up past 1000 is disconnected.

## GUI notes

- `BrowserWindow` is created with `sandbox: false` (the electron-vite template default). Revisit when hardening the GUI.
- The main process finds the connection file relative to the app path, which works only when run from source (`npm run dev`). A packaged app needs another way to find `engine/state/`.
- The preload script exposes only `window.api.engine` (snapshot, the two commands, and status/state/logs listeners); the template's general `window.electron` IPC access was removed. The main process accepts only the two command names and asks for confirmation in a native dialog before sending either.
- Electron 39's built-in `WebSocket` (main process, checked 2026-10-16) differs from Node 24's:
  - A refused connection fires `error` and never `close`, so the client treats either event as the end of the connection.
  - `close()` on a socket that is still connecting fires `error` again synchronously; calling `close()` from the `error` handler recursed until the stack overflowed and stalled the main process. Close only a socket whose `readyState` is `OPEN`.
  - The engine's 1008 refusal (wrong token) arrives as `close` with code 1008 and no `error`.
