// Display helpers. Amounts stay strings (D-33): they are only inspected as text, never parsed.
// Times arrive as ISO 8601 UTC and are shown in this computer's time zone (KST here; D-12 note).

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

/** `2026-10-16T07:41:50.908+00:00` → `16:41:50` in local time */
export function clock(iso: string): string {
  const time = new Date(iso)
  if (Number.isNaN(time.getTime())) return iso
  return `${pad(time.getHours())}:${pad(time.getMinutes())}:${pad(time.getSeconds())}`
}

/** `2026-10-16T07:41:50.908+00:00` → `2026-10-16 16:41:50` in local time */
export function dateTime(iso: string): string {
  const time = new Date(iso)
  if (Number.isNaN(time.getTime())) return iso
  const date = `${time.getFullYear()}-${pad(time.getMonth() + 1)}-${pad(time.getDate())}`
  return `${date} ${clock(iso)}`
}

/** `2026-10-19T04:00:00+00:00` → `10-19 13:00` in local time, for candle times */
export function shortDateTime(iso: string): string {
  const time = new Date(iso)
  if (Number.isNaN(time.getTime())) return iso
  return `${pad(time.getMonth() + 1)}-${pad(time.getDate())} ${pad(time.getHours())}:${pad(time.getMinutes())}`
}

/** The local date of `iso` as `2026-10-22`, for grouping by day */
export function localDay(iso: string): string {
  const time = new Date(iso)
  if (Number.isNaN(time.getTime())) return iso
  return `${time.getFullYear()}-${pad(time.getMonth() + 1)}-${pad(time.getDate())}`
}

const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토']

/** `2026-10-22T05:03:12+00:00` → `10월 22일 (목)` in local time */
export function dayLabel(iso: string): string {
  const time = new Date(iso)
  if (Number.isNaN(time.getTime())) return iso
  return `${time.getMonth() + 1}월 ${time.getDate()}일 (${WEEKDAYS[time.getDay()]})`
}

/** Time from `iso` to `now` (ms): `12m`, `2h 5m`, or `1d 4h` */
export function elapsed(iso: string, now: number): string {
  const start = new Date(iso).getTime()
  if (Number.isNaN(start)) return iso
  const minutes = Math.max(0, Math.floor((now - start) / 60_000))
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ${minutes % 60}m`
  return `${Math.floor(hours / 24)}d ${hours % 24}h`
}

/** When the risk day starts in local time: 00:00 UTC (D-23), `09:00` in Korea. */
export function riskDayStart(): string {
  const start = new Date(Date.UTC(2026, 0, 1))
  return `${pad(start.getHours())}:${pad(start.getMinutes())}`
}

export function isNegative(amount: string): boolean {
  return amount.startsWith('-')
}

export function isZero(amount: string): boolean {
  return /^-?0*(\.0*)?$/.test(amount)
}

/** `30.5` → `+30.5`, so a gain reads as one; zero and losses as they are. */
export function signed(amount: string): string {
  return isZero(amount) || isNegative(amount) ? amount : `+${amount}`
}

/** CSS class for a profit-or-loss amount. */
export function pnlClass(amount: string | null): string {
  if (amount === null || isZero(amount)) return ''
  return isNegative(amount) ? 'loss' : 'profit'
}
