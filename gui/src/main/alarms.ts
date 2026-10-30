// The user's alarms (GitHub issue #42). They watch only what the main process already receives
// from the engine: its state, its last 7 days' trades (D-51), its log records, and the connection.
// Nothing is sent to the engine. An alarm fires on a change seen while the GUI runs; what is
// already so when the GUI starts, or when the settings are saved, is the starting point and fires
// nothing. The one exception is a one-time price alarm, which fires whenever its condition holds,
// since it waits for exactly that and then turns itself off.
//
// The last state and trades are kept across a lost connection, so what changed while the GUI was
// disconnected (a fill, a halt) still fires once it reconnects.

import { app } from 'electron'
import { readFileSync, writeFileSync } from 'fs'
import { join } from 'path'
import {
  DEFAULT_ALARMS,
  parseAlarmSettings,
  type AlarmEvent,
  type AlarmPreview,
  type AlarmSettings,
  type PriceAlarm
} from '../shared/alarms'
import { compareDecimal, reachesPercent } from '../shared/decimal'
import type {
  ConnectionStatus,
  EngineState,
  LogsUpdate,
  RecentTrades,
  TradeRole,
  TradeRow
} from '../shared/engine'
import type { Popup, PopupIcon, PopupTone } from '../shared/popup'
import type { PopupStack } from './popups'

// A lost connection this soon after the user stopped the engine is the stop itself.
const EXPECTED_STOP_MS = 60_000
// More new fills than this at once (after a long disconnect) show as one popup.
const MAX_TRADE_POPUPS = 3
const MAX_BODY = 160

const SIDES: Record<string, string> = { BUY: '매수', SELL: '매도' }

const TRADE_ALARMS: Record<TradeRole, { event: AlarmEvent; label: string; icon: PopupIcon }> = {
  entry: { event: 'entry', label: '진입', icon: 'entry' },
  exit: { event: 'exit', label: '청산', icon: 'exit' },
  other: { event: 'exit', label: '기타', icon: 'exit' },
  stop: { event: 'stop', label: '손절', icon: 'stop' },
  kill: { event: 'forced', label: '킬 스위치 청산', icon: 'forced' },
  liquidation: { event: 'forced', label: '강제 청산', icon: 'forced' },
  adl: { event: 'forced', label: 'ADL', icon: 'forced' }
}

type Alert = Pick<Popup, 'tone' | 'icon' | 'title' | 'body'> & { time?: string }

export class AlarmCenter {
  private settings: AlarmSettings
  private connected = false
  private stopRequestedAt = 0
  private last: EngineState | null = null // kept across a lost connection
  private seenTrades: Set<string> | null = null // null until the first list
  private lossArmed: boolean | null = null // null: no starting point yet
  private readonly priceArmed = new Map<string, boolean>() // repeating alarms, by id

  /** `onChange` gets the saved settings whenever they change. */
  constructor(
    private readonly popups: PopupStack,
    private readonly onChange: (settings: AlarmSettings) => void
  ) {
    this.settings = loadSettings()
  }

  get current(): AlarmSettings {
    return this.settings
  }

  /** Saves settings the caller has checked with parseAlarmSettings. */
  set(settings: AlarmSettings): void {
    this.settings = settings
    // The state as it is now is the starting point for the loss warning and repeating price
    // alarms, so saving doesn't fire them for a condition that already held.
    this.lossArmed = null
    this.priceArmed.clear()
    if (this.last && this.connected) this.checkLoss(this.last)
    if (this.last && this.connected) this.checkPrices(this.last)
    this.save()
  }

  setEnabled(enabled: boolean): void {
    if (enabled !== this.settings.enabled) this.set({ ...this.settings, enabled })
  }

  test(preview: AlarmPreview): void {
    this.popups.show({
      tone: 'info',
      icon: 'price',
      title: '테스트 알림',
      body: '알림이 이렇게 보입니다.\n누르면 QuantDeck 창이 열립니다.',
      time: new Date().toISOString(),
      count: 1,
      ...preview
    })
  }

  /** The user confirmed stopping the engine, so the lost connection that follows is expected. */
  stopRequested(): void {
    this.stopRequestedAt = Date.now()
  }

  status(status: ConnectionStatus): void {
    const wasConnected = this.connected
    this.connected = status.phase === 'connected'
    if (!wasConnected || status.phase !== 'disconnected') return
    if (Date.now() - this.stopRequestedAt < EXPECTED_STOP_MS) return
    this.fire('disconnect', {
      tone: 'danger',
      icon: 'disconnect',
      title: '엔진 연결 끊김',
      body:
        `${status.detail ?? '엔진과의 연결이 끊겼습니다'}\n` +
        '엔진이 멈췄다면 손실 한도 감시도 멈춥니다'
    })
  }

  state(state: EngineState): void {
    const previous = this.last
    this.last = state
    if (previous) this.checkHalts(previous, state)
    this.checkLoss(state)
    this.checkPrices(state)
  }

  trades(trades: RecentTrades | null): void {
    if (!trades) return // a new connection: its engine sends its own list
    const key = (row: TradeRow): string => `${row.symbol}:${row.order_id}`
    const seen = this.seenTrades
    this.seenTrades = new Set([...(seen ?? []), ...trades.trades.map(key)])
    if (!seen) return
    const fresh = trades.trades.filter((row) => !seen.has(key(row))).reverse() // oldest first
    if (fresh.length > MAX_TRADE_POPUPS) {
      const symbols = [...new Set(fresh.map((row) => row.symbol))].join(', ')
      if (fresh.some((row) => this.wants(TRADE_ALARMS[row.role].event))) {
        this.show({
          tone: 'info',
          icon: 'info',
          title: `새 체결 ${fresh.length}건`,
          body: `${symbols}\n거래 기록에서 확인하세요`
        })
      }
      return
    }
    for (const row of fresh) this.fireTrade(row, trades.asset)
  }

  logs(update: LogsUpdate): void {
    if (update.reset) return // a new connection's backlog repeats older records
    for (const record of update.records) {
      if (record.level !== 'ERROR' && record.level !== 'CRITICAL') continue
      this.fire(
        'errors',
        {
          tone: 'danger',
          icon: 'error',
          title: `엔진 ${record.level === 'CRITICAL' ? '심각한 오류' : '오류'}`,
          body: record.message,
          time: record.time
        },
        'errors'
      )
    }
  }

  private fireTrade(row: TradeRow, asset: string): void {
    const alarm = TRADE_ALARMS[row.role]
    const lines = [`${SIDES[row.side] ?? row.side} ${row.quantity} @ ${row.price}`]
    let tone: PopupTone = 'info'
    if (row.role !== 'entry') {
      lines.push(`실현 손익 ${signed(row.realized_pnl)} ${asset}`)
      const pnl = compareDecimal(row.realized_pnl, '0')
      if (alarm.event === 'stop' || alarm.event === 'forced') tone = 'danger'
      else if (pnl === 1) tone = 'ok'
      else if (pnl === -1) tone = 'warn'
    }
    this.fire(alarm.event, {
      tone,
      icon: alarm.icon,
      title: `${alarm.label} 체결 · ${row.symbol}`,
      body: lines.join('\n'),
      time: row.time
    })
  }

  private checkHalts(previous: EngineState, state: EngineState): void {
    if (!previous.halt.halted && state.halt.halted) {
      this.fire('halt', {
        tone: 'danger',
        icon: 'halt',
        title: '전체 거래 정지',
        body: state.halt.reason ?? '엔진이 모든 심볼의 거래를 멈췄습니다',
        time: state.halt.since ?? undefined
      })
    }
    for (const symbol of state.symbols) {
      const before = previous.symbols.find((s) => s.symbol === symbol.symbol)
      if (!before) continue
      if (!before.halted && symbol.halted) {
        this.fire('halt', {
          tone: 'danger',
          icon: 'halt',
          title: `${symbol.symbol} 거래 정지`,
          body: symbol.halted
        })
      }
      const stopped = symbol.strategy?.stopped
      if (before.strategy && !before.strategy.stopped && stopped) {
        this.fire('halt', {
          tone: 'danger',
          icon: 'halt',
          title: `${symbol.symbol} 전략 중단`,
          body: stopped
        })
      }
    }
  }

  // Fires once as today's loss reaches the set share of the limit, and again only after it has
  // fallen back below it (the next risk day).
  private checkLoss(state: EngineState): void {
    const loss = state.daily_loss
    if (!loss) return
    const { percent } = this.settings.loss
    const reached = reachesPercent(loss.loss, loss.limit, percent)
    if (reached === null) return
    const armed = this.lossArmed
    this.lossArmed = !reached
    if (!reached || armed !== true || !this.settings.loss.enabled) return
    this.show({
      tone: 'warn',
      icon: 'loss',
      title: `일일 손실 경고 · 한도의 ${percent}%`,
      body:
        `오늘 손실 ${loss.loss} / 한도 ${loss.limit} ${loss.asset}\n` +
        '한도에 닿으면 킬 스위치가 모든 포지션을 닫습니다'
    })
  }

  private checkPrices(state: EngineState): void {
    const fired: PriceAlarm[] = []
    for (const alarm of this.settings.prices) {
      if (!alarm.enabled) continue
      const mark = state.symbols.find((s) => s.symbol === alarm.symbol)?.mark_price
      if (!mark) continue
      const side = compareDecimal(mark, alarm.price)
      if (side === null) continue
      const holds = alarm.direction === 'above' ? side >= 0 : side <= 0
      if (!alarm.repeat) {
        // Waits while every popup is off, rather than turning itself off unseen.
        if (!holds || !this.settings.enabled) continue
        fired.push(alarm)
      } else {
        const armed = this.priceArmed.get(alarm.id)
        this.priceArmed.set(alarm.id, !holds)
        if (!holds || armed !== true) continue
      }
      this.show(
        {
          tone: 'info',
          icon: 'price',
          title: `${alarm.symbol} ${alarm.price} ${alarm.direction === 'above' ? '이상' : '이하'}`,
          body: `마크 가격 ${mark}\n${alarm.repeat ? '반복 알림' : '한 번 알림이라 이제 꺼집니다'}`
        },
        `price:${alarm.id}`
      )
    }
    if (fired.length === 0) return
    const off = new Set(fired.map((alarm) => alarm.id))
    this.settings = {
      ...this.settings,
      prices: this.settings.prices.map((alarm) =>
        off.has(alarm.id) ? { ...alarm, enabled: false } : alarm
      )
    }
    this.save()
  }

  private wants(event: AlarmEvent): boolean {
    return this.settings.events[event]
  }

  private fire(event: AlarmEvent, alert: Alert, key: string | null = null): void {
    if (this.wants(event)) this.show(alert, key)
  }

  private show(alert: Alert, key: string | null = null): void {
    if (!this.settings.enabled) return
    const body = alert.body.length > MAX_BODY ? `${alert.body.slice(0, MAX_BODY - 1)}…` : alert.body
    this.popups.show(
      {
        tone: alert.tone,
        icon: alert.icon,
        title: alert.title,
        body,
        time: alert.time ?? new Date().toISOString(),
        count: 1,
        seconds: this.settings.seconds,
        sound: this.settings.sound
      },
      key
    )
  }

  private save(): void {
    try {
      writeFileSync(settingsFile(), JSON.stringify(this.settings, null, 2))
    } catch (e) {
      // The settings still apply until the GUI closes.
      console.error(`[alarms] could not save the alarm settings: ${e}`)
    }
    this.onChange(this.settings)
  }
}

function settingsFile(): string {
  return join(app.getPath('userData'), 'alarms.json')
}

/** The saved settings, with any field a newer version added taken from the defaults; the
 * defaults if none are saved or the file can't be used. */
function loadSettings(): AlarmSettings {
  let saved: Partial<AlarmSettings>
  try {
    saved = JSON.parse(readFileSync(settingsFile(), 'utf-8'))
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code !== 'ENOENT') {
      console.error(`[alarms] could not read the alarm settings, using the defaults: ${e}`)
    }
    return DEFAULT_ALARMS
  }
  const settings = parseAlarmSettings({
    ...DEFAULT_ALARMS,
    ...saved,
    events: { ...DEFAULT_ALARMS.events, ...saved?.events },
    loss: { ...DEFAULT_ALARMS.loss, ...saved?.loss }
  })
  if (typeof settings === 'string') {
    console.error(`[alarms] the saved alarm settings are invalid, using the defaults: ${settings}`)
    return DEFAULT_ALARMS
  }
  return settings
}

/** `30.5` → `+30.5`, so a gain reads as one; zero and losses as they are. */
function signed(amount: string): string {
  return compareDecimal(amount, '0') === 1 ? `+${amount}` : amount
}
