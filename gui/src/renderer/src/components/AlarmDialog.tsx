import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  ALARM_EVENTS,
  MAX_PRICE_ALARMS,
  POPUP_SECONDS,
  isAlarmPrice,
  type AlarmEvent,
  type AlarmSettings,
  type PriceAlarm
} from '../../../shared/alarms'
import { compareDecimal } from '../../../shared/decimal'
import type { SymbolState } from '../../../shared/engine'

// The alarm settings (#42), in a dialog over the main window. They are the GUI's own and send
// nothing to the engine, so a plain dialog in this page is enough; the main process still checks
// what it saves. The checks here only catch typing mistakes before that.

const EVENTS: Record<AlarmEvent, { label: string; hint: string }> = {
  entry: { label: '진입 체결', hint: '전략이 연 포지션의 주문이 체결될 때' },
  exit: { label: '청산 체결', hint: '전략의 청산이나 직접 누른 청산이 체결될 때' },
  stop: { label: '손절 체결', hint: '거래소에 걸어 둔 손절 주문이 체결될 때' },
  forced: { label: '킬 스위치·강제 청산', hint: '킬 스위치, 강제 청산, ADL로 포지션이 닫힐 때' },
  halt: { label: '거래 정지', hint: '전체나 한 심볼의 거래가 멈추거나, 심볼의 전략이 멈출 때' },
  disconnect: {
    label: '엔진 연결 끊김',
    hint: '엔진과의 연결이 끊길 때. 엔진 정지를 누른 뒤는 빼고'
  },
  errors: { label: '엔진 오류 로그', hint: '엔진이 ERROR나 CRITICAL 로그를 남길 때' }
}

function secondsLabel(seconds: number): string {
  return seconds === 0 ? '닫을 때까지' : `${seconds}초`
}

// The settings as edited: the loss percent stays text until it is saved.
interface Draft extends Omit<AlarmSettings, 'loss'> {
  loss: { enabled: boolean; percent: string }
}

function toDraft(settings: AlarmSettings): Draft {
  return { ...settings, loss: { ...settings.loss, percent: String(settings.loss.percent) } }
}

function fromDraft(draft: Draft): AlarmSettings {
  return { ...draft, loss: { ...draft.loss, percent: Number(draft.loss.percent) } }
}

function validPercent(text: string): boolean {
  return /^\d{1,2}$/.test(text) && Number(text) >= 1
}

function newId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

interface Props {
  settings: AlarmSettings // the saved settings, kept current while the dialog is open
  symbols: SymbolState[]
  onClose(): void
}

function AlarmDialog({ settings, symbols, onClose }: Props): React.JSX.Element {
  const [draft, setDraft] = useState(() => toDraft(settings))
  const [base, setBase] = useState(settings)
  const [saving, setSaving] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const dialog = useRef<HTMLDivElement>(null)

  // Saved elsewhere while open (the tray's switch, a one-time alarm that fired and turned itself
  // off): taken in if nothing has been edited yet, so a save doesn't undo it.
  if (settings !== base) {
    if (JSON.stringify(draft) === JSON.stringify(toDraft(base))) setDraft(toDraft(settings))
    setBase(settings)
  }

  // The page behind can't be reached while the dialog is open.
  useEffect(() => {
    const root = document.getElementById('root')
    if (root) root.inert = true
    dialog.current?.focus()
    return () => {
      if (root) root.inert = false
    }
  }, [])

  const set = (change: Partial<Draft>): void => setDraft({ ...draft, ...change })
  const setPrice = (id: string, change: Partial<PriceAlarm>): void =>
    set({ prices: draft.prices.map((p) => (p.id === id ? { ...p, ...change } : p)) })
  const addPrice = (): void => {
    const symbol = symbols[0]
    if (!symbol) return
    set({
      prices: [
        ...draft.prices,
        {
          id: newId(),
          symbol: symbol.symbol,
          direction: 'above',
          price: symbol.mark_price ?? '',
          repeat: false,
          enabled: true
        }
      ]
    })
  }

  const percentValid = validPercent(draft.loss.percent)
  const percentError = draft.loss.enabled && !percentValid
  const priceErrors = new Set(draft.prices.filter((p) => !isAlarmPrice(p.price)).map((p) => p.id))
  const invalid = percentError || priceErrors.size > 0

  const save = async (): Promise<void> => {
    setSaving(true)
    setFailure(null)
    try {
      // A loss warning that is off keeps its last valid percent.
      const loss = percentValid ? draft.loss : { ...draft.loss, percent: String(base.loss.percent) }
      const result = await window.api.alarms.set(fromDraft({ ...draft, loss }))
      if (result.ok) onClose()
      else setFailure(result.message)
    } catch (e) {
      setFailure(String(e))
    } finally {
      setSaving(false)
    }
  }

  return createPortal(
    <div className="modal-backdrop">
      <div
        ref={dialog}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="alarm-title"
        tabIndex={-1}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.preventDefault()
            onClose()
          }
        }}
      >
        <div className="modal-head">
          <h3 id="alarm-title">알림 설정</h3>
          <span className="spacer" />
          <label className="check strong">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(e) => set({ enabled: e.target.checked })}
            />
            알림 받기
          </label>
        </div>

        <div className={`modal-body${draft.enabled ? '' : ' muted-all'}`}>
          <p className="hint">
            알림은 화면 오른쪽 아래에 뜨고, 누르면 이 창이 열립니다. GUI 창을 닫아도 트레이에서 계속
            옵니다. GUI가 꺼져 있으면 오지 않습니다.
          </p>

          <section className="alarm-section">
            <h4>팝업</h4>
            <div className="alarm-row">
              <label className="check">
                <input
                  type="checkbox"
                  checked={draft.sound}
                  onChange={(e) => set({ sound: e.target.checked })}
                />
                소리
              </label>
              <label className="inline-field">
                표시 시간
                <select
                  value={draft.seconds}
                  onChange={(e) => set({ seconds: Number(e.target.value) })}
                >
                  {POPUP_SECONDS.map((seconds) => (
                    <option key={seconds} value={seconds}>
                      {secondsLabel(seconds)}
                    </option>
                  ))}
                </select>
              </label>
              <button
                className="small"
                onClick={() =>
                  window.api.alarms.test({ sound: draft.sound, seconds: draft.seconds })
                }
              >
                테스트 알림
              </button>
            </div>
          </section>

          <section className="alarm-section">
            <h4>엔진 이벤트</h4>
            <div className="alarm-events">
              {ALARM_EVENTS.map((event) => (
                <label className="check" key={event}>
                  <input
                    type="checkbox"
                    checked={draft.events[event]}
                    onChange={(e) =>
                      set({ events: { ...draft.events, [event]: e.target.checked } })
                    }
                  />
                  <span>
                    {EVENTS[event].label}
                    <span className="hint block">{EVENTS[event].hint}</span>
                  </span>
                </label>
              ))}
            </div>
          </section>

          <section className="alarm-section">
            <h4>일일 손실</h4>
            <div className="alarm-row">
              <label className="check">
                <input
                  type="checkbox"
                  checked={draft.loss.enabled}
                  onChange={(e) => set({ loss: { ...draft.loss, enabled: e.target.checked } })}
                />
                오늘 손실이 한도의
              </label>
              <input
                className="percent"
                inputMode="numeric"
                aria-label="한도 대비 퍼센트"
                value={draft.loss.percent}
                disabled={!draft.loss.enabled}
                onChange={(e) => set({ loss: { ...draft.loss, percent: e.target.value.trim() } })}
              />
              <span>%에 닿으면 경고</span>
              {percentError && <span className="field-error">1~99 사이 정수</span>}
            </div>
            <p className="hint">
              하루에 한 번 울리고, 손실이 다시 그 아래로 내려가면(다음 날) 다시 울립니다.
            </p>
          </section>

          <section className="alarm-section">
            <div className="alarm-section-head">
              <h4>가격 알림</h4>
              <span className="hint">엔진이 거래하는 심볼의 마크 가격 기준</span>
              <span className="spacer" />
              <button
                className="small"
                disabled={symbols.length === 0 || draft.prices.length >= MAX_PRICE_ALARMS}
                title={
                  symbols.length === 0
                    ? '엔진이 거래하는 심볼이 없습니다'
                    : draft.prices.length >= MAX_PRICE_ALARMS
                      ? `가격 알림은 ${MAX_PRICE_ALARMS}개까지입니다`
                      : undefined
                }
                onClick={addPrice}
              >
                가격 알림 추가
              </button>
            </div>
            {draft.prices.length === 0 ? (
              <p className="hint">설정한 가격 알림이 없습니다.</p>
            ) : (
              <ul className="price-alarms">
                {draft.prices.map((alarm) => (
                  <PriceRow
                    key={alarm.id}
                    alarm={alarm}
                    symbols={symbols}
                    invalid={priceErrors.has(alarm.id)}
                    onChange={(change) => setPrice(alarm.id, change)}
                    onRemove={() => set({ prices: draft.prices.filter((p) => p.id !== alarm.id) })}
                  />
                ))}
              </ul>
            )}
          </section>
        </div>

        <div className="modal-foot">
          {failure && <span className="field-error">{failure}</span>}
          <span className="spacer" />
          <button onClick={onClose}>취소</button>
          <button className="primary" disabled={invalid || saving} onClick={() => void save()}>
            {saving ? '저장 중…' : '저장'}
          </button>
        </div>
      </div>
    </div>,
    document.body
  )
}

interface RowProps {
  alarm: PriceAlarm
  symbols: SymbolState[]
  invalid: boolean
  onChange(change: Partial<PriceAlarm>): void
  onRemove(): void
}

function PriceRow({ alarm, symbols, invalid, onChange, onRemove }: RowProps): React.JSX.Element {
  const traded = symbols.find((s) => s.symbol === alarm.symbol)
  const mark = traded?.mark_price ?? null
  let note: string | null = null
  if (!traded) note = '엔진이 거래하지 않는 심볼이라 울리지 않습니다'
  else if (mark && !invalid && alarm.enabled) {
    const side = compareDecimal(mark, alarm.price)
    const holds = side !== null && (alarm.direction === 'above' ? side >= 0 : side <= 0)
    if (holds) {
      note = alarm.repeat
        ? '지금 이미 조건에 맞아, 가격이 벗어났다가 다시 들어올 때 울립니다'
        : '지금 이미 조건에 맞아, 저장하면 바로 울리고 꺼집니다'
    }
  }
  return (
    <li className={`price-alarm${alarm.enabled ? '' : ' off'}`}>
      <div className="alarm-row">
        <input
          type="checkbox"
          aria-label="이 가격 알림 켜기"
          title="켜기/끄기"
          checked={alarm.enabled}
          onChange={(e) => onChange({ enabled: e.target.checked })}
        />
        <select
          aria-label="심볼"
          value={alarm.symbol}
          onChange={(e) => onChange({ symbol: e.target.value })}
        >
          {!traded && <option value={alarm.symbol}>{alarm.symbol} (거래 안 함)</option>}
          {symbols.map((s) => (
            <option key={s.symbol} value={s.symbol}>
              {s.symbol}
            </option>
          ))}
        </select>
        <input
          className={`price${invalid ? ' invalid' : ''}`}
          inputMode="decimal"
          aria-label="가격"
          placeholder="가격"
          value={alarm.price}
          onChange={(e) => onChange({ price: e.target.value.trim() })}
        />
        <select
          aria-label="조건"
          value={alarm.direction}
          onChange={(e) => onChange({ direction: e.target.value as PriceAlarm['direction'] })}
        >
          <option value="above">이상</option>
          <option value="below">이하</option>
        </select>
        <select
          aria-label="반복"
          value={alarm.repeat ? 'repeat' : 'once'}
          onChange={(e) => onChange({ repeat: e.target.value === 'repeat' })}
        >
          <option value="once">한 번</option>
          <option value="repeat">반복</option>
        </select>
        <span className="spacer" />
        <button className="plain small" onClick={onRemove}>
          삭제
        </button>
      </div>
      <div className="price-alarm-note">
        <span className="hint">현재 {mark ?? '—'}</span>
        {invalid && <span className="field-error">가격은 0보다 큰 숫자로 적습니다</span>}
        {note && <span className="hint">· {note}</span>}
      </div>
    </li>
  )
}

export default AlarmDialog
