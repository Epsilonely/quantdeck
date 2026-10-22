import { useEffect, useState } from 'react'
import type { CommandName, CommandResult, EngineCommand } from '../../shared/engine'
import LogView from './components/LogView'
import SymbolEditor, { type EditTarget } from './components/SymbolEditor'
import SymbolTable, { type RowCommand } from './components/SymbolTable'
import ThemeSwitch from './components/ThemeSwitch'
import TradeList from './components/TradeList'
import {
  clock,
  dateTime,
  elapsed,
  isNegative,
  isZero,
  pnlClass,
  riskDayStart,
  signed
} from './format'
import { useEngine } from './useEngine'

const UPTIME_REFRESH_MS = 30_000

const COMMAND_LABELS: Record<CommandName, string> = {
  emergency_close: '긴급 청산',
  lift_halt: '정지 해제',
  stop_engine: '엔진 정지',
  set_symbol: '심볼 설정',
  remove_symbol: '코인 제거',
  pause_symbol: '일시정지',
  close_symbol: '청산·정지',
  resume_symbol: '재개'
}

const ROW_COMMANDS: readonly CommandName[] = ['pause_symbol', 'close_symbol', 'resume_symbol']

function isRowCommand(name: CommandName): name is RowCommand {
  return ROW_COMMANDS.includes(name)
}

interface ShownResult extends CommandResult {
  name: CommandName
}

// The commands of the settings panel, whose failures show in the panel (D-48).
function isSymbolCommand(name: CommandName): boolean {
  return name === 'set_symbol' || name === 'remove_symbol'
}

function targetKey(target: EditTarget): string {
  return target.mode === 'add' ? 'add' : `edit:${target.symbol}`
}

function App(): React.JSX.Element {
  const { status, launch, state, logs, catalog, trades } = useEngine()
  const [showTrades, setShowTrades] = useState(true)
  const [busy, setBusy] = useState<CommandName | null>(null)
  const [busySymbol, setBusySymbol] = useState<string | null>(null) // the symbol `busy` names
  const [result, setResult] = useState<ShownResult | null>(null)
  const [editing, setEditing] = useState<EditTarget | null>(null)
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), UPTIME_REFRESH_MS)
    return () => clearInterval(timer)
  }, [])

  const connected = status?.phase === 'connected'

  const run = async (command: EngineCommand): Promise<void> => {
    const { name } = command
    setBusy(name)
    setBusySymbol('args' in command ? command.args.symbol : null)
    try {
      const reply = await window.api.engine.command(command)
      if (reply) setResult({ name, ...reply })
      // A symbol change that went through closes the settings panel (D-48).
      if (reply?.ok && isSymbolCommand(name)) setEditing(null)
    } catch (e) {
      setResult({ name, ok: false, message: String(e) })
    } finally {
      setBusy(null)
      setBusySymbol(null)
    }
  }

  // A failed symbol change shows in the settings panel while it is open, next to the form.
  const editorFailure =
    editing && result && !result.ok && isSymbolCommand(result.name) ? result : null
  const openEditor = (target: EditTarget | null): void => {
    setEditing(target)
    if (editorFailure) setResult(null) // it belonged to the panel being left
  }

  let editBlocked: string | null = null
  if (!connected) editBlocked = '엔진에 연결되어 있지 않습니다'
  else if (!catalog)
    editBlocked = '이 엔진은 전략 목록을 보내지 않습니다. 엔진을 새 코드로 재시작하세요'
  else if (busy !== null) editBlocked = '다른 명령을 보내는 중입니다'
  let addBlocked = editBlocked
  if (!addBlocked && catalog && state && state.symbols.length >= catalog.max_symbols) {
    addBlocked = `코인은 최대 ${catalog.max_symbols}개입니다`
  }
  // A row's pause, close, and resume (D-50) need no catalog.
  let actionBlocked: string | null = null
  if (!connected) actionBlocked = '엔진에 연결되어 있지 않습니다'
  else if (busy !== null) actionBlocked = '다른 명령을 보내는 중입니다'
  const busyRow =
    busy !== null && busySymbol !== null && isRowCommand(busy)
      ? { name: busy, symbol: busySymbol }
      : null

  let statusText: string
  let statusClass: string
  let statusTitle: string | undefined
  if (!status || status.phase === 'connecting') {
    statusText = '연결 중'
    statusClass = 'info'
  } else if (connected) {
    // The tag's color says it is connected; the dot takes that color.
    statusClass = 'ok'
    const startedAt = state?.started_at
    if (startedAt) {
      statusText = `● 엔진 가동 ${elapsed(startedAt, now)}`
      statusTitle = `연결됨 · 엔진 시작 ${dateTime(startedAt)} · GUI 연결 ${dateTime(status.since)}`
    } else {
      // An engine that doesn't send started_at
      statusText = `● ${clock(status.since)}부터`
      statusTitle = '연결됨'
    }
  } else if (launch.phase === 'starting') {
    statusText = '엔진 시작 중'
    statusClass = 'info'
  } else {
    statusText = `연결 끊김 · ${status.detail ?? ''}`
    statusClass = 'danger'
  }

  // Today's loss is positive when the day lost money (D-24).
  const lossClass = (loss: string | null | undefined): string =>
    loss && !isZero(loss) && !isNegative(loss) ? 'loss' : ''
  const unrealized = state?.daily_loss?.unrealized_pnl
  const withUnrealized = state?.daily_loss?.loss_with_unrealized

  return (
    <div className="app">
      <header className="header">
        <h1>QuantDeck</h1>
        {state && (
          <span className={`tag ${state.network === 'mainnet' ? 'danger' : 'info'}`}>
            {state.network.toUpperCase()}
          </span>
        )}
        <span className={`tag ${statusClass}`} title={statusTitle}>
          {statusText}
        </span>
        <span className="spacer" />
        <button
          className="plain"
          aria-pressed={showTrades}
          title={showTrades ? '거래 기록을 숨깁니다' : '오른쪽에 최근 거래 기록을 보여 줍니다'}
          onClick={() => setShowTrades((shown) => !shown)}
        >
          거래 기록
        </button>
        <ThemeSwitch />
        {connected ? (
          <button disabled={busy !== null} onClick={() => run({ name: 'stop_engine' })}>
            {busy === 'stop_engine' ? '정지 중…' : '엔진 정지'}
          </button>
        ) : (
          <button
            disabled={!status || launch.phase === 'starting'}
            onClick={() => window.api.engine.startEngine()}
          >
            {launch.phase === 'starting' ? '시작 중…' : '엔진 시작'}
          </button>
        )}
        <button
          className="danger"
          disabled={!connected || busy !== null}
          onClick={() => run({ name: 'emergency_close' })}
        >
          {busy === 'emergency_close' ? '청산 중…' : '긴급 청산'}
        </button>
      </header>

      {!connected && launch.phase === 'failed' && (
        <div className="banner danger">
          <div>
            <strong>엔진이 실행되고 있지 않습니다</strong>
            <div className="detail">{launch.message}</div>
          </div>
        </div>
      )}

      {status && !connected && state && (
        <div className="banner warn">
          엔진과 연결되어 있지 않습니다. 아래는 마지막으로 받은 상태입니다 ({dateTime(status.since)}
          에 끊김).
        </div>
      )}

      {state?.halt.halted && (
        <div className="banner danger">
          <div>
            <strong>거래 정지 중</strong>
            {state.halt.since && <> · {dateTime(state.halt.since)}부터</>}
            {state.halt.reason && <div className="detail">{state.halt.reason}</div>}
          </div>
          <span className="spacer" />
          <button disabled={!connected || busy !== null} onClick={() => run({ name: 'lift_halt' })}>
            {busy === 'lift_halt' ? '해제 중…' : '정지 해제'}
          </button>
        </div>
      )}

      {result && !editorFailure && (
        <div className={`banner ${result.ok ? 'ok' : 'danger'}`}>
          <div>
            <strong>
              {COMMAND_LABELS[result.name]} {result.ok ? '완료' : '실패'}
            </strong>
            <div className="detail">{result.message}</div>
          </div>
          <span className="spacer" />
          <button className="plain" onClick={() => setResult(null)}>
            닫기
          </button>
        </div>
      )}

      <div className="workspace">
        <div className="main-column">
          {state ? (
            <>
              <section className="cards">
                <div className="card">
                  <div className="label">오늘 손실 / 한도</div>
                  {state.daily_loss ? (
                    <>
                      <div className="value">
                        <span className={lossClass(state.daily_loss.loss)}>
                          {state.daily_loss.loss}
                        </span>{' '}
                        / {state.daily_loss.limit} {state.daily_loss.asset}
                      </div>
                      {unrealized && withUnrealized && (
                        <div
                          className="sub"
                          title={
                            '열린 포지션의 미실현 손익 합계(마크 가격 기준, 청산 수수료 제외)와 그것까지 ' +
                            '반영한 손실입니다. 일일 손실 한도와 킬스위치는 위의 실현 손실만 셉니다.'
                          }
                        >
                          미실현 <span className={pnlClass(unrealized)}>{signed(unrealized)}</span>{' '}
                          · 포함하면{' '}
                          <span className={lossClass(withUnrealized)}>{withUnrealized}</span>
                        </div>
                      )}
                    </>
                  ) : (
                    <div className="value muted">첫 확인 전</div>
                  )}
                </div>
                <div className="card">
                  <div className="label">기준 잔고 (매일 {riskDayStart()})</div>
                  <div className="value">
                    {state.daily_loss ? `${state.daily_loss.base} ${state.daily_loss.asset}` : '—'}
                  </div>
                </div>
                <div className="card">
                  <div className="label">지갑 잔고</div>
                  {Object.keys(state.balances).length === 0 ? (
                    <div className="value muted">—</div>
                  ) : (
                    Object.entries(state.balances).map(([asset, amount]) => (
                      <div className="value" key={asset}>
                        {amount} {asset}
                      </div>
                    ))
                  )}
                </div>
              </section>

              <section>
                <div className="section-head">
                  <h2>심볼</h2>
                  <button
                    className="small"
                    disabled={addBlocked !== null}
                    title={addBlocked ?? '거래할 코인을 추가합니다'}
                    onClick={() => openEditor({ mode: 'add' })}
                  >
                    코인 추가
                  </button>
                  {connected && !catalog && <span className="hint">{editBlocked}</span>}
                </div>
                <SymbolTable
                  symbols={state.symbols}
                  editing={editing?.mode === 'edit' ? editing.symbol : null}
                  editBlocked={editBlocked}
                  actionBlocked={actionBlocked}
                  busy={busyRow}
                  onEdit={(symbol) => openEditor({ mode: 'edit', symbol })}
                  onCommand={(name, symbol) => run({ name, args: { symbol } })}
                />
                {editing && catalog && (
                  <SymbolEditor
                    key={targetKey(editing)}
                    target={editing}
                    symbols={state.symbols}
                    catalog={catalog}
                    connected={connected}
                    busy={busy}
                    failure={editorFailure}
                    onApply={(args) => run({ name: 'set_symbol', args })}
                    onRemove={(symbol) => run({ name: 'remove_symbol', args: { symbol } })}
                    onClose={() => openEditor(null)}
                  />
                )}
              </section>
            </>
          ) : (
            <p className="muted">엔진 상태를 아직 받지 못했습니다.</p>
          )}

          <section className="log-section">
            <h2>엔진 로그</h2>
            <LogView logs={logs} />
          </section>
        </div>
        {showTrades && <TradeList trades={trades} connected={connected} />}
      </div>
    </div>
  )
}

export default App
