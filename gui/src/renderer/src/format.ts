// Display helpers. Amounts stay strings (D-33): they are only inspected as text, never parsed.

/** `2026-10-16T05:00:00.123+00:00` → `05:00:00` */
export function clock(iso: string): string {
  return iso.slice(11, 19)
}

/** `2026-10-16T05:00:00.123+00:00` → `2026-10-16 05:00:00 UTC` */
export function dateTime(iso: string): string {
  return `${iso.slice(0, 10)} ${iso.slice(11, 19)} UTC`
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
