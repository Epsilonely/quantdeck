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

/** CSS class for a profit-or-loss amount. */
export function pnlClass(amount: string | null): string {
  if (amount === null || isZero(amount)) return ''
  return isNegative(amount) ? 'loss' : 'profit'
}
