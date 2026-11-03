import { memo, useState } from 'react'
import type { FundingPayment, RecentTrades, TradeRow } from '../../../shared/engine'
import {
  ROLE_LABELS,
  SIDE_LABELS,
  clock,
  dateTime,
  dayLabel,
  fundingFlow,
  isZero,
  localDay,
  pnlClass,
  signed
} from '../format'

// The recent trades panel (D-51): the last 7 days' orders, newest first, under a heading per
// local date, with the same days' funding payments between them as smaller lines. The engine
// sends every amount, totals included; nothing here adds them.

type Item = { kind: 'trade'; row: TradeRow } | { kind: 'funding'; payment: FundingPayment }

function itemTime(item: Item): string {
  return item.kind === 'trade' ? item.row.time : item.payment.time
}

function itemKey(item: Item): string {
  return item.kind === 'trade'
    ? `${item.row.symbol}:${item.row.order_id}`
    : `funding:${item.payment.symbol}:${item.payment.time}:${item.payment.amount}`
}

/** Both lists come newest first; merged by time, newest first. */
function merge(trades: TradeRow[], funding: FundingPayment[]): Item[] {
  const items: Item[] = [
    ...trades.map((row): Item => ({ kind: 'trade', row })),
    ...funding.map((payment): Item => ({ kind: 'funding', payment }))
  ]
  const ms = (item: Item): number => new Date(itemTime(item)).getTime()
  // A stable sort keeps each list's own order among equal times.
  return items.sort((a, b) => ms(b) - ms(a))
}

function feesText(fees: Record<string, string>): string {
  const parts = Object.entries(fees).map(([asset, amount]) => `${amount} ${asset}`)
  return parts.length > 0 ? parts.join(' + ') : '—'
}

function fundingText(funding: Record<string, string>): string {
  const parts = Object.entries(funding).map(([asset, amount]) => `${fundingFlow(amount)} ${asset}`)
  return parts.length > 0 ? parts.join(' + ') : '0'
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

function FundingItem({ payment }: { payment: FundingPayment }): React.JSX.Element {
  return (
    <li
      className="trade funding"
      title={`${dateTime(payment.time)} 펀딩 정산: 포지션을 들고 있어서 받거나 낸 돈입니다`}
    >
      <div className="trade-line">
        <span className="trade-time">{clock(payment.time)}</span>
        <span className="trade-symbol">{payment.symbol}</span>
        <span>펀딩</span>
        <span className="spacer" />
        <span className={`trade-pnl ${pnlClass(payment.amount)}`}>
          {fundingFlow(payment.amount)} {payment.asset}
        </span>
      </div>
    </li>
  )
}

interface Props {
  trades: RecentTrades | null
  connected: boolean
}

function TradeList({ trades, connected }: Props): React.JSX.Element {
  const [showFunding, setShowFunding] = useState(true)
  const funding = trades?.funding // undefined from an older engine, null while unread
  const days: { key: string; items: Item[] }[] = []
  for (const item of merge(trades?.trades ?? [], showFunding ? (funding ?? []) : [])) {
    const key = localDay(itemTime(item))
    const last = days[days.length - 1]
    if (last?.key === key) last.items.push(item)
    else days.push({ key, items: [item] })
  }
  const totals = trades?.totals

  return (
    <aside className="trades-panel">
      <div className="section-head">
        <h2>거래 기록</h2>
        {trades && <span className="hint">최근 {trades.window_days}일</span>}
        <span className="spacer" />
        {funding && funding.length > 0 && (
          <button
            className="small"
            aria-pressed={showFunding}
            title={
              showFunding ? '펀딩 정산 줄을 숨깁니다' : '거래 사이에 펀딩 정산 줄을 보여 줍니다'
            }
            onClick={() => setShowFunding(!showFunding)}
          >
            펀딩 {showFunding ? '숨기기' : '보기'}
          </button>
        )}
      </div>
      {!trades || !totals ? (
        <p className="muted trades-note">
          {connected
            ? '엔진이 거래 내역을 아직 보내지 않았습니다. 계속 비어 있으면 엔진을 새 코드로 재시작하세요.'
            : '엔진에 연결되면 보입니다.'}
        </p>
      ) : (
        <>
          <div className="trades-totals">
            {trades.trades.length}건 · 실현{' '}
            <span className={pnlClass(totals.realized_pnl)}>{signed(totals.realized_pnl)}</span>{' '}
            {trades.asset} · 수수료 {feesText(totals.fees)}
            {totals.funding && (
              <>
                {' '}
                · 펀딩 <span>{fundingText(totals.funding)}</span>
              </>
            )}
            {totals.net && (
              <>
                {' '}
                ·{' '}
                <span
                  className={pnlClass(totals.net)}
                  title="실현 손익에서 수수료를 빼고 펀딩을 더한 값입니다"
                >
                  순 {signed(totals.net)}
                </span>{' '}
                {trades.asset}
              </>
            )}
            {connected && funding === undefined && (
              <div>펀딩 기록은 엔진을 새 코드로 재시작하면 보입니다.</div>
            )}
            {funding === null && <div>펀딩 기록을 아직 읽지 못했습니다.</div>}
          </div>
          <div className="trades-scroll">
            {days.length === 0 && (
              <p className="muted trades-note">
                최근 {trades.window_days}일 동안 체결된 거래가 없습니다.
              </p>
            )}
            {days.map((day) => (
              <section key={day.key}>
                <h3 className="trades-day">{dayLabel(itemTime(day.items[0]))}</h3>
                <ul>
                  {day.items.map((item) =>
                    item.kind === 'trade' ? (
                      <TradeItem key={itemKey(item)} row={item.row} />
                    ) : (
                      <FundingItem key={itemKey(item)} payment={item.payment} />
                    )
                  )}
                </ul>
              </section>
            ))}
          </div>
        </>
      )}
    </aside>
  )
}

// Drawn again only when its props change: the state comes about once a second, while the list
// changes only with a trades message.
export default memo(TradeList)
