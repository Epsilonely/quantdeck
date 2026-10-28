import { useEffect, useState } from 'react'
import type { UnseenLogs } from '../../../shared/logs'

// Opens the engine log's window (D-55), and counts the warnings and errors that arrived while it
// was closed or minimized, so they aren't missed with the log out of view.
function LogButton(): React.JSX.Element {
  const [unseen, setUnseen] = useState<UnseenLogs>({ warnings: 0, errors: 0 })

  useEffect(() => {
    const { logWindow } = window.api
    // Subscribe before asking, as useEngine does: the answer replaces what the listener set.
    const stop = logWindow.onUnseen(setUnseen)
    let active = true
    logWindow.unseen().then((counts) => {
      if (active) setUnseen(counts)
    })
    return () => {
      active = false
      stop()
    }
  }, [])

  const total = unseen.warnings + unseen.errors
  const title =
    total > 0
      ? `로그 창을 닫아 둔 동안 경고 ${unseen.warnings}건, 오류 ${unseen.errors}건이 왔습니다`
      : '엔진 로그를 별도 창으로 엽니다'
  return (
    <button className="plain" title={title} onClick={() => window.api.logWindow.open()}>
      엔진 로그
      {total > 0 && (
        <span className={`log-count ${unseen.errors > 0 ? 'danger' : 'warn'}`}>
          {total > 99 ? '99+' : total}
        </span>
      )}
    </button>
  )
}

export default LogButton
