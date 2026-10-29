// A symbol's strategy setting (D-47) as the main process's confirmations and the renderer's
// screens both read it, so the two never compare or write it differently.

import type { StrategySetting } from './engine'

/** The same strategy, interval, and parameters. */
export function sameStrategy(a: StrategySetting | null, b: StrategySetting | null): boolean {
  if (!a || !b) return a === b
  const keys = Object.keys(a.params)
  return (
    a.name === b.name &&
    a.interval === b.interval &&
    keys.length === Object.keys(b.params).length &&
    keys.every((key) => a.params[key] === b.params[key])
  )
}

/** E.g. `entry_period=55, exit_period=20`, as the engine logs them. */
export function paramsText(params: Record<string, string>): string {
  return Object.entries(params)
    .map(([key, value]) => `${key}=${value}`)
    .join(', ')
}
