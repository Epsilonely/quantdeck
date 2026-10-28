// The GUI's connection to the engine (D-32): the main process reads the connection file, connects
// with Node's built-in WebSocket (it sends no Origin header, so the engine accepts it), and keeps
// reconnecting. The renderer never sees the token or the file; it gets status, state, and logs
// through the callbacks below, which the main process relays over IPC.

import { readFile } from 'fs/promises'
import type {
  CommandResult,
  ConnectionStatus,
  EngineCommand,
  EngineSnapshot,
  EngineState,
  LogRecord,
  LogsUpdate,
  RecentTrades,
  StrategyCatalog,
  SymbolCandles
} from '../shared/engine'

const PROTOCOL = 1
const RETRY_DELAY_MS = 2000
const COMMAND_TIMEOUT_MS = 60_000 // the kill switch retries for a while before it answers
const LOG_CAPACITY = 1000

export interface EngineClientEvents {
  status(status: ConnectionStatus): void
  state(state: EngineState): void
  logs(update: LogsUpdate): void
  catalog(catalog: StrategyCatalog | null): void
  trades(trades: RecentTrades | null): void
  candles(candles: Record<string, SymbolCandles>): void
}

interface ConnectionInfo {
  port: number
  token: string
}

type EngineMessage =
  | { type: 'welcome'; protocol: number; catalog?: unknown }
  | { type: 'logs'; records: LogRecord[] }
  | { type: 'state'; state: EngineState }
  | ({ type: 'trades' } & Record<string, unknown>)
  | ({ type: 'candles' } & Record<string, unknown>)
  | { type: 'result'; id: unknown; ok: boolean; message: string }

export class EngineClient {
  private socket: WebSocket | null = null
  private retryTimer: NodeJS.Timeout | null = null
  private stopped = false
  private nextCommandId = 1
  private readonly pending = new Map<number, (result: CommandResult) => void>()
  private status: ConnectionStatus = { phase: 'connecting', detail: null, since: now() }
  private state: EngineState | null = null
  private logs: LogRecord[] = []
  private catalog: StrategyCatalog | null = null
  private trades: RecentTrades | null = null
  private candles: Record<string, SymbolCandles> = {} // replaced, never changed in place

  constructor(
    private readonly connectionFile: string,
    private readonly events: EngineClientEvents
  ) {}

  start(): void {
    void this.connect()
  }

  stop(): void {
    this.stopped = true
    if (this.retryTimer) clearTimeout(this.retryTimer)
    this.socket?.close()
    this.socket = null
  }

  snapshot(): Omit<EngineSnapshot, 'launch'> {
    return {
      status: this.status,
      state: this.state,
      logs: [...this.logs],
      catalog: this.catalog,
      trades: this.trades,
      candles: this.candles
    }
  }

  /** The command as it is, so the caller must have checked it (main/commands.ts). */
  command(command: EngineCommand): Promise<CommandResult> {
    const socket = this.socket
    if (!socket || this.status.phase !== 'connected') {
      return Promise.resolve({ ok: false, message: '엔진에 연결되어 있지 않습니다' })
    }
    const id = this.nextCommandId++
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.pending.delete(id)
        resolve({ ok: false, message: '엔진이 제때 답하지 않았습니다. 상태와 로그를 확인하세요' })
      }, COMMAND_TIMEOUT_MS)
      this.pending.set(id, (result) => {
        clearTimeout(timer)
        resolve(result)
      })
      socket.send(JSON.stringify({ type: 'command', id, ...command }))
    })
  }

  private async connect(): Promise<void> {
    if (this.stopped) return
    let info: ConnectionInfo
    try {
      // Re-read every time: the port and token change at every engine start (D-32).
      info = await readConnectionFile(this.connectionFile)
    } catch (e) {
      this.retry(e instanceof Error ? e.message : String(e))
      return
    }
    if (this.stopped) return

    const socket = new WebSocket(`ws://127.0.0.1:${info.port}`)
    this.socket = socket
    let welcomed = false
    let backlogPending = false

    socket.addEventListener('open', () => {
      socket.send(JSON.stringify({ type: 'hello', token: info.token }))
    })
    socket.addEventListener('message', (event) => {
      let message: EngineMessage
      try {
        message = JSON.parse(String(event.data))
      } catch {
        console.error('[engine] message is not JSON; ignored')
        return
      }
      switch (message.type) {
        case 'welcome':
          if (message.protocol !== PROTOCOL) {
            this.socket = null // so the close event doesn't replace the reason
            socket.close()
            this.retry(`엔진 프로토콜 ${message.protocol}, GUI는 ${PROTOCOL}만 지원합니다`)
            return
          }
          welcomed = true
          backlogPending = true
          // Before the status, so a screen that opens on "connected" finds the catalog.
          this.catalog = parseCatalog(message.catalog)
          this.events.catalog(this.catalog)
          // This engine sends its own trades and candles after its state, or none if it predates
          // them.
          this.trades = null
          this.events.trades(null)
          this.candles = {}
          this.events.candles(this.candles)
          this.setStatus('connected', null)
          break
        case 'logs':
          if (backlogPending) {
            // The first logs after welcome are the engine's backlog, which overlaps what an
            // earlier connection already showed: start over with it.
            backlogPending = false
            this.logs = message.records.slice(-LOG_CAPACITY)
            this.events.logs({ reset: true, records: this.logs })
          } else {
            this.logs.push(...message.records)
            if (this.logs.length > LOG_CAPACITY)
              this.logs.splice(0, this.logs.length - LOG_CAPACITY)
            this.events.logs({ reset: false, records: message.records })
          }
          break
        case 'state':
          this.state = message.state
          this.events.state(message.state)
          break
        case 'trades': {
          const trades = parseTrades(message)
          if (trades) {
            this.trades = trades
            this.events.trades(trades)
          }
          break
        }
        case 'candles': {
          const candles = parseCandles(message)
          if (candles) {
            this.candles = { ...this.candles, [candles.symbol]: candles }
            this.events.candles(this.candles)
          }
          break
        }
        case 'result':
          if (typeof message.id === 'number') {
            this.pending.get(message.id)?.({ ok: message.ok, message: message.message })
            this.pending.delete(message.id)
          }
          break
      }
    })
    const lost = (reason: string): void => {
      if (this.socket !== socket) return // already handled
      this.socket = null
      for (const resolve of this.pending.values()) {
        resolve({
          ok: false,
          message:
            '엔진과의 연결이 끊겨 결과를 알 수 없습니다. 다시 연결된 뒤 상태와 로그를 확인하세요'
        })
      }
      this.pending.clear()
      this.retry(reason)
    }
    const lostReason = (): string =>
      welcomed ? '엔진과의 연결이 끊겼습니다' : '엔진에 연결할 수 없습니다'
    // Electron 39's WebSocket fires only 'error', never 'close', when the connection is refused,
    // so both events end the connection. Its close() on a socket that is still connecting fires
    // 'error' again at once, so only an open socket is closed here (both checked 2026-10-16).
    socket.addEventListener('error', () => {
      lost(lostReason())
      if (socket.readyState === WebSocket.OPEN) socket.close()
    })
    socket.addEventListener('close', (event) => {
      lost(event.code === 1008 ? '엔진이 연결을 거부했습니다 (토큰)' : lostReason())
    })
  }

  private retry(reason: string): void {
    this.setStatus('disconnected', reason)
    if (this.stopped || this.retryTimer) return
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null
      void this.connect()
    }, RETRY_DELAY_MS)
  }

  private setStatus(phase: ConnectionStatus['phase'], detail: string | null): void {
    if (this.status.phase === phase && this.status.detail === detail) return
    // "since" is when the phase began: retries while the engine stays down don't move it.
    const since = this.status.phase === phase ? this.status.since : now()
    this.status = { phase, detail, since }
    console.log(`[engine] ${phase}${detail ? `: ${detail}` : ''}`)
    this.events.status(this.status)
  }
}

async function readConnectionFile(path: string): Promise<ConnectionInfo> {
  let text: string
  try {
    text = await readFile(path, 'utf-8')
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === 'ENOENT') {
      throw new Error('엔진이 실행 중이 아닙니다 (연결 파일 없음)')
    }
    throw new Error(`연결 파일을 읽을 수 없습니다: ${(e as Error).message}`)
  }
  let data: { protocol?: unknown; port?: unknown; token?: unknown }
  try {
    data = JSON.parse(text)
  } catch {
    // The engine replaces the file atomically, so this is not a half-written file.
    throw new Error('연결 파일이 JSON이 아닙니다')
  }
  if (data.protocol !== PROTOCOL) {
    throw new Error(`엔진 프로토콜 ${String(data.protocol)}, GUI는 ${PROTOCOL}만 지원합니다`)
  }
  if (!Number.isInteger(data.port) || typeof data.token !== 'string') {
    throw new Error('연결 파일에 port나 token이 없습니다')
  }
  return { port: data.port as number, token: data.token }
}

// The engine is ours, but a malformed catalog would break the settings screen, so it is checked
// here once. An engine before 2026-10-20 sends none (D-48).
function parseCatalog(value: unknown): StrategyCatalog | null {
  if (value === undefined) return null
  const catalog = value as StrategyCatalog
  const valid =
    typeof catalog === 'object' &&
    catalog !== null &&
    Number.isInteger(catalog.max_leverage) &&
    Number.isInteger(catalog.max_symbols) &&
    Array.isArray(catalog.intervals) &&
    catalog.intervals.every((interval) => typeof interval === 'string') &&
    Array.isArray(catalog.strategies) &&
    catalog.strategies.every(
      (strategy) =>
        typeof strategy?.name === 'string' &&
        typeof strategy.interval === 'string' &&
        Array.isArray(strategy.params) &&
        strategy.params.every(
          (param) =>
            typeof param?.name === 'string' &&
            ['int', 'decimal', 'bool'].includes(param.type) &&
            typeof param.default === 'string' &&
            (param.description === undefined || typeof param.description === 'string')
        )
    )
  if (!valid) console.error('[engine] the catalog in welcome is malformed; ignored')
  return valid ? catalog : null
}

// Checked once here like the catalog, so a malformed list can't break the trades panel (D-51).
// Only the shape is checked; amounts stay strings the renderer shows as they are.
function parseTrades(message: Record<string, unknown>): RecentTrades | null {
  const { window_days, asset, totals, trades } = message as unknown as RecentTrades
  const isAmounts = (value: unknown): boolean =>
    typeof value === 'object' &&
    value !== null &&
    Object.values(value).every((amount) => typeof amount === 'string')
  const valid =
    Number.isInteger(window_days) &&
    typeof asset === 'string' &&
    typeof totals?.realized_pnl === 'string' &&
    isAmounts(totals.fees) &&
    Array.isArray(trades) &&
    trades.every(
      (t) =>
        ['time', 'symbol', 'role', 'side', 'quantity', 'price', 'realized_pnl'].every(
          (key) => typeof t?.[key as keyof typeof t] === 'string'
        ) &&
        Number.isInteger(t.order_id) &&
        (t.client_id === null || typeof t.client_id === 'string') &&
        isAmounts(t.fees) &&
        Number.isInteger(t.fills)
    )
  if (!valid) {
    console.error('[engine] the trades message is malformed; ignored')
    return null
  }
  return { window_days, asset, totals, trades }
}

// Checked once here like the trades, so a malformed list can't break the chart (D-54).
function parseCandles(message: Record<string, unknown>): SymbolCandles | null {
  const { symbol, interval, candles } = message as unknown as SymbolCandles
  const valid =
    typeof symbol === 'string' &&
    typeof interval === 'string' &&
    Array.isArray(candles) &&
    candles.every((c) =>
      ['open_time', 'open', 'high', 'low', 'close'].every(
        (key) => typeof c?.[key as keyof typeof c] === 'string'
      )
    )
  if (!valid) {
    console.error('[engine] a candles message is malformed; ignored')
    return null
  }
  return { symbol, interval, candles }
}

function now(): string {
  return new Date().toISOString()
}
