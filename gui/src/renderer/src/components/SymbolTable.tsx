import type { StrategyAction, SymbolState, SymbolStrategy } from '../../../shared/engine'
import { dateTime, isNegative, pnlClass, shortDateTime } from '../format'

// What a row's buttons send (D-50).
export type RowCommand = 'pause_symbol' | 'close_symbol' | 'resume_symbol'

const ROW_BUSY_LABELS: Record<RowCommand, string> = {
  pause_symbol: '정지 중…',
  close_symbol: '청산 중…',
  resume_symbol: '재개 중…'
}

const ACTION_LABELS: Record<StrategyAction, string> = {
  enter_long: '롱 진입',
  enter_short: '숏 진입',
  exit: '청산'
}

const ACTION_CLASSES: Record<StrategyAction, string> = {
  enter_long: 'profit',
  enter_short: 'loss',
  exit: ''
}

// The symbol's leverage and strategy setting (D-47), with every parameter below it.
function Setting({ symbol }: { symbol: SymbolState }): React.JSX.Element {
  const strategy = symbol.strategy
  const params = Object.entries(strategy?.params ?? {})
    .map(([key, value]) => `${key}=${value}`)
    .join(', ')
  return (
    <>
      {symbol.leverage !== undefined && <div>{symbol.leverage}x</div>}
      {!strategy ? (
        <span className="muted" title="전략이 없어 진입하지 않습니다">
          전략 없음
        </span>
      ) : strategy.name ? (
        <div>
          {strategy.name} {strategy.interval}
          {params && <div className="muted">{params}</div>}
        </div>
      ) : (
        <span className="muted">—</span>
      )}
    </>
  )
}

function LastCandle({ strategy }: { strategy: SymbolStrategy | null }): React.JSX.Element {
  const candle = strategy?.last_candle
  if (!candle) return <span className="muted">{strategy ? '판단 전' : '—'}</span>
  return (
    <>
      {shortDateTime(candle.open_time)} · {candle.close}
    </>
  )
}

// The last signal and its outcome, and whether this symbol's strategy stopped (D-43).
function LastSignal({ strategy }: { strategy: SymbolStrategy | null }): React.JSX.Element {
  if (!strategy) return <span className="muted">—</span>
  const signal = strategy.last_signal
  return (
    <>
      {strategy.stopped && (
        <div>
          <span className="tag danger" title={strategy.stopped}>
            전략 중단: {strategy.stopped}
          </span>
        </div>
      )}
      {signal ? (
        <div title={signal.outcome}>
          <span className={ACTION_CLASSES[signal.action]}>{ACTION_LABELS[signal.action]}</span>
          {signal.stop_price && <> · 스톱 {signal.stop_price}</>}
          <div className="muted">
            {shortDateTime(signal.candle_open_time)} 캔들 · {signal.outcome}
          </div>
        </div>
      ) : (
        !strategy.stopped && <span className="muted">아직 없음</span>
      )}
    </>
  )
}

// The user's pause (D-50) and the engine's own halt (D-16, D-19, D-20), each in its own tag.
function Status({ symbol }: { symbol: SymbolState }): React.JSX.Element {
  const paused = symbol.paused_at
  if (!paused && !symbol.halted) return <span className="tag ok">정상</span>
  return (
    <>
      {paused && (
        <div>
          <span
            className="tag warn"
            title={`${dateTime(paused)}부터 새 진입을 멈췄습니다. 포지션과 손절, 청산 신호는 그대로입니다`}
          >
            일시정지 · {shortDateTime(paused)}
          </span>
        </div>
      )}
      {symbol.halted && (
        <div>
          <span className="tag danger" title={symbol.halted}>
            정지: {symbol.halted}
          </span>
        </div>
      )}
    </>
  )
}

interface ActionsProps {
  symbol: SymbolState
  blocked: string | null
  busy: RowCommand | null // the command in progress for this symbol
  onCommand(name: RowCommand): void
}

// Pause or resume the symbol, and close its position with a pause (D-50). The other symbols trade
// on; the account-wide emergency close stays in the header.
function Actions({ symbol, blocked, busy, onCommand }: ActionsProps): React.JSX.Element {
  // Engines before 2026-10-22 send no paused_at and don't know these commands.
  const reason =
    symbol.paused_at === undefined
      ? '이 엔진은 코인별 정지를 모릅니다. 엔진을 새 코드로 재시작하세요'
      : blocked
  const button = (
    name: RowCommand,
    label: string,
    title: string,
    className = 'small'
  ): React.JSX.Element => (
    <button
      className={className}
      disabled={reason !== null}
      title={reason ?? title}
      onClick={() => onCommand(name)}
    >
      {busy === name ? ROW_BUSY_LABELS[name] : label}
    </button>
  )
  return (
    <>
      {symbol.paused_at
        ? button('resume_symbol', '재개', '일시정지를 풀고 다음 진입 신호부터 다시 진입합니다')
        : button(
            'pause_symbol',
            '일시정지',
            '이 코인만 새 진입을 멈춥니다. 포지션과 손절, 청산 신호는 그대로입니다'
          )}
      {symbol.position &&
        button(
          'close_symbol',
          '청산·정지',
          '이 코인만 새 진입을 막고 포지션을 시장가 reduceOnly로 청산합니다',
          'small danger-outline'
        )}
    </>
  )
}

interface Props {
  symbols: SymbolState[]
  editing: string | null // the symbol open in the settings panel
  editBlocked: string | null // why settings can't be changed now
  actionBlocked: string | null // why a row's pause, close, or resume can't be sent now
  busy: { name: RowCommand; symbol: string } | null
  onEdit(symbol: string): void
  onCommand(name: RowCommand, symbol: string): void
}

function SymbolTable({
  symbols,
  editing,
  editBlocked,
  actionBlocked,
  busy,
  onEdit,
  onCommand
}: Props): React.JSX.Element {
  if (symbols.length === 0) return <p className="muted">설정된 심볼이 없습니다.</p>
  return (
    <table className="symbols">
      <thead>
        <tr>
          <th>심볼</th>
          <th>상태</th>
          <th>설정</th>
          <th className="num">마크 가격</th>
          <th>포지션</th>
          <th className="num">진입가</th>
          <th className="num">미실현 손익</th>
          <th>스톱</th>
          <th>주문</th>
          <th>마지막 캔들</th>
          <th>전략 신호</th>
        </tr>
      </thead>
      <tbody>
        {symbols.map((s) => (
          <tr key={s.symbol} className={s.symbol === editing ? 'editing' : undefined}>
            <td className="strong">{s.symbol}</td>
            <td>
              <Status symbol={s} />
              <Actions
                symbol={s}
                blocked={actionBlocked}
                busy={busy?.symbol === s.symbol ? busy.name : null}
                onCommand={(name) => onCommand(name, s.symbol)}
              />
            </td>
            <td>
              <Setting symbol={s} />
              <button
                className="small"
                disabled={editBlocked !== null}
                title={editBlocked ?? `${s.symbol}의 레버리지와 전략을 바꿉니다`}
                onClick={() => onEdit(s.symbol)}
              >
                변경
              </button>
            </td>
            <td className="num">{s.mark_price ?? '—'}</td>
            <td>
              {s.position ? (
                <span className={isNegative(s.position.amount) ? 'loss' : 'profit'}>
                  {isNegative(s.position.amount) ? 'SHORT' : 'LONG'}{' '}
                  {s.position.amount.replace(/^-/, '')}
                </span>
              ) : (
                <span className="muted">없음</span>
              )}
            </td>
            <td className="num">{s.position?.entry_price ?? '—'}</td>
            <td className={`num ${pnlClass(s.position?.unrealized_pnl ?? null)}`}>
              {s.position?.unrealized_pnl ?? '—'}
            </td>
            <td>
              {s.stops.length === 0 ? (
                s.position ? (
                  <span className="tag danger">스톱 없음</span>
                ) : (
                  <span className="muted">—</span>
                )
              ) : (
                s.stops.map((stop) => (
                  <div key={stop.client_algo_id} title={stop.client_algo_id}>
                    {stop.side} {stop.type} @ {stop.trigger_price}
                    {stop.close_position ? ' (closePosition)' : ''}
                  </div>
                ))
              )}
            </td>
            <td>
              {s.orders.length === 0 ? (
                <span className="muted">—</span>
              ) : (
                s.orders.map((order) => (
                  <div key={order.client_id} title={order.client_id}>
                    {order.side} {order.type} {order.quantity}
                    {order.reduce_only ? ' (reduceOnly)' : ''}
                  </div>
                ))
              )}
            </td>
            <td>
              <LastCandle strategy={s.strategy ?? null} />
            </td>
            <td>
              <LastSignal strategy={s.strategy ?? null} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export default SymbolTable
