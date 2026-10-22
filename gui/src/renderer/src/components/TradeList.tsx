import type { RecentTrades, TradeRole, TradeRow } from '../../../shared/engine'
import { clock, dateTime, dayLabel, isZero, localDay, pnlClass, signed } from '../format'

// The recent trades panel (D-51): the last 7 days' orders, newest first, under a heading per
// local date. The engine sends every amount, totals included; nothing here adds them.

const ROLE_LABELS: Record<TradeRole, string> = {
  entry: '진입',
  exit: '청산',
  stop: '손절',
  kill: '킬 스위치',
  liquidation: '강제 청산',
  adl: 'ADL',
  other: '기타'
}

const SIDE_LABELS: Record<string, string> = { BUY: '매수', SELL: '매도' }

function feesText(fees: Record<string, string>): string {
  const parts = Object.entries(fees).map(([asset, amount]) => `${amount} ${asset}`)
  return parts.length > 0 ? parts.join(' + ') : '—'
}

function TradeItem({ row }: { row: TradeRow }): React.JSX.Element {
  const details = [
    dateTime(row.time),
    `주문 ${row.order_id}${row.fills > 1 ? ` · 체결 ${row.fills}건` : ''}`,
    row.client_id
  ].filter(Boolean)
  return (
    <li className="trade" title={details.join('\n')}>
      <div className="trade-line">
        <span className="trade-time">{clock(row.time)}</span>
        <span className="trade-symbol">{row.symbol}</span>
        <span className={`role role-${row.role}`}>{ROLE_LABELS[row.role] ?? row.role}</span>
        <span>{SIDE_LABELS[row.side] ?? row.side}</span>
        <span className="spacer" />
        {isZero(row.realized_pnl) ? (
          <span className="trade-pnl muted">—</span>
        ) : (
          <span className={`trade-pnl ${pnlClass(row.realized_pnl)}`}>
            {signed(row.realized_pnl)}
          </span>
        )}
      </div>
      <div className="trade-line sub">
        <span>
          {row.quantity} @ {row.price}
        </span>
        <span className="trade-fee">수수료 {feesText(row.fees)}</span>
      </div>
    </li>
  )
}

interface Props {
  trades: RecentTrades | null
  connected: boolean
}

function TradeList({ trades, connected }: Props): React.JSX.Element {
  const days: { key: string; rows: TradeRow[] }[] = []
  for (const row of trades?.trades ?? []) {
    const key = localDay(row.time)
    const last = days[days.length - 1]
    if (last?.key === key) last.rows.push(row)
    else days.push({ key, rows: [row] })
  }

  return (
    <aside className="trades-panel">
      <div className="section-head">
        <h2>거래 기록</h2>
        {trades && <span className="hint">최근 {trades.window_days}일</span>}
      </div>
      {!trades ? (
        <p className="muted trades-note">
          {connected
            ? '엔진이 거래 내역을 아직 보내지 않았습니다. 계속 비어 있으면 엔진을 새 코드로 재시작하세요.'
            : '엔진에 연결되면 보입니다.'}
        </p>
      ) : (
        <>
          <div className="trades-totals">
            {trades.trades.length}건 · 실현{' '}
            <span className={pnlClass(trades.totals.realized_pnl)}>
              {signed(trades.totals.realized_pnl)}
            </span>{' '}
            {trades.asset} · 수수료 {feesText(trades.totals.fees)}
          </div>
          <div className="trades-scroll">
            {days.length === 0 && (
              <p className="muted trades-note">
                최근 {trades.window_days}일 동안 체결된 거래가 없습니다.
              </p>
            )}
            {days.map((day) => (
              <section key={day.key}>
                <h3 className="trades-day">{dayLabel(day.rows[0].time)}</h3>
                <ul>
                  {day.rows.map((row) => (
                    <TradeItem key={`${row.symbol}:${row.order_id}`} row={row} />
                  ))}
                </ul>
              </section>
            ))}
          </div>
        </>
      )}
    </aside>
  )
}

export default TradeList
