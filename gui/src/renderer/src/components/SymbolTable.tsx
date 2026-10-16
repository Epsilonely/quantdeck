import type { SymbolState } from '../../../shared/engine'
import { isNegative, pnlClass } from '../format'

function SymbolTable({ symbols }: { symbols: SymbolState[] }): React.JSX.Element {
  if (symbols.length === 0) return <p className="muted">설정된 심볼이 없습니다.</p>
  return (
    <table className="symbols">
      <thead>
        <tr>
          <th>심볼</th>
          <th>상태</th>
          <th className="num">마크 가격</th>
          <th>포지션</th>
          <th className="num">진입가</th>
          <th className="num">미실현 손익</th>
          <th>스톱</th>
          <th>주문</th>
        </tr>
      </thead>
      <tbody>
        {symbols.map((s) => (
          <tr key={s.symbol}>
            <td className="strong">{s.symbol}</td>
            <td>
              {s.halted ? (
                <span className="tag danger" title={s.halted}>
                  정지: {s.halted}
                </span>
              ) : (
                <span className="tag ok">정상</span>
              )}
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
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export default SymbolTable
