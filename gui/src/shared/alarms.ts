// The user's alarms (GitHub issue #42): what makes the GUI show a popup in the corner of the
// screen, and the API the preload script gives the main window for them. Shared by main, preload,
// and renderer. The main process saves them in the userData folder, like the theme, and checks
// every setting it saves or loads with parseAlarmSettings.

import { compareDecimal, isDecimal } from './decimal'

// What the engine reports that an alarm can be set on.
export const ALARM_EVENTS = [
  'entry', // an entry filled
  'exit', // a strategy exit or the user's close filled
  'stop', // an exchange-side stop filled
  'forced', // a kill switch close, liquidation, or ADL filled
  'halt', // the account or a symbol halted, or a symbol's strategy stopped
  'disconnect', // the connection to the engine was lost
  'errors' // the engine logged an ERROR or CRITICAL record
] as const
export type AlarmEvent = (typeof ALARM_EVENTS)[number]

// How long a popup stays, in seconds; 0 keeps it until it is closed.
export const POPUP_SECONDS = [5, 10, 30, 0] as const

export const MAX_PRICE_ALARMS = 20

export type PriceDirection = 'above' | 'below'

// Fires when the symbol's mark price is at or beyond `price`: a one-time alarm once, which then
// turns itself off; a repeating one each time the price moves into the condition.
export interface PriceAlarm {
  id: string
  symbol: string
  direction: PriceDirection
  price: string // a positive decimal, as amounts are text everywhere
  repeat: boolean
  enabled: boolean
}

export interface AlarmSettings {
  enabled: boolean // off: no popups at all
  sound: boolean
  seconds: number // one of POPUP_SECONDS
  events: Record<AlarmEvent, boolean>
  loss: { enabled: boolean; percent: number } // today's loss at this percent of the limit
  prices: PriceAlarm[]
}

export const DEFAULT_ALARMS: AlarmSettings = {
  enabled: true,
  sound: true,
  seconds: 10,
  events: {
    entry: true,
    exit: true,
    stop: true,
    forced: true,
    halt: true,
    disconnect: true,
    errors: true
  },
  loss: { enabled: true, percent: 70 },
  prices: []
}

const SYMBOL = /^[A-Z0-9]{2,30}$/
const ID = /^[a-z0-9-]{1,40}$/

/** Whether `price` is a price an alarm can be set at: a positive plain decimal. */
export function isAlarmPrice(price: string): boolean {
  return (
    price.length <= 30 &&
    isDecimal(price) &&
    !price.startsWith('-') &&
    compareDecimal(price, '0') === 1
  )
}

/** The settings as they are, or what is wrong with them, in words for the user. */
export function parseAlarmSettings(value: unknown): AlarmSettings | string {
  const v = value as AlarmSettings
  if (typeof v !== 'object' || v === null) return '알림 설정이 아닙니다'
  if (typeof v.enabled !== 'boolean' || typeof v.sound !== 'boolean') {
    return '알림 켜기나 소리 설정이 올바르지 않습니다'
  }
  if (!(POPUP_SECONDS as readonly number[]).includes(v.seconds)) {
    return '표시 시간이 올바르지 않습니다'
  }
  if (
    typeof v.events !== 'object' ||
    v.events === null ||
    !ALARM_EVENTS.every((event) => typeof v.events[event] === 'boolean')
  ) {
    return '이벤트 알림 설정이 올바르지 않습니다'
  }
  const { loss } = v
  if (
    typeof loss !== 'object' ||
    loss === null ||
    typeof loss.enabled !== 'boolean' ||
    !Number.isInteger(loss.percent) ||
    loss.percent < 1 ||
    loss.percent > 99
  ) {
    return '손실 경고는 한도의 1~99%로 정합니다'
  }
  if (!Array.isArray(v.prices)) return '가격 알림 목록이 올바르지 않습니다'
  if (v.prices.length > MAX_PRICE_ALARMS) {
    return `가격 알림은 ${MAX_PRICE_ALARMS}개까지입니다`
  }
  const ids = new Set<string>()
  for (const alarm of v.prices) {
    if (
      typeof alarm !== 'object' ||
      alarm === null ||
      typeof alarm.id !== 'string' ||
      !ID.test(alarm.id) ||
      ids.has(alarm.id) ||
      typeof alarm.symbol !== 'string' ||
      !SYMBOL.test(alarm.symbol) ||
      (alarm.direction !== 'above' && alarm.direction !== 'below') ||
      typeof alarm.repeat !== 'boolean' ||
      typeof alarm.enabled !== 'boolean'
    ) {
      return '가격 알림 설정이 올바르지 않습니다'
    }
    if (typeof alarm.price !== 'string' || !isAlarmPrice(alarm.price)) {
      return `${alarm.symbol} 가격 알림의 가격이 올바르지 않습니다`
    }
    ids.add(alarm.id)
  }
  // Only the known fields, so nothing else rides along into the saved file.
  return {
    enabled: v.enabled,
    sound: v.sound,
    seconds: v.seconds,
    events: Object.fromEntries(ALARM_EVENTS.map((event) => [event, v.events[event]])) as Record<
      AlarmEvent,
      boolean
    >,
    loss: { enabled: loss.enabled, percent: loss.percent },
    prices: v.prices.map(({ id, symbol, direction, price, repeat, enabled }) => ({
      id,
      symbol,
      direction,
      price,
      repeat,
      enabled
    }))
  }
}

// What a test popup takes from the settings being edited, before they are saved.
export interface AlarmPreview {
  sound: boolean
  seconds: number
}

export type AlarmSaveResult = { ok: true } | { ok: false; message: string }

export interface AlarmApi {
  get(): Promise<AlarmSettings>
  set(settings: AlarmSettings): Promise<AlarmSaveResult>
  test(preview: AlarmPreview): Promise<void>
  // The saved settings whenever they change: a save, the tray's switch, or a one-time price alarm
  // that fired and turned itself off.
  onChange(listener: (settings: AlarmSettings) => void): () => void
}
