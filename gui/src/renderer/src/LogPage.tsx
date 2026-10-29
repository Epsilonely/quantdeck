import { useEffect, useState } from 'react'
import type { ConnectionStatus, LogRecord } from '../../shared/engine'
import { LOG_CAPACITY } from '../../shared/logs'
import LogView, { type ShownRecord } from './components/LogView'
import { dateTime } from './format'

// The engine log's own window (D-55). The main process sends it the engine's records and the
// connection; the page has no other call.
function LogPage(): React.JSX.Element {
  const [status, setStatus] = useState<ConnectionStatus | null>(null)
  const [logs, setLogs] = useState<ShownRecord[]>([])

  useEffect(() => {
    const { logApi } = window
    let next = 0
    const numbered = (records: LogRecord[]): ShownRecord[] =>
      records.map((record) => ({ ...record, id: next++ }))
    // Subscribe before asking for the snapshot, as useEngine does: anything sent before the
    // snapshot's reply is already in it, and the snapshot replaces what the listeners set.
    const unsubscribe = [
      logApi.onStatus(setStatus),
      logApi.onLogs((update) => {
        const added = numbered(update.records)
        setLogs((shown) => (update.reset ? added : [...shown, ...added].slice(-LOG_CAPACITY)))
      })
    ]
    let active = true
    logApi.snapshot().then((snapshot) => {
      if (!active) return
      setStatus(snapshot.status)
      setLogs(numbered(snapshot.logs))
    })
    return () => {
      active = false
      for (const stop of unsubscribe) stop()
    }
  }, [])

  // Records stop while the engine is disconnected, so the window says so.
  let statusText = '연결 중'
  let statusClass = 'info'
  if (status?.phase === 'connected') {
    statusText = '● 연결됨'
    statusClass = 'ok'
  } else if (status?.phase === 'disconnected') {
    statusText = `연결 끊김 · ${dateTime(status.since)}부터 새 로그가 오지 않습니다`
    statusClass = 'danger'
  }

  return (
    <div className="app">
      <header className="header">
        <h1>엔진 로그</h1>
        <span className={`tag ${statusClass}`} title={status?.detail ?? undefined}>
          {statusText}
        </span>
      </header>
      <LogView logs={logs} />
    </div>
  )
}

export default LogPage
