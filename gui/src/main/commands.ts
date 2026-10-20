// The commands the main process accepts from the renderer, and what it asks the user before
// sending one (D-33, D-34, D-47, D-48). The renderer only asks: the main process checks the
// command's shape, builds the confirmation from the exact command it will send, and sends that
// copy, so nothing the renderer does alone can send a command or show one thing and send
// another. The engine checks every value again against its own bounds (safety principle 4).

import {
  COMMAND_NAMES,
  type CommandName,
  type EngineCommand,
  type EngineState,
  type SetSymbolArgs,
  type StrategySetting,
  type SymbolState
} from '../shared/engine'

export interface Confirmation {
  message: string
  detail: string
  button: string
}

// Shapes only; the engine checks the values. Text that goes into the confirmation is limited to
// characters that can't break its lines or pass for other text.
const SYMBOL = /^[A-Z0-9]{2,30}$/ // the engine's own pattern
const STRATEGY_NAME = /^[a-z0-9-]{1,40}$/
const INTERVAL = /^[0-9]{1,3}[a-zA-Z]$/
const PARAM_NAME = /^[a-z_][a-z0-9_]{0,39}$/
const PARAM_VALUE = /^[a-zA-Z0-9.+-]{1,40}$/
const MAX_PARAMS = 20

/** The command as the main process will send it, rebuilt from checked fields, or the reason it
 * is refused. */
export function parseCommand(value: unknown): EngineCommand | string {
  if (!isObject(value) || !COMMAND_NAMES.includes(value.name as CommandName)) {
    return `알 수 없는 명령: ${describeUnknown(value)}`
  }
  const name = value.name as CommandName
  const keys = Object.keys(value)
  switch (name) {
    case 'emergency_close':
    case 'lift_halt':
    case 'stop_engine':
      return keys.length === 1 ? { name } : `${name}은 인자를 받지 않습니다`
    case 'set_symbol': {
      const args = keys.length === 2 ? parseSetSymbol(value.args) : null
      return args ? { name, args } : 'set_symbol의 인자 형식이 잘못되었습니다'
    }
    case 'remove_symbol': {
      const args = value.args
      const valid =
        keys.length === 2 &&
        isObject(args) &&
        hasOnlyKeys(args, ['symbol']) &&
        typeof args.symbol === 'string' &&
        SYMBOL.test(args.symbol)
      return valid
        ? { name, args: { symbol: args.symbol as string } }
        : 'remove_symbol의 인자 형식이 잘못되었습니다'
    }
  }
}

function parseSetSymbol(args: unknown): SetSymbolArgs | null {
  if (!isObject(args) || !hasOnlyKeys(args, ['symbol', 'leverage', 'strategy'])) return null
  const { symbol, leverage, strategy } = args
  if (typeof symbol !== 'string' || !SYMBOL.test(symbol)) return null
  if (typeof leverage !== 'number' || !Number.isInteger(leverage)) return null
  if (strategy === null) return { symbol, leverage, strategy: null }
  if (!isObject(strategy) || !hasOnlyKeys(strategy, ['name', 'interval', 'params'])) return null
  const { name, interval, params } = strategy
  if (typeof name !== 'string' || !STRATEGY_NAME.test(name)) return null
  if (typeof interval !== 'string' || !INTERVAL.test(interval)) return null
  if (!isObject(params)) return null
  const entries = Object.entries(params)
  if (entries.length > MAX_PARAMS) return null
  const copy: Record<string, string> = {}
  for (const [key, text] of entries) {
    if (!PARAM_NAME.test(key) || typeof text !== 'string' || !PARAM_VALUE.test(text)) return null
    copy[key] = text
  }
  return { symbol, leverage, strategy: { name, interval, params: copy } }
}

const FIXED: Record<'emergency_close' | 'lift_halt' | 'stop_engine', Confirmation> = {
  emergency_close: {
    message: '긴급 청산을 실행할까요?',
    detail:
      '킬스위치를 발동합니다. 모든 포지션을 시장가 reduceOnly 주문으로 청산하고, 모든 주문을 취소한 뒤, ' +
      '직접 정지를 해제할 때까지 거래를 멈춥니다 (D-27).',
    button: '긴급 청산'
  },
  lift_halt: {
    message: '거래 정지를 해제할까요?',
    detail:
      '킬스위치의 정지를 해제합니다. 오늘 손실이 아직 한도에 있으면 엔진이 거부합니다 (D-33).',
    button: '정지 해제'
  },
  stop_engine: {
    message: '엔진을 정지할까요?',
    detail:
      '엔진 프로세스를 종료합니다. 정지된 동안에는 일일 손실 확인과 킬스위치가 동작하지 않고, ' +
      '열린 포지션은 거래소에 걸린 손절 주문만 지킵니다. 진행 중인 킬스위치가 있으면 끝난 뒤에 ' +
      '정지합니다 (D-34).',
    button: '엔진 정지'
  }
}

const DECIDES_AT_ONCE =
  '새 전략은 붙자마자 최근 마감 캔들로 판단합니다. 그 캔들에 진입 신호가 있으면 다음 봉을 ' +
  '기다리지 않고 현재가로 진입하고, 거래소에 손절을 겁니다 (D-47).'
const POSITION_REFUSED = '포지션이 열려 있으면 엔진이 거부하고 아무것도 바꾸지 않습니다.'

/** What to ask before sending `command`, described against the last state received. */
export function confirmation(command: EngineCommand, state: EngineState | null): Confirmation {
  switch (command.name) {
    case 'emergency_close':
    case 'lift_halt':
    case 'stop_engine':
      return FIXED[command.name]
    case 'set_symbol':
      return confirmSetSymbol(command.args, findSymbol(state, command.args.symbol))
    case 'remove_symbol': {
      const current = findSymbol(state, command.args.symbol)
      const lines = [
        ...(current
          ? [`지금 설정: ${describeSetting(current.leverage, currentStrategy(current))}`, '']
          : []),
        '이 코인의 전략을 멈추고 감시 대상에서 빼며, 남은 주문을 취소합니다. 거래소의 격리 마진과 ' +
          '레버리지 설정은 그대로 남습니다.',
        POSITION_REFUSED
      ]
      return {
        message: `${command.args.symbol} 코인을 엔진에서 뺄까요?`,
        detail: lines.join('\n'),
        button: '코인 제거'
      }
    }
  }
}

function confirmSetSymbol(args: SetSymbolArgs, current: SymbolState | undefined): Confirmation {
  const next = describeSetting(args.leverage, args.strategy)
  if (!current) {
    const lines = [
      `설정: ${next}`,
      '',
      '거래소에 격리 마진과 레버리지를 적용하고, 남은 주문을 취소한 뒤 감시를 시작합니다.',
      args.strategy ? DECIDES_AT_ONCE : '전략이 없어 진입하지 않습니다.',
      POSITION_REFUSED
    ]
    return {
      message: `${args.symbol} 코인을 추가할까요?`,
      detail: lines.join('\n'),
      button: '추가'
    }
  }
  const before = currentStrategy(current)
  const leverageChanged = current.leverage !== args.leverage
  const strategyChanged = !sameStrategy(before, args.strategy)
  const lines = [
    leverageChanged
      ? `레버리지: ${describeLeverage(current.leverage)} → ${args.leverage}x`
      : `레버리지: ${args.leverage}x (그대로)`,
    strategyChanged
      ? `전략: ${describeStrategy(before)} → ${describeStrategy(args.strategy)}`
      : `전략: ${describeStrategy(args.strategy)} (그대로)`,
    ''
  ]
  if (strategyChanged && args.strategy) {
    lines.push(DECIDES_AT_ONCE, '포워드 테스트 기록은 새 설정의 파일에 씁니다.')
  } else if (strategyChanged) {
    lines.push('전략이 없으면 이 코인은 새로 진입하지 않습니다.')
  } else if (leverageChanged) {
    lines.push('레버리지만 바뀌고, 실행 중인 전략은 그대로 이어집니다.')
  } else {
    lines.push('바뀌는 것이 없습니다.')
  }
  lines.push(POSITION_REFUSED)
  return {
    message: `${args.symbol} 설정을 바꿀까요?`,
    detail: lines.join('\n'),
    button: '변경'
  }
}

function findSymbol(state: EngineState | null, symbol: string): SymbolState | undefined {
  return state?.symbols.find((s) => s.symbol === symbol)
}

// The symbol's current strategy setting, as far as the state says (older engines don't send it).
function currentStrategy(symbol: SymbolState): StrategySetting | null {
  const strategy = symbol.strategy
  if (!strategy) return null
  return {
    name: strategy.name ?? '?',
    interval: strategy.interval ?? '?',
    params: strategy.params ?? {}
  }
}

function sameStrategy(a: StrategySetting | null, b: StrategySetting | null): boolean {
  if (!a || !b) return a === b
  const keys = Object.keys(a.params)
  return (
    a.name === b.name &&
    a.interval === b.interval &&
    keys.length === Object.keys(b.params).length &&
    keys.every((key) => a.params[key] === b.params[key])
  )
}

function describeSetting(leverage: number | undefined, strategy: StrategySetting | null): string {
  return `${describeLeverage(leverage)} · ${describeStrategy(strategy)}`
}

function describeLeverage(leverage: number | undefined): string {
  return leverage === undefined ? '레버리지 ?' : `${leverage}x`
}

/** E.g. `donchian 4h (entry_period=55, exit_period=20)`, as the engine logs it. */
function describeStrategy(strategy: StrategySetting | null): string {
  if (!strategy) return '전략 없음'
  const params = Object.entries(strategy.params)
    .map(([key, value]) => `${key}=${value}`)
    .join(', ')
  return `${strategy.name} ${strategy.interval}` + (params ? ` (${params})` : '')
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: string[]): boolean {
  return Object.keys(value).every((key) => allowed.includes(key))
}

function describeUnknown(value: unknown): string {
  const name = isObject(value) ? value.name : value
  return typeof name === 'string' ? name.slice(0, 40) : typeof name
}
