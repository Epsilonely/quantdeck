import { useEffect, useState } from 'react'
import type {
  ConnectionStatus,
  EngineState,
  LaunchState,
  RecentTrades,
  StrategyCatalog,
  SymbolCandles
} from '../../shared/engine'

// The engine's logs show in a window of their own (D-55), not here.
export interface EngineView {
  status: ConnectionStatus | null // null until the main process first answers
  launch: LaunchState
  state: EngineState | null
  catalog: StrategyCatalog | null // what set_symbol accepts (D-48); null from older engines
  trades: RecentTrades | null // the last 7 days' trades (D-51); null until sent
  candles: Record<string, SymbolCandles> // each strategy's chart candles (D-54), by symbol
}

export function useEngine(): EngineView {
  const [status, setStatus] = useState<ConnectionStatus | null>(null)
  const [state, setState] = useState<EngineState | null>(null)
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
      engine.onCandles((update) =>
        setCandles((current) => (update ? { ...current, [update.symbol]: update } : {}))
      )
    ]
    let active = true
    engine.snapshot().then((snapshot) => {
      if (!active) return
      setStatus(snapshot.status)
      setLaunch(snapshot.launch)
      setState(snapshot.state)
      setCatalog(snapshot.catalog)
      setTrades(snapshot.trades)
      setCandles(snapshot.candles)
    })
    return () => {
      active = false
      for (const stop of unsubscribe) stop()
    }
  }, [])

  return { status, launch, state, catalog, trades, candles }
}
