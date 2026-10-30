// Exact comparisons of amounts, which stay decimal strings as the engine sends them (D-33). The
// alarms compare a price or a loss with a threshold the user typed; parsed as floats, an alarm at
// 0.1 could miss a mark price of exactly 0.1.

const DECIMAL = /^(-?)(\d+)(?:\.(\d+))?$/

/** A plain decimal such as `-12.5`: no exponent, no thousands separators. */
export function isDecimal(text: string): boolean {
  return DECIMAL.test(text)
}

/** Both amounts as integers at one scale, so they compare and multiply exactly; null if either
 * isn't a plain decimal. */
function scaled(a: string, b: string): [bigint, bigint] | null {
  const x = DECIMAL.exec(a)
  const y = DECIMAL.exec(b)
  if (!x || !y) return null
  const scale = Math.max((x[3] ?? '').length, (y[3] ?? '').length)
  const value = (match: RegExpExecArray): bigint => {
    const digits = BigInt(match[2] + (match[3] ?? '').padEnd(scale, '0'))
    return match[1] ? -digits : digits
  }
  return [value(x), value(y)]
}

/** -1, 0, or 1 as `a` is below, equal to, or above `b`; null if either isn't a plain decimal. */
export function compareDecimal(a: string, b: string): number | null {
  const pair = scaled(a, b)
  if (!pair) return null
  const [x, y] = pair
  return x < y ? -1 : x > y ? 1 : 0
}

/** Whether `part` is at least `percent`% of a positive `whole`; null if that can't be told. */
export function reachesPercent(part: string, whole: string, percent: number): boolean | null {
  const pair = scaled(part, whole)
  if (!pair || !Number.isInteger(percent)) return null
  const [x, y] = pair
  if (y <= BigInt(0)) return null
  return x * BigInt(100) >= y * BigInt(percent)
}
