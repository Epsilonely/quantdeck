import { useEffect, useState } from 'react'
import type { AlarmSettings } from '../../../shared/alarms'
import type { SymbolState } from '../../../shared/engine'
import AlarmDialog from './AlarmDialog'

// Opens the alarm settings (#42), and says so when every popup is off.
function AlarmButton({ symbols }: { symbols: SymbolState[] }): React.JSX.Element {
  const [settings, setSettings] = useState<AlarmSettings | null>(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    const { alarms } = window.api
    // Subscribe before asking, as useEngine does: the answer replaces what the listener set.
    const stop = alarms.onChange(setSettings)
    let active = true
    alarms.get().then((saved) => {
      if (active) setSettings(saved)
    })
    return () => {
      active = false
      stop()
    }
  }, [])

  const off = settings !== null && !settings.enabled
  return (
    <>
      <button
        className="plain"
        aria-haspopup="dialog"
        disabled={settings === null}
        title={off ? '알림이 꺼져 있습니다. 알림 설정을 엽니다' : '알림 설정을 엽니다'}
        onClick={() => setOpen(true)}
      >
        알림
        {off && <span className="alarm-off">꺼짐</span>}
      </button>
      {open && settings && (
        <AlarmDialog settings={settings} symbols={symbols} onClose={() => setOpen(false)} />
      )}
    </>
  )
}

export default AlarmButton
