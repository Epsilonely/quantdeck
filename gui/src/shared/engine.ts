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
  loss: string
  limit: string
}

export interface Position {
  amount: string // negative = short
  entry_price: string
  unrealized_pnl: string | null
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

// The strategy the engine trades on every symbol (D-43), from engine/config.toml.
export interface StrategyInfo {
  name: string
  interval: string
  params: Record<string, string>
}

export type StrategyAction = 'enter_long' | 'enter_short' | 'exit'

export interface StrategySignal {
  candle_open_time: string
  action: StrategyAction
  stop_price: string | null // an entry's stop
  outcome: string
}

export interface SymbolStrategy {
  last_candle: { open_time: string; close: string } | null // the last candle decided on
  last_signal: StrategySignal | null
  stopped: string | null // why this symbol's strategy stopped
}

export interface SymbolState {
  symbol: string
  halted: string | null // this symbol's own halt (D-19, D-20)
  strategy: SymbolStrategy | null // null without a strategy
  mark_price: string | null
  position: Position | null
  orders: OpenOrder[]
  stops: Stop[]
}

export interface EngineState {
  network: 'testnet' | 'mainnet'
  started_at?: string // when this engine started; engines before 2026-10-19 don't send it
  strategy: StrategyInfo | null // null: no entries
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

export const COMMAND_NAMES = ['emergency_close', 'lift_halt', 'stop_engine'] as const
export type CommandName = (typeof COMMAND_NAMES)[number]

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
}

export interface EngineApi {
  snapshot(): Promise<EngineSnapshot>
  // The main process asks the user to confirm first; null means the user cancelled.
  command(name: CommandName): Promise<CommandResult | null>
  startEngine(): Promise<void>
  onStatus(listener: (status: ConnectionStatus) => void): () => void
  onState(listener: (state: EngineState) => void): () => void
  onLogs(listener: (update: LogsUpdate) => void): () => void
  onLaunch(listener: (launch: LaunchState) => void): () => void
}
