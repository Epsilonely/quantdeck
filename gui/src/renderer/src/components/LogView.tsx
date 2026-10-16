import { useLayoutEffect, useRef } from 'react'
import type { LogRecord } from '../../../shared/engine'
import { clock } from '../format'

function LogView({ logs }: { logs: LogRecord[] }): React.JSX.Element {
  const box = useRef<HTMLDivElement>(null)
  // Follow new records only while the view is scrolled to the bottom.
  const following = useRef(true)

  useLayoutEffect(() => {
    if (following.current && box.current) box.current.scrollTop = box.current.scrollHeight
  }, [logs])

  const onScroll = (): void => {
    const el = box.current
    if (el) following.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24
  }

  return (
    <div className="logs" ref={box} onScroll={onScroll}>
      {logs.length === 0 && <p className="muted">로그가 없습니다.</p>}
      {logs.map((record, index) => (
        <div key={index} className={`log level-${record.level.toLowerCase()}`}>
          <span className="log-time" title={record.time}>
            {clock(record.time)}
          </span>
          <span className="log-level">{record.level}</span>
          <span className="log-logger">{record.logger}</span>
          <span className="log-message">{record.message}</span>
        </div>
      ))}
    </div>
  )
}

export default LogView
