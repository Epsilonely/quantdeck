import { useState } from 'react'
import type { CommandName, CommandResult } from '../../shared/engine'
import LogView from './components/LogView'
import SymbolTable from './components/SymbolTable'
import { clock, dateTime, isNegative, isZero, riskDayStart } from './format'
import { useEngine } from './useEngine'

const COMMAND_LABELS: Record<CommandName, string> = {
  emergency_close: '긴급 청산',
  lift_halt: '정지 해제',
  stop_engine: '엔진 정지'
}

interface ShownResult extends CommandResult {
  name: CommandName
}

function App(): React.JSX.Element {
  const { status, launch, state, logs } = useEngine()
  const [busy, setBusy] = useState<CommandName | null>(null)
  const [result, setResult] = useState<ShownResult | null>(null)

  const connected = status?.phase === 'connected'

  const run = async (name: CommandName): Promise<void> => {
    setBusy(name)
    try {
      const reply = await window.api.engine.command(name)
      if (reply) setResult({ name, ...reply })
    } catch (e) {
      setResult({ name, ok: false, message: String(e) })
    } finally {
      setBusy(null)
    }
  }

  let statusText: string
  let statusClass: string
  if (!status || status.phase === 'connecting') {
    statusText = '연결 중'
    statusClass = 'info'
  } else if (connected) {
    statusText = `연결됨 · ${clock(status.since)}부터`
    statusClass = 'ok'
  } else if (launch.phase === 'starting') {
    statusText = '엔진 시작 중'
    statusClass = 'info'
  } else {
    statusText = `연결 끊김 · ${status.detail ?? ''}`
    statusClass = 'danger'
  }

  // Today's loss is positive when the day lost money (D-24).
  const loss = state?.daily_loss?.loss
  const lossClass = loss && !isZero(loss) && !isNegative(loss) ? 'loss' : ''

  return (
    <div className="app">
      <header className="header">
        <h1>QuantDeck</h1>
        {state && (
          <span className={`tag ${state.network === 'mainnet' ? 'danger' : 'info'}`}>
            {state.network.toUpperCase()}
          </span>
        )}
        <span className={`tag ${statusClass}`}>{statusText}</span>
        <span className="spacer" />
        {connected ? (
          <button disabled={busy !== null} onClick={() => run('stop_engine')}>
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
          onClick={() => run('emergency_close')}
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
          <button disabled={!connected || busy !== null} onClick={() => run('lift_halt')}>
            {busy === 'lift_halt' ? '해제 중…' : '정지 해제'}
          </button>
        </div>
      )}

      {result && (
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

      {state ? (
        <>
          <section className="cards">
            <div className="card">
              <div className="label">오늘 손실 / 한도</div>
              {state.daily_loss ? (
                <div className="value">
                  <span className={lossClass}>{state.daily_loss.loss}</span> /{' '}
                  {state.daily_loss.limit} {state.daily_loss.asset}
                </div>
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
            <h2>심볼</h2>
            <SymbolTable symbols={state.symbols} />
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
  )
}

export default App
