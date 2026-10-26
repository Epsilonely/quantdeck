import type {
  SignalResult,
  StrategyAction,
  StrategySignal,
  SymbolState,
  SymbolStrategy
} from '../../../shared/engine'
import { dateTime, isNegative, pnlClass, shortDateTime, signed } from '../format'

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

const RESULT_LABELS: Record<SignalResult, string> = {
  entered: '진입함',
  closed: '청산함',
  skipped_paused: '건너뜀 · 일시정지 중',
  skipped_halted: '건너뜀 · 거래 정지 중',
  ignored: '무시함',
  not_entered: '진입 안 함 · 로그 참고',
  failed: '요청 실패 · 로그 참고'
}

function resultLabel(signal: StrategySignal): string {
  // Engines before 2026-10-26 send only the text.
  if (!signal.result) return signal.outcome
  if (signal.result === 'ignored') {
    return signal.action === 'exit' ? '무시함 · 포지션 없음' : '무시함 · 이미 포지션 있음'
  }
  // A result this GUI doesn't know yet, from a newer engine, shows as the engine's text.
  return RESULT_LABELS[signal.result] ?? signal.outcome
}

// Whether the signal was carried out. Without a result, as from an older engine, assume it was.
function carriedOut(signal: StrategySignal): boolean {
  return !signal.result || signal.result === 'entered' || signal.result === 'closed'
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

const INTERVAL_UNIT_MS: Record<string, number> = {
  m: 60_000,
  h: 60 * 60_000,
  d: 24 * 60 * 60_000,
  w: 7 * 24 * 60 * 60_000,
  M: 31 * 24 * 60 * 60_000 // the longest month, so a month is never called late too soon
}

// REST's copy of a candle the stream missed is used a minute after its close (D-42); a minute
// more covers the position read and the order.
const DECISION_GRACE_MS = 2 * 60_000

/** A kline interval such as `4h` or `15m` in ms, or null for one this GUI can't read. */
function intervalMs(interval: string | undefined): number | null {
  const match = interval?.match(/^(\d+)([mhdwM])$/)
  return match ? Number(match[1]) * INTERVAL_UNIT_MS[match[2]] : null
}

/** The close of the candle after `strategy`'s last one, if that is more than the grace ago. */
function lateClose(strategy: SymbolStrategy, now: number): number | null {
  const step = intervalMs(strategy.interval)
  const open = strategy.last_candle && new Date(strategy.last_candle.open_time).getTime()
  if (step === null || !open) return null
  const nextClose = open + 2 * step
  return now > nextClose + DECISION_GRACE_MS ? nextClose : null
}

// The candle the strategy last decided on, which shows it still decides when no signal comes,
// and a warning once the next candle is overdue.
function LastDecision({
  strategy,
  now,
  connected
}: {
  strategy: SymbolStrategy
  now: number
  connected: boolean
}): React.JSX.Element {
  const candle = strategy.last_candle
  if (!candle) return <div className="sub muted">판단 전</div>
  // A stopped strategy says so already, and a disconnected engine's state is old anyway.
  const late = connected && !strategy.stopped ? lateClose(strategy, now) : null
  return (
    <div className="sub muted">
      마지막 판단 {shortDateTime(candle.open_time)} 캔들
      {late !== null && (
        <div>
          <span
            className="tag warn"
            title={`${shortDateTime(new Date(late).toISOString())}에 마감한 캔들을 2분이 지나도록 판단하지 않았습니다. 엔진 로그를 확인하세요`}
          >
            판단 늦음
          </span>
        </div>
      )}
    </div>
  )
}

// The last signal and its outcome, and whether this symbol's strategy stopped (D-43).
function LastSignal({
  strategy,
  now,
  connected
}: {
  strategy: SymbolStrategy | null
  now: number
  connected: boolean
}): React.JSX.Element {
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
        // Only a signal that was carried out gets its color, so a skipped one doesn't read as an
        // entry. The engine's own text stays in the tooltip.
        <div title={signal.outcome}>
          <span className={carriedOut(signal) ? undefined : 'muted'}>
            <span className={carriedOut(signal) ? ACTION_CLASSES[signal.action] : undefined}>
              {ACTION_LABELS[signal.action]}
            </span>
            {signal.stop_price && <> · 스톱 {signal.stop_price}</>}
          </span>
          <div className="muted">
            {shortDateTime(signal.candle_open_time)} 캔들 · {resultLabel(signal)}
          </div>
        </div>
      ) : (
        !strategy.stopped && <div className="muted">아직 없음</div>
      )}
      <LastDecision strategy={strategy} now={now} connected={connected} />
    </>
  )
}

// The symbol's stops, and its open orders only when there are any: the engine places market
// orders alone, so one that rests is a stop's order filling, or one the engine didn't place.
function Stops({ symbol }: { symbol: SymbolState }): React.JSX.Element {
  const { stops, orders, position } = symbol
  const orderLines = orders.map(
    (order) =>
      `${order.side} ${order.type} ${order.quantity}${order.reduce_only ? ' reduceOnly' : ''} · ${order.client_id}`
  )
  return (
    <>
      {stops.map((stop) => (
        <div key={stop.client_algo_id} title={stop.client_algo_id}>
          {stop.side} {stop.type} @ {stop.trigger_price}
          {stop.close_position ? ' (closePosition)' : ''}
        </div>
      ))}
      {stops.length === 0 && position && (
        <div>
          <span className="tag danger">스톱 없음</span>
        </div>
      )}
      {orders.length > 0 && (
        <div>
          <span
            className="tag warn"
            title={[
              '엔진은 시장가 주문만 내서 미체결 주문이 보통 없습니다. 손절이 발동한 직후 잠깐 ' +
                '보일 수 있고, 계속 남아 있으면 엔진이 걸지 않은 주문입니다.',
              ...orderLines
            ].join('\n')}
          >
            미체결 주문 {orders.length}건
          </span>
        </div>
      )}
      {stops.length === 0 && !position && orders.length === 0 && <span className="muted">—</span>}
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
  now: number // ms, for telling an overdue decision
  connected: boolean
  onEdit(symbol: string): void
  onCommand(name: RowCommand, symbol: string): void
}

function SymbolTable({
  symbols,
  editing,
  editBlocked,
  actionBlocked,
  busy,
  now,
  connected,
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
              {s.position?.roi_pct && (
                <div
                  className="sub"
                  title={`진입 증거금(진입가 × 수량 ÷ 레버리지 ${s.leverage ?? '?'}x) 대비 수익률입니다. 가격 변동률 × 레버리지와 같고, 수수료와 펀딩은 빠져 있습니다`}
                >
                  {signed(s.position.roi_pct)}%
                </div>
              )}
            </td>
            <td>
              <Stops symbol={s} />
            </td>
            <td>
              <LastSignal strategy={s.strategy ?? null} now={now} connected={connected} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export default SymbolTable
