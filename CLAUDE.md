# QuantDeck

Personal automated trading system for Binance USDⓈ-M Futures. A Python engine (`engine/`) makes every trading decision; the Electron GUI (`gui/`) and a Discord bot (planned) only monitor the engine and send it commands. README.md has the full overview.

## Memory bank

Project context lives in `docs/memory-bank/`. These two files are loaded every session:

@docs/memory-bank/activeContext.md
@docs/memory-bank/progress.md

Read the others when the task touches their area:

| File | Contents | Read before |
|---|---|---|
| `projectbrief.md` | Purpose, scope, non-goals | Adding features or changing scope |
| `decisions.md` | Design decisions and their reasons | Changing architecture, storage, or communication |
| `techContext.md` | Stack, layout, env vars, engine↔GUI protocol | Setup, new dependencies, new env vars |
| `binanceNotes.md` | Verified Binance API behavior and open checks | Any Binance API work |

### Memory bank rules

- **Write every file in `docs/memory-bank/` in English**, even when the conversation is in Korean. Keep code identifiers, API names, and error codes verbatim.
- Record only what the code can't tell you: current state, decisions and their reasons, constraints, exchange behavior. Don't list classes, methods, or files, and don't copy code — that goes stale.
- One fact, one place. Before writing, check whether another file already states it; update that file instead of repeating it.
- `activeContext.md` describes the present. Overwrite stale content instead of appending session logs — history lives in git.
- Update the memory bank when a roadmap item finishes, a design decision is made, a Binance behavior is verified, or the user asks. Read all memory-bank files first, and fix any contradictions you find.
- When a roadmap item is done, check it off in both `progress.md` and `README.md`.
- Use absolute dates (YYYY-MM-DD).
- Don't create `systemPatterns.md` until engine code exists with patterns worth recording.

## Safety principles

These apply to all code in this repo. If a task would break one, stop and ask the user first. They mirror the 안전 원칙 section in README.md — change both together.

1. Binance API keys are handled only by the engine. Never pass them to the GUI (main or renderer) or the Discord bot, and never include them in a bundle.
2. API keys never get withdrawal permission; use an IP whitelist where possible.
3. Testnet is the default. Switching to mainnet requires an explicit config change.
4. Only the engine decides on and places orders. The GUI and Discord bot only send commands to the engine.
5. Close orders always use `reduceOnly`, and exchange-side stops use `closePosition` (one-way position mode), so a close can never open a reverse position.
6. Stop-loss orders are placed on the exchange up front, so they fire even if the engine is down.
7. Kill switch: when the daily loss limit is exceeded, cancel all orders, close all positions, and halt trading until the user lifts the halt. The engine keeps running so it can still be monitored.
8. The exchange is the source of truth for positions and balances. On restart, restore state from Binance, not from local records.
9. Discord commands are limited to queries and stop/close actions. Ignore commands from anyone except the allowed user ID.

## Working rules

- Engine code gets settings only from `quantdeck_engine.config` (`load_settings()` at startup). Never read `os.environ` elsewhere, and keep keys wrapped in `Secret` until the moment they're used.
- Prices, quantities, balances, and PnL are `Decimal`, never `float`. Binance sends them as strings; parse them with `Decimal(str_value)`. Float rounding breaks tick/step-size checks and order sizing.
- Log request paths, never full URLs: a signed query string carries the signature, and user data stream URLs carry the `listenKey`. For the same reason the `httpx`, `httpcore`, and `websockets` loggers stay at WARNING even when the engine logs at DEBUG.
- Never print, log, or commit values from `.env`. `.env.example` lists the variable names; when adding a variable, add it there too with an empty value.
- Risk settings go in `engine/config.toml` (gitignored), not `.env` (D-28). When adding one, add it to `engine/config.example.toml` too, and give a risk value a hard bound in `config.py`. The symbols and each one's leverage and strategy live in `engine/state/symbols.json`, which only the engine writes (D-47); never edit it while an engine runs.
- For Binance API details, check `docs/memory-bank/binanceNotes.md` and the official docs instead of relying on memory — the API changes.
- Talk to the user in their language (usually Korean). This doesn't change the English-only rule for the memory bank.
- Track work as GitHub issues (`gh`). Write issue titles and bodies in Korean, keeping code identifiers, API names, and paths verbatim. The repo is public: never put balances, keys, or raw log contents in an issue.
- Commits: tag the related issue in the message: the title ends with `(#N)`, and the body ends with `Refs #N`, or `Closes #N` when the commit finishes the issue. Before committing, show the user the plan (files, title, body) and commit only after they approve it.

## Commands

GUI — run in `gui/`:

- `npm run dev` — dev mode with HMR
- `npm run lint` — ESLint
- `npm run typecheck` — tsc for main/preload and renderer
- `npm run build` — typecheck, then electron-vite build
- `npm run build:win` — Windows installer via electron-builder

Engine — run in `engine/`:

- `uv sync` — create `.venv` and install the locked dependencies
- `uv run pytest` — tests
- `uv run ruff check .` — lint
- `uv run ruff format .` — format
- `uv run quantdeck-engine run` — TESTNET ONLY: the long-running engine until Ctrl+C or a `stop_engine` command (D-31, D-34). The GUI also starts it, hidden, when none is running. It trades each symbol in `engine/state/symbols.json` with that symbol's strategy and places orders (D-43, D-47), so ask the user before starting it; a symbol without a strategy places no entries. The first `run` creates `symbols.json` from `config.toml`'s `[symbols]` and `[strategy]`. It adopts and protects positions, fires the kill switch at the daily limit, and serves the GUI over WebSocket (D-32, D-33). Only one `run` (or `trial`) at a time
- `uv run quantdeck-engine check` — read-only connection check against the configured network (places no orders)
- `uv run quantdeck-engine trial` — TESTNET ONLY, places orders: entry → stop → emergency stop → close, then a second entry closed by the kill switch, once. Its halt record goes to a temporary directory. Needs `engine/config.toml` and a flat symbol from `symbols.json` (the first, or `--symbol`); refuses mainnet and a halted engine. Ask the user before running it
- `uv run quantdeck-engine backtest --strategy donchian|bollinger` — runs a strategy over mainnet history (D-37 to D-40); places no orders. Downloads archive files into `engine/data/` on first use, and reads symbol rules and leverage brackets from the configured network, so it needs `.env` and `engine/config.toml`. `--help` lists the options
- `uv run quantdeck-engine lift-halt` — lift the kill switch's halt by deleting `engine/state/halt.json` (D-29). Only the user decides to lift a halt: never run it on your own
- Client commands to a running engine (D-33): `emergency_close` fires the kill switch and writes the real halt record; `lift_halt` lifts it. Ask the user before sending either, including from a test script. `stop_engine` (D-34) stops the engine like Ctrl+C; ask before stopping an engine the user started. `set_symbol` and `remove_symbol` (D-47) change what the engine trades and save it to `symbols.json`; a symbol given a strategy decides on its latest closed candle at once and can enter, so ask before sending either. `pause_symbol`, `close_symbol`, and `resume_symbol` (D-50) pause one symbol, close its position at market and pause it, or lift the pause, saved to `symbols.json`; a close places orders and a resume lets the symbol enter again, so ask before sending any of them. Connecting and reading state and logs is fine
- `uv add <pkg>` / `uv add --dev <pkg>` — add a dependency (updates `uv.lock`; commit both files)

Bot: not implemented yet.
