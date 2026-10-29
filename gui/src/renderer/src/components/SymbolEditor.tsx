import { useState } from 'react'
import type {
  CommandName,
  CommandResult,
  ParameterSpec,
  SetSymbolArgs,
  StrategyCatalog,
  StrategySetting,
  SymbolState
} from '../../../shared/engine'
import { sameStrategy } from '../../../shared/strategy'
import HelpTip from './HelpTip'

// Adds a symbol or changes its leverage and strategy (D-47, D-48). The panel only builds the
// command: the main process asks the user to confirm with what it sends, and the engine checks
// every value again. The checks here only catch typing mistakes before that.

export type EditTarget = { mode: 'add' } | { mode: 'edit'; symbol: string }

interface Draft {
  symbol: string
  leverage: string
  strategy: string // '' for no strategy
  interval: string
  params: Record<string, string> // as text; a bool is 'true' or 'false'
}

const SYMBOL = /^[A-Z0-9]{2,30}$/
const INT = /^-?\d+$/
const DECIMAL = /^-?\d+(\.\d+)?$/

function defaults(params: ParameterSpec[]): Record<string, string> {
  return Object.fromEntries(params.map((p) => [p.name, p.default]))
}

// The current setting, with any parameter the engine didn't send filled in from the catalog.
function currentDraft(current: SymbolState, catalog: StrategyCatalog): Draft {
  const strategy = current.strategy
  const spec = catalog.strategies.find((s) => s.name === strategy?.name)
  return {
    symbol: current.symbol,
    leverage: current.leverage === undefined ? '' : String(current.leverage),
    strategy: strategy?.name ?? '',
    interval: strategy?.interval ?? spec?.interval ?? '',
    params: { ...(spec ? defaults(spec.params) : {}), ...(strategy?.params ?? {}) }
  }
}

function toArgs(draft: Draft): SetSymbolArgs {
  return {
    symbol: draft.symbol,
    leverage: Number(draft.leverage),
    strategy: draft.strategy
      ? { name: draft.strategy, interval: draft.interval, params: { ...draft.params } }
      : null
  }
}

interface Props {
  target: EditTarget
  symbols: SymbolState[]
  catalog: StrategyCatalog
  connected: boolean
  busy: CommandName | null
  failure: CommandResult | null // the last set_symbol or remove_symbol that failed
  onApply(args: SetSymbolArgs): void
  onRemove(symbol: string): void
  onClose(): void
}

function SymbolEditor({
  target,
  symbols,
  catalog,
  connected,
  busy,
  failure,
  onApply,
  onRemove,
  onClose
}: Props): React.JSX.Element {
  const current =
    target.mode === 'edit' ? symbols.find((s) => s.symbol === target.symbol) : undefined
  const [draft, setDraft] = useState<Draft>(() =>
    current
      ? currentDraft(current, catalog)
      : { symbol: '', leverage: '', strategy: '', interval: '', params: {} }
  )

  if (target.mode === 'edit' && !current) {
    return (
      <div className="editor">
        <div className="editor-head">
          <h3>{target.symbol}</h3>
          <span className="spacer" />
          <button className="plain small" onClick={onClose}>
            닫기
          </button>
        </div>
        <p className="muted">이 심볼은 더 이상 엔진에 없습니다.</p>
      </div>
    )
  }

  const spec = catalog.strategies.find((s) => s.name === draft.strategy)
  const set = (change: Partial<Draft>): void => setDraft({ ...draft, ...change })

  const chooseStrategy = (name: string): void => {
    const chosen = catalog.strategies.find((s) => s.name === name)
    if (!chosen) set({ strategy: '', interval: '', params: {} })
    else if (current && current.strategy?.name === name) {
      // Back to the symbol's own strategy: its own interval and parameters, not the defaults.
      const own = currentDraft(current, catalog)
      set({ strategy: name, interval: own.interval, params: own.params })
    } else set({ strategy: name, interval: chosen.interval, params: defaults(chosen.params) })
  }

  // Typing mistakes only; the engine checks the values (bounds, warm-up, limit entries).
  const errors: Record<string, string> = {}
  if (target.mode === 'add') {
    if (!draft.symbol) errors.symbol = '심볼을 입력하세요'
    else if (!SYMBOL.test(draft.symbol)) errors.symbol = '대문자와 숫자 2–30자 (예: ETHUSDT)'
    else if (symbols.some((s) => s.symbol === draft.symbol))
      errors.symbol = '이미 있는 심볼입니다. 표에서 "변경"을 누르세요'
  }
  const leverage = Number(draft.leverage)
  if (!draft.leverage) errors.leverage = '레버리지를 입력하세요'
  else if (!INT.test(draft.leverage) || leverage < 1 || leverage > catalog.max_leverage) {
    errors.leverage = `1–${catalog.max_leverage} 사이의 정수`
  }
  for (const p of spec?.params ?? []) {
    const text = draft.params[p.name] ?? ''
    if (p.type === 'int' && !INT.test(text)) errors[p.name] = '정수'
    if (p.type === 'decimal' && !DECIMAL.test(text)) errors[p.name] = '숫자 (예: 0.5)'
  }

  const args = toArgs(draft)
  const unchanged =
    current !== undefined &&
    current.leverage === args.leverage &&
    sameStrategy(current.strategy ? currentDraftStrategy(current, catalog) : null, args.strategy)
  const positionOpen = Boolean(current?.position)
  const idle = connected && busy === null
  const invalid = Object.keys(errors).length > 0

  // Why "apply" is off; an open position has its own notice above.
  let blocked: string | null = null
  if (!connected) blocked = '엔진에 연결되어 있지 않습니다'
  else if (!positionOpen && invalid) blocked = '입력값을 확인하세요'
  else if (!positionOpen && unchanged) blocked = '바뀐 것이 없습니다'
  const canApply = idle && !positionOpen && !invalid && !unchanged

  return (
    <div
      className="editor"
      onKeyDown={(event) => {
        if (event.key === 'Escape') onClose()
      }}
    >
      <div className="editor-head">
        <h3>{current ? `${current.symbol} 설정` : '코인 추가'}</h3>
        <span className="spacer" />
        <button className="plain small" onClick={onClose}>
          닫기
        </button>
      </div>

      {positionOpen && (
        <div className="notice danger">
          포지션이 열려 있어 엔진이 설정 변경과 제거를 거부합니다. 포지션이 닫힌 뒤에 바꾸세요.
        </div>
      )}
      {current?.halted && (
        <div className="notice warn">
          이 코인은 정지 상태입니다: {current.halted}. 설정을 바꿔도 정지는 풀리지 않고, 제거했다가
          다시 추가하면 풀립니다.
        </div>
      )}
      {current?.strategy?.stopped && (
        <div className="notice warn">
          전략이 멈춰 있습니다: {current.strategy.stopped}. 전략 설정을 바꾸거나 엔진을 재시작하면
          다시 시작합니다.
        </div>
      )}
      {failure && (
        <div className="notice danger">
          <strong>실패</strong> · {failure.message}
        </div>
      )}

      <div className="fields">
        <label className="field">
          <span className="label">심볼</span>
          {current ? (
            <span className="static">{current.symbol}</span>
          ) : (
            <input
              value={draft.symbol}
              placeholder="ETHUSDT"
              spellCheck={false}
              autoFocus
              onChange={(event) => set({ symbol: event.target.value.trim().toUpperCase() })}
            />
          )}
          {errors.symbol && <span className="field-error">{errors.symbol}</span>}
        </label>
        <label className="field narrow">
          <span className="label">레버리지</span>
          <input
            type="number"
            min={1}
            max={catalog.max_leverage}
            step={1}
            value={draft.leverage}
            placeholder={`1–${catalog.max_leverage}`}
            onChange={(event) => set({ leverage: event.target.value })}
          />
          {errors.leverage && <span className="field-error">{errors.leverage}</span>}
        </label>
        <label className="field">
          <span className="label">전략</span>
          <select value={draft.strategy} onChange={(event) => chooseStrategy(event.target.value)}>
            <option value="">전략 없음 (진입 안 함)</option>
            {catalog.strategies.map((s) => (
              <option key={s.name} value={s.name}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        {draft.strategy && (
          <label className="field narrow">
            <span className="label">봉 간격</span>
            <select
              value={draft.interval}
              onChange={(event) => set({ interval: event.target.value })}
            >
              {catalog.intervals.map((interval) => (
                <option key={interval} value={interval}>
                  {interval}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      {spec && spec.params.length > 0 && (
        <div className="params">
          <div className="params-head">
            <span className="label">파라미터</span>
            <button
              className="plain small"
              onClick={() => set({ params: defaults(spec.params) })}
              title="이 전략의 기본 파라미터로 되돌립니다 (봉 간격은 그대로)"
            >
              기본값으로
            </button>
          </div>
          <div className="fields">
            {spec.params.map((p) =>
              p.type === 'bool' ? (
                <label className="field check" key={p.name}>
                  <ParameterLabel spec={p} />
                  <span>
                    <input
                      type="checkbox"
                      checked={draft.params[p.name] === 'true'}
                      onChange={(event) =>
                        set({
                          params: { ...draft.params, [p.name]: String(event.target.checked) }
                        })
                      }
                    />{' '}
                    <span className="hint">기본 {p.default}</span>
                  </span>
                </label>
              ) : (
                <label className="field narrow" key={p.name}>
                  <ParameterLabel spec={p} />
                  <input
                    value={draft.params[p.name] ?? ''}
                    inputMode={p.type === 'int' ? 'numeric' : 'decimal'}
                    spellCheck={false}
                    onChange={(event) =>
                      set({ params: { ...draft.params, [p.name]: event.target.value.trim() } })
                    }
                  />
                  {errors[p.name] ? (
                    <span className="field-error">{errors[p.name]}</span>
                  ) : (
                    <span className="hint">기본 {p.default}</span>
                  )}
                </label>
              )
            )}
          </div>
        </div>
      )}

      {draft.strategy && (
        <p className="hint">
          전략은 여러 코인의 백테스트에서 버틴 것만 붙이고, 코인마다 파라미터를 따로 맞추지
          않습니다. 붙이면 엔진이 최근 마감 캔들로 바로 판단해 진입할 수 있습니다.
        </p>
      )}

      <div className="actions">
        <button disabled={!canApply} onClick={() => onApply(args)}>
          {busy === 'set_symbol' ? '보내는 중…' : current ? '적용' : '추가'}
        </button>
        {blocked && <span className="hint">{blocked}</span>}
        <span className="spacer" />
        {current && (
          <button
            className="danger-outline"
            disabled={!idle || positionOpen}
            onClick={() => onRemove(current.symbol)}
          >
            {busy === 'remove_symbol' ? '제거 중…' : '코인 제거'}
          </button>
        )}
      </div>
    </div>
  )
}

// The parameter's name, with its help from the catalog when the engine sends one.
function ParameterLabel({ spec }: { spec: ParameterSpec }): React.JSX.Element {
  return (
    <span className="label">
      {spec.name}
      {spec.description && <HelpTip text={spec.description} />}
    </span>
  )
}

// The symbol's current strategy as set_symbol would carry it, for telling whether the draft
// changes anything.
function currentDraftStrategy(current: SymbolState, catalog: StrategyCatalog): StrategySetting {
  const own = currentDraft(current, catalog)
  return { name: own.strategy, interval: own.interval, params: own.params }
}

export default SymbolEditor
