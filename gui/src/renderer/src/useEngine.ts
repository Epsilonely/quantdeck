import { useEffect, useState } from 'react'
import type {
  ConnectionStatus,
  EngineState,
  LaunchState,
  LogRecord,
  RecentTrades,
  StrategyCatalog,
  SymbolCandles
} from '../../shared/engine'

const LOG_CAPACITY = 1000

export interface EngineView {
  status: ConnectionStatus | null // null until the main process first answers
  launch: LaunchState
  state: EngineState | null
  logs: LogRecord[]
  catalog: StrategyCatalog | null // what set_symbol accepts (D-48); null from older engines
  trades: RecentTrades | null // the last 7 days' trades (D-51); null until sent
  candles: Record<string, SymbolCandles> // each strategy's chart candles (D-54), by symbol
}

export function useEngine(): EngineView {
  const [status, setStatus] = useState<ConnectionStatus | null>(null)
  const [state, setState] = useState<EngineState | null>(null)
  const [logs, setLogs] = useState<LogRecord[]>([])
  const [launch, setLaunch] = useState<LaunchState>({ phase: 'idle', message: null })
  const [catalog, setCatalog] = useState<StrategyCatalog | null>(null)
  const [trades, setTrades] = useState<RecentTrades | null>(null)
  const [candles, setCandles] = useState<Record<string, SymbolCandles>>({})

  useEffect(() => {
    const { engine } = window.api
    // Subscribe before asking for the snapshot. Anything sent before the snapshot's reply is
    // already in it, and the snapshot replaces what the listeners set.
    const unsubscribe = [
      engine.onStatus(setStatus),
      engine.onState(setState),
      engine.onLaunch(setLaunch),
      engine.onCatalog(setCatalog),
      engine.onTrades(setTrades),
      engine.onCandles(setCandles),
      engine.onLogs((update) =>
        setLogs((shown) =>
          update.reset ? update.records : [...shown, ...update.records].slice(-LOG_CAPACITY)
        )
      )
    ]
    let active = true
    engine.snapshot().then((snapshot) => {
      if (!active) return
      setStatus(snapshot.status)
      setLaunch(snapshot.launch)
      setState(snapshot.state)
      setLogs(snapshot.logs)
      setCatalog(snapshot.catalog)
      setTrades(snapshot.trades)
      setCandles(snapshot.candles)
    })
    return () => {
      active = false
      for (const stop of unsubscribe) stop()
    }
  }, [])

  return { status, launch, state, logs, catalog, trades, candles }
}
