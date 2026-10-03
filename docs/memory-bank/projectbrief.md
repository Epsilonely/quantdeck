# Project Brief

## What it is

QuantDeck is a personal automated trading system for Binance USDⓈ-M Futures.

- **Engine (Python):** receives market data, evaluates strategy signals, places orders, manages positions and risk. The only component that makes trading decisions.
- **GUI (Electron):** a cockpit for the engine — positions, PnL, engine logs, start/stop, emergency close, strategy settings.
- **Discord bot:** status queries and stop/close commands.

## Goals

- Run strategies automatically on Binance USDⓈ-M Futures, testnet first.
- Backtest with the same strategy code the live engine runs.
- Keep records of trades, orders, and daily PnL.

## Scope

In scope:

- Binance USDⓈ-M Futures, one-way position mode
- A single user running everything on their own machine

Out of scope:

- Other exchanges, spot, COIN-M futures
- Multiple users or hosted deployment
- Trading decisions anywhere other than the engine

## Constraints

- Personal learning and research project. Not investment advice; the user carries all risk.
- Leveraged futures can lose more than the principal quickly, so every feature is validated on testnet before it touches a real account.
- Public GitHub repository with no license: all rights reserved. Never commit secrets, balances, or account details — anyone can read the repo and its issues.
