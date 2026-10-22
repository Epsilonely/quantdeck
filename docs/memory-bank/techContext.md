# Tech Context

Build and run commands are in CLAUDE.md. Exact package versions are in `gui/package.json`.

## Stack

| Component | Technology | Status |
|---|---|---|
| GUI | Electron, React, TypeScript, electron-vite, electron-builder | Connects to the engine (D-32) and shows its state and logs; sends the commands; starts the engine when none is running (D-34); symbol settings panel (D-48); light/dark theme (D-35) |
| Fonts | Pretendard, JetBrains Mono, bundled from npm (D-49) | In use |
| Charts | lightweight-charts | Planned, not installed |
| Engine | Python 3.12+ (3.13 pinned locally), uv, pytest, ruff (D-09) | Settings, logging, order execution with exchange-side stops, daily loss limit and kill switch, `run` (D-31) with a strategy per symbol (D-43, D-47) serving clients (D-32, D-33), backtests (D-37 to D-40, D-45) |
| Binance client | In-house, asyncio on httpx + websockets (D-10) | REST including orders and algo orders, kline stream, user data stream |
| Discord bot | Undecided | Not started |
| Storage | SQLite (trades, orders, logs); historical candles as Binance's downloaded zip files in `engine/data/`, Parquet deferred (D-38) | Planned; diagnostic logs go to files (D-12), the kill-switch halt record is `engine/state/halt.json` (D-29), and the symbols with their settings and the user's pauses are `engine/state/symbols.json` (D-47, D-50) |
| Engine ↔ clients | WebSocket on `127.0.0.1`, token from a connection file (D-32) | Engine server and GUI client done |
| Exchange | Binance USDⓈ-M Futures, HMAC keys (D-11) | Testnet; `run` has traded strategies there since 2026-10-21 |

## Requirements

- Node.js 22+, npm 11+
- Python 3.12+ and uv (engine)
- Development machine: Windows 11
- GitHub CLI (`gh`) for issues, logged in as the repo owner

## Layout

```
quantdeck/
├─ gui/      Electron + React + TypeScript app (electron-vite)
├─ engine/   Python trading engine — uv project, src layout, package `quantdeck_engine`
├─ bot/      Discord bot — not created yet
└─ docs/     Design docs and this memory bank
```

## Environment variables

Defined in the root `.env` (gitignored); names are listed in `.env.example`. Never record values here. Real environment variables override `.env`. The engine reads them only through `quantdeck_engine.config`. Risk settings live in `engine/config.toml` instead (D-28): gitignored, created by copying `engine/config.example.toml`. `check` doesn't need it; `trial` does. The symbols, their leverage, and their strategies live in `engine/state/symbols.json`, written only by the engine; the first `run` or `trial` creates it from `config.toml`'s `[symbols]` and `[strategy]` (D-47).

| Variable | Used by | Notes |
|---|---|---|
| `BINANCE_API_KEY`, `BINANCE_API_SECRET` | Engine only (D-08) | No withdrawal permission; IP whitelist where possible |
| `BINANCE_TESTNET` | Engine | Unset or `true` → testnet; exactly `false` → mainnet; any other value (even `True`) stops the engine |
| `ENGINE_LOG_LEVEL` | Engine | `DEBUG`, `INFO`, `WARNING`, `ERROR` (case-insensitive); unset → `INFO`; anything else stops the engine. `DEBUG` adds one line per REST request |
| `DISCORD_BOT_TOKEN` | Bot | Optional |
| `DISCORD_ALLOWED_USER_ID` | Bot | Commands from any other user are ignored |

## Engine logs

- Written to the console and to `engine/logs/` (gitignored), wherever the engine is started from. Timestamps are the machine's local time with the UTC offset, e.g. `2026-10-16T16:41:50.908+09:00` (D-12 note).
- File name: `engine_<local start time>_<index>.log`, index from `000`. Past 10 MB the next record opens index + 1; a new local date opens a new start time at `000` (D-12).
- Files last written more than 30 days ago are deleted at startup.

## Engine ↔ GUI / bot protocol

Decided in D-32 (connection and authentication) and D-33 (messages and commands). The engine and the GUI client both implement protocol 1.

**Connecting.** While `run` is serving, `engine/state/connection.json` holds `{"protocol": 1, "port": <int>, "token": "<string>"}`. The port and token change at every start; re-read the file before each reconnect. The server listens on `127.0.0.1` only and refuses any handshake that carries an `Origin` header (HTTP 403). Node's built-in `WebSocket` sends none (checked with Node 24 and Electron 39's main process, 2026-10-16).

Every message is a JSON object with a `type`. Amounts are strings in fixed-point notation (never `1E+3`); times are ISO 8601 UTC. The GUI shows them in local time.

| Direction | `type` | Fields |
|---|---|---|
| client → engine | `hello` | `token`. Must be the first message, within 5 s; otherwise the engine closes with code 1008 |
| engine → client | `welcome` | `protocol`, `catalog` (below; D-48) |
| engine → client | `logs` | `records`: list of `{time, level, logger, message}`. First the backlog (up to 200, INFO and above), then each new record as it is logged |
| engine → client | `state` | `state`: the whole state (below). Sent after `welcome`, then whenever it changes (checked every 0.25 s) |
| client → engine | `command` | `id` (any JSON value, echoed back), `name`: `emergency_close`, `lift_halt`, `stop_engine`, `set_symbol`, `remove_symbol`, `pause_symbol`, `close_symbol`, or `resume_symbol`; `args` (an object, optional, used by the symbol commands) |
| engine → client | `result` | `id`, `ok` (bool), `message` (text for the user) |

After `hello` the first three messages are `welcome`, `logs`, `state`, in that order.

**State.**

```
network      "testnet" | "mainnet"
started_at   when this engine started; the GUI shows the engine's uptime from it
halt         {halted: bool, reason: str | null, since: str | null}
daily_loss   null until the first check, then {asset, base, loss, limit,
               unrealized_pnl: str | null, loss_with_unrealized: str | null}
balances     {asset: wallet balance}, nonzero only
symbols      [{symbol, halted: str | null (the engine's own halt of it, D-16/D-19/D-20),
               paused_at: str | null (the user's pause, D-50),
               leverage: int,
               strategy: null | {name, interval, params: {name: str},
                                 last_candle: null | {open_time, close},
                                 last_signal: null | {candle_open_time, action, stop_price: str | null, outcome},
                                 stopped: str | null},
               mark_price: str | null,
               position: null | {amount (negative = short), entry_price, unrealized_pnl: str | null},
               orders: [{client_id, side, type, quantity, reduce_only}],
               stops:  [{client_algo_id, side, type, trigger_price, close_position}]}]
```

Strategy fields (D-43, D-47): `params` holds every parameter, as strings like amounts. `action` is `enter_long`, `enter_short`, or `exit`; `outcome` is text for the user, and `stopped` says why this symbol's strategy stopped. `unrealized_pnl` is every open position's unrealized PnL at the display-only mark prices, before closing fees, and `loss_with_unrealized` is `loss` minus it, both rounded to 0.01; they are null with no position open or before every position's mark price has arrived, since a partial total would mislead, and only the GUI uses them (D-24 note of 2026-10-22). Fields are added without changing the protocol version (the strategy fields, `leverage`, `paused_at`, the unrealized fields, and `started_at` all were), so a client must accept any of them missing from an older engine; the GUI takes a missing `paused_at` to mean the engine can't pause a symbol.

**Catalog** (D-48). What `set_symbol` accepts, built from the strategy registry. Like the state's added fields, `catalog` and `description` may be missing from an older engine; the GUI then turns the settings off or shows no `?`.

```
max_leverage  int
max_symbols   int
intervals     [str]  the kline intervals, e.g. "4h"
strategies    [{name, interval (its default), params: [{name, type: "int" | "decimal" | "bool", default: str, description: str}]}]
```

Defaults are text, like parameter values. `description` is the parameter's help in Korean, which the settings panel shows when the pointer is over the `?` after the name; it is declared with the parameter in the strategy's code, and a test fails if any parameter lacks one. Whether a setting enters with limit orders depends on its values, so the catalog doesn't say; `set_symbol` refuses those (D-45).

**Commands.** `emergency_close` fires the kill switch (D-27); `ok` is false if a symbol couldn't be flattened. `lift_halt` answers `ok: true` with "halt lifted" or "not halted", and `ok: false` while today's loss is still at the limit. `stop_engine` (D-34) answers `ok: true` once any kill switch in progress has finished; the engine then closes every connection with code 1001 and deletes the connection file. The process itself exits a few seconds later, after its Binance streams close.

`set_symbol` (D-47) takes `args` `{symbol, leverage: int, strategy: null | {name, interval, params: {name: str}}}`: a new symbol is added, a known one changed; left-out parameters take their defaults. It answers `ok: false` with the reason when a value is out of bounds, the symbol can't be traded, or it has an open position. `remove_symbol` takes `{symbol}` and is refused the same way while the symbol has a position. A refused or failed change has changed nothing.

`pause_symbol`, `close_symbol`, and `resume_symbol` take `{symbol}`; what they answer and how they fail is in D-50. `resume_symbol`'s message also names any halt that still blocks entries.

A client whose unsent messages pile up past 1000 is disconnected.

## GUI notes

- `BrowserWindow` is created with `sandbox: false` (the electron-vite template default). Revisit when hardening the GUI.
- The main process finds `engine/` relative to the app path, which works only when run from source (`npm run dev`). A packaged app needs another way to find the connection file and the engine's virtualenv.
- Closing a terminal tab of the Claude desktop app ends the tab's whole process tree, detached processes included, as long as their parent is alive; a detached process whose parent had already exited survived (2026-10-19). An engine started as Electron's direct child therefore died with the terminal running `npm run dev`, without its clean shutdown, and missed a 4h decision. Hence the starter in the D-34 note.
- The GUI starts the engine as `engine/.venv/Scripts/pythonw.exe -m quantdeck_engine run` (D-34), so the venv must exist (`uv sync`). That launcher is uv's trampoline, which runs the real interpreter as its child, so one engine shows as two `pythonw.exe` processes. The engine inherits Electron's inheritable handles: started from a GUI run with `--remoteDebuggingPort`, it kept that port bound after the GUI quit, and the next GUI couldn't open its debugging port (2026-10-16). Use a different port, or stop the engine, when testing over the DevTools protocol.
- The preload script exposes only `window.api.engine` (snapshot, the commands, starting the engine, and status/state/logs/launch/catalog listeners) and `window.api.theme` (get and set the theme); the template's general `window.electron` IPC access was removed. The main process checks and confirms every command before sending it (D-48).
- Screen checks without Electron or an engine, as in a worktree where neither may run (CLAUDE.md working rules): `npx electron-vite build`, copy `out/renderer/` elsewhere, add a `<script>` before the bundle that defines a fake `window.api` returning a made-up snapshot, and serve the copy on `127.0.0.1` for the browser pane.
- A stopping engine waits about 2 s for each Binance stream to close (`binanceNotes.md`, GitHub issue #20) before it releases the instance lock (D-31), so a start pressed meanwhile is refused and the GUI shows the lock error. A symbol change that replaces or removes a running strategy waits the same 2 s, holding that symbol's order lock.
- Theme (D-35): the choice is saved as `theme.json` in Electron's userData folder (`%APPDATA%\gui` when run from source). `main.css` keeps every color in `:root` variables, dark by default and light under `prefers-color-scheme: light`; new UI uses the variables, never literal colors. lightweight-charts takes colors in JavaScript, so charts will need to read the variables and redraw when the theme changes.
- Screen checks over the DevTools protocol: while the window is behind other windows, `document.visibilityState` is `hidden` and Chromium doesn't repaint, so a Win32 window capture (`PrintWindow`) shows the last painted frame while the title bar is current (2026-10-16). Take a `Page.captureScreenshot` first, which forces a paint. To catch an animation mid-way, slow it with `Animation.setPlaybackRate` in the same DevTools session.
- Electron 39's built-in `WebSocket` (main process, checked 2026-10-16) differs from Node 24's:
  - A refused connection fires `error` and never `close`, so the client treats either event as the end of the connection.
  - `close()` on a socket that is still connecting fires `error` again synchronously; calling `close()` from the `error` handler recursed until the stack overflowed and stalled the main process. Close only a socket whose `readyState` is `OPEN`.
  - The engine's 1008 refusal (wrong token) arrives as `close` with code 1008 and no `error`.
