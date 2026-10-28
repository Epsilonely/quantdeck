// The engine's client protocol 1 (D-32, D-33; message shapes in docs/memory-bank/techContext.md)
// and the API the preload script gives the renderer. Types only, shared by main, preload, and
// renderer.
//
// Amounts are strings in fixed-point notation, as the engine sends them. Show them as they are;
// never parse them into numbers.

export interface HaltState {
  halted: boolean
  reason: string | null
  since: string | null
}

export interface DailyLoss {
  asset: string
  base: string
  loss: string // positive when the day lost money; the limit and the kill switch count it (D-24)
  limit: string
  // Display only: the open positions' unrealized PnL, and the loss with it counted. Null without
  // a position or before every position's mark price is in; engines before 2026-10-22 don't send
  // them.
  unrealized_pnl?: string | null
  loss_with_unrealized?: string | null
}

export interface Position {
  amount: string // negative = short
  entry_price: string
  unrealized_pnl: string | null
  // Display only: unrealized_pnl as a percent of the initial margin at entry (the price move
  // times the leverage). Null with unrealized_pnl; engines before 2026-10-26 don't send it.
  roi_pct?: string | null
}

export interface OpenOrder {
  client_id: string
  side: string
  type: string
  quantity: string
  reduce_only: boolean
}

export interface Stop {
  client_algo_id: string
  side: string
  type: string
  trigger_price: string
  close_position: boolean
}

export type StrategyAction = 'enter_long' | 'enter_short' | 'exit'

// What came of a signal. Only `entered` and `closed` carried it out.
export type SignalResult =
  | 'entered'
  | 'closed'
  | 'skipped_paused' // the user paused the symbol (D-50)
  | 'skipped_halted' // the engine halted the symbol or the account
  | 'ignored' // an exit with no position, or an entry with one open
  | 'not_entered' // sized out, not filled, or closed for want of a stop; the log says which
  | 'failed' // a request error or an unclear order outcome

export interface StrategySignal {
  candle_open_time: string
  action: StrategyAction
  stop_price: string | null // an entry's stop, on the tick as the engine places it
  result?: SignalResult // engines before 2026-10-26 don't send it
  outcome: string // the engine's text, as in its forward test record
}

// A candle on a chart (D-54): prices as text, like every amount.
export interface ChartCandle {
  open_time: string
  open: string
  high: string
  low: string
  close: string
}

// A symbol's strategy (D-47) and what it last did (D-43). Engines before 2026-10-20 had one
// strategy for every symbol and don't send name, interval, or params here.
export interface SymbolStrategy {
  name?: string
  interval?: string
  params?: Record<string, string> // every parameter, as text
  last_candle: { open_time: string; close: string } | null // the last candle decided on
  last_signal: StrategySignal | null
  stopped: string | null // why this symbol's strategy stopped
  // The chart's candle still forming on the strategy's interval (D-54); null before the stream
  // sends one. Engines before 2026-10-28 don't send it.
  forming_candle?: ChartCandle | null
}

export interface SymbolState {
  symbol: string
  halted: string | null // the engine's own halt of this symbol (D-16, D-19, D-20)
  // The user's pause (D-50): when it began, or null. Engines before 2026-10-22 don't send it and
  // can't pause a symbol.
  paused_at?: string | null
  leverage?: number // engines before 2026-10-20 don't send it
  strategy: SymbolStrategy | null // null without a strategy: no entries
  mark_price: string | null
  position: Position | null
  orders: OpenOrder[]
  stops: Stop[]
}

export interface EngineState {
  network: 'testnet' | 'mainnet'
  started_at?: string // when this engine started; engines before 2026-10-19 don't send it
  halt: HaltState
  daily_loss: DailyLoss | null
  balances: Record<string, string>
  symbols: SymbolState[]
}

export interface LogRecord {
  time: string
  level: string
  logger: string
  message: string
}

// What an order was for, from its client order ID (D-21): entry, exit (strategy exits and the
// user's close), stop, kill switch; liquidation and ADL by the exchange; other for an order the
// engine didn't place or whose role couldn't be found.
export type TradeRole = 'entry' | 'exit' | 'stop' | 'kill' | 'liquidation' | 'adl' | 'other'

// One order's fills together (D-51).
export interface TradeRow {
  time: string // the first fill
  symbol: string
  order_id: number
  client_id: string | null // the ID that gave the role: the order's own, or its stop's
  role: TradeRole
  side: string // BUY, SELL
  quantity: string
  price: string // the average fill price
  realized_pnl: string // in RecentTrades.asset
  fees: Record<string, string> // by asset
  fills: number
}

// The `trades` message (D-51): every order filled in the last `window_days` days, newest first.
// Engines before 2026-10-22 never send it.
export interface RecentTrades {
  window_days: number
  asset: string // the margin asset, which realized PnL is in
  totals: { realized_pnl: string; fees: Record<string, string> }
  trades: TradeRow[]
}

// A `candles` message (D-54): one symbol's last closed candles on its strategy's interval,
// oldest first, the candles the strategy decided on. Sent per symbol with a strategy when a
// client connects and whenever that list changes. Engines before 2026-10-28 never send it.
export interface SymbolCandles {
  symbol: string
  interval: string
  candles: ChartCandle[]
}

// What `set_symbol` accepts, sent with `welcome` (D-48). Engines before 2026-10-20 don't send
// it. Parameter defaults are text, like parameter values everywhere.
export type ParameterType = 'int' | 'decimal' | 'bool'

export interface ParameterSpec {
  name: string
  type: ParameterType
  default: string
  description?: string // help in Korean, shown next to the parameter; older engines send none
}

export interface StrategySpec {
  name: string
  interval: string // its default interval
  params: ParameterSpec[]
}

export interface StrategyCatalog {
  max_leverage: number
  max_symbols: number
  intervals: string[]
  strategies: StrategySpec[]
}

// A symbol's strategy setting as `set_symbol` takes it (D-47).
export interface StrategySetting {
  name: string
  interval: string
  params: Record<string, string>
}

export interface SetSymbolArgs {
  symbol: string
  leverage: number
  strategy: StrategySetting | null // null: no strategy, no entries
}

// The arguments of the commands that name a symbol and nothing else.
export interface SymbolArgs {
  symbol: string
}

export const COMMAND_NAMES = [
  'emergency_close',
  'lift_halt',
  'stop_engine',
  'set_symbol',
  'remove_symbol',
  'pause_symbol',
  'close_symbol',
  'resume_symbol'
] as const
export type CommandName = (typeof COMMAND_NAMES)[number]

// remove_symbol (D-47); pause, close and pause, and resume one symbol (D-50).
export const SYMBOL_ONLY_COMMANDS = [
  'remove_symbol',
  'pause_symbol',
  'close_symbol',
  'resume_symbol'
] as const
export type SymbolOnlyCommand = (typeof SYMBOL_ONLY_COMMANDS)[number]

export type EngineCommand =
  | { name: 'emergency_close' }
  | { name: 'lift_halt' }
  | { name: 'stop_engine' }
  | { name: 'set_symbol'; args: SetSymbolArgs }
  | { name: SymbolOnlyCommand; args: SymbolArgs }

export interface CommandResult {
  ok: boolean
  message: string
}

export interface ConnectionStatus {
  phase: 'connecting' | 'connected' | 'disconnected'
  detail: string | null // why it is disconnected
  since: string // ISO 8601 UTC
}

export interface LogsUpdate {
  reset: boolean // true: replace every record shown so far (a new connection's backlog)
  records: LogRecord[]
}

// The engine process this GUI started (D-34).
export interface LaunchState {
  phase: 'idle' | 'starting' | 'failed'
  message: string | null // why it failed
}

export interface EngineSnapshot {
  status: ConnectionStatus
  launch: LaunchState
  state: EngineState | null // the last state received; stale while disconnected
  logs: LogRecord[]
  catalog: StrategyCatalog | null // from the last welcome; null if that engine sent none
  trades: RecentTrades | null // null until this connection's engine sends them
  candles: Record<string, SymbolCandles> // by symbol; empty until this connection's engine sends them
}

export interface EngineApi {
  snapshot(): Promise<EngineSnapshot>
  // The main process checks the command, asks the user to confirm with what it will send, and
  // sends it; null means the user cancelled.
  command(command: EngineCommand): Promise<CommandResult | null>
  startEngine(): Promise<void>
  onStatus(listener: (status: ConnectionStatus) => void): () => void
  onState(listener: (state: EngineState) => void): () => void
  onLogs(listener: (update: LogsUpdate) => void): () => void
  onLaunch(listener: (launch: LaunchState) => void): () => void
  onCatalog(listener: (catalog: StrategyCatalog | null) => void): () => void
  onTrades(listener: (trades: RecentTrades | null) => void): () => void
  onCandles(listener: (candles: Record<string, SymbolCandles>) => void): () => void
}
