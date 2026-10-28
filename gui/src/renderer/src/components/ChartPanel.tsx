import { useEffect, useRef, useState } from 'react'
import {
  CandlestickSeries,
  ColorType,
  LineStyle,
  createChart,
  type AutoscaleInfo,
  type CandlestickData,
  type IChartApi,
  type IPriceLine,
  type IPrimitivePaneView,
  type ISeriesApi,
  type ISeriesPrimitive,
  type MouseEventParams,
  type SeriesAttachedParameter,
  type Time,
  type UTCTimestamp
} from 'lightweight-charts'
import type {
  ChartCandle,
  RecentTrades,
  SymbolCandles,
  SymbolState,
  TradeRole,
  TradeRow
} from '../../../shared/engine'
import {
  ROLE_LABELS,
  SIDE_LABELS,
  isNegative,
  isZero,
  pnlClass,
  shortDateTime,
  signed
} from '../format'

// The chart of one of the engine's symbols (D-54): its strategy's candles, the position's entry
// price, its exchange-side stops, and the last 7 days' fills. Prices arrive as text; they become
// numbers here only to be drawn, and nothing is computed from them.

interface Palette {
  background: string
  text: string
  grid: string
  font: string
  profit: string
  loss: string
  warn: string
  fills: Record<TradeRole, string> // the fill dots' colors
}

// main.css holds every color; the chart takes them in JavaScript (techContext.md, GUI notes).
function readPalette(): Palette {
  const style = getComputedStyle(document.documentElement)
  const color = (name: string): string => style.getPropertyValue(name).trim()
  return {
    background: color('--bg-sunken'),
    text: color('--text-muted'),
    grid: color('--border'),
    font: color('--mono'),
    profit: color('--profit'),
    loss: color('--loss'),
    warn: color('--warn'),
    // Liquidation and ADL share the kill switch's color.
    fills: {
      entry: color('--fill-entry'),
      exit: color('--fill-exit'),
      stop: color('--fill-stop'),
      kill: color('--fill-kill'),
      liquidation: color('--fill-kill'),
      adl: color('--fill-kill'),
      other: color('--text-muted')
    }
  }
}

/** The palette, read again whenever the theme changes (D-35). */
function usePalette(): Palette {
  const [palette, setPalette] = useState(readPalette)
  useEffect(() => {
    const query = window.matchMedia('(prefers-color-scheme: light)')
    const changed = (): void => setPalette(readPalette())
    query.addEventListener('change', changed)
    return () => query.removeEventListener('change', changed)
  }, [])
  return palette
}

// lightweight-charts shows timestamps as if they were UTC; shifting them by the local offset
// shows local time, as everywhere else in the GUI.
function chartTime(iso: string): UTCTimestamp {
  const ms = Date.parse(iso)
  return (ms / 1000 - new Date(ms).getTimezoneOffset() * 60) as UTCTimestamp
}

function bar(candle: ChartCandle): CandlestickData<Time> {
  return {
    time: chartTime(candle.open_time),
    open: Number(candle.open),
    high: Number(candle.high),
    low: Number(candle.low),
    close: Number(candle.close)
  }
}

/** The decimals a price carries, so the axis shows the symbol's tick (`60050.10` → 2). */
function decimals(candle: ChartCandle): number {
  const prices = [candle.open, candle.high, candle.low, candle.close]
  return Math.max(...prices.map((price) => price.split('.')[1]?.length ?? 0))
}

/** The chart's bar times, ascending: the closed candles', then the forming one's if newer. */
function barTimes(closed: SymbolCandles | null, formingOpen: string | null): UTCTimestamp[] {
  const times = (closed?.candles ?? []).map((c) => chartTime(c.open_time))
  const last = times.at(-1)
  if (formingOpen !== null && last !== undefined && chartTime(formingOpen) > last)
    times.push(chartTime(formingOpen))
  return times
}

/** The last bar that opened at or before `time`; `times` ascending and not empty. */
function barAt(times: UTCTimestamp[], time: number): UTCTimestamp {
  let low = 0
  let high = times.length - 1
  while (low < high) {
    const middle = (low + high + 1) >> 1
    if (times[middle] <= time) low = middle
    else high = middle - 1
  }
  return times[low]
}

interface ChartFill {
  row: TradeRow
  bar: UTCTimestamp // the bar it happened in
}

/** The fills of `symbol` from the chart's first bar on, oldest first; `times` from barTimes. */
function chartFills(
  trades: RecentTrades | null,
  symbol: string,
  times: UTCTimestamp[]
): ChartFill[] {
  if (!trades || times.length === 0) return []
  return trades.trades
    .filter((row) => row.symbol === symbol && chartTime(row.time) >= times[0])
    .sort((a, b) => chartTime(a.time) - chartTime(b.time))
    .map((row) => ({ row, bar: barAt(times, chartTime(row.time)) }))
}

interface Dot {
  time: UTCTimestamp
  price: number
  color: string
}

const DOT_RADIUS = 3.5 // px

/**
 * A dot per fill at its price on its bar, in its role's color; hovering the bar tells the rest.
 * Drawn here rather than as the library's markers, whose dots are at least 11 px and grow with the
 * zoom: these stay small at every zoom, and are drawn over the candles and the price lines.
 */
class FillDots implements ISeriesPrimitive<Time> {
  private dots: Dot[] = []
  private host: SeriesAttachedParameter<Time> | null = null

  private readonly view: IPrimitivePaneView = {
    renderer: () => ({
      draw: (target) =>
        target.useMediaCoordinateSpace(({ context }) => {
          if (!this.host) return
          const { chart, series } = this.host
          for (const dot of this.dots) {
            const x = chart.timeScale().timeToCoordinate(dot.time)
            const y = series.priceToCoordinate(dot.price)
            if (x === null || y === null) continue
            context.fillStyle = dot.color
            context.beginPath()
            context.arc(x, y, DOT_RADIUS, 0, 2 * Math.PI)
            context.fill()
          }
        })
    })
  }

  attached(host: SeriesAttachedParameter<Time>): void {
    this.host = host
  }

  detached(): void {
    this.host = null
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view]
  }

  set(dots: Dot[]): void {
    this.dots = dots
    this.host?.requestUpdate()
  }
}

function fillDots(fills: ChartFill[], palette: Palette): Dot[] {
  return fills.map(({ row, bar }) => ({
    time: bar,
    price: Number(row.price),
    color: palette.fills[row.role] ?? palette.fills.other
  }))
}

// The legend's roles; liquidation and ADL share the kill switch's color.
const LEGEND_ROLES: TradeRole[] = ['entry', 'exit', 'stop', 'kill']

/** The bar under the pointer, and where the fill details go: on the side away from it. */
interface Hover {
  bar: number
  right: number | null // the box's distance from the frame's right edge, or null at the left
}

interface PriceLineSpec {
  price: string
  color: 'profit' | 'loss' | 'warn'
  style: LineStyle
  title: string
}

/** The position's entry price and each exchange-side stop, as lines across the chart. */
function priceLines(symbol: SymbolState | undefined): PriceLineSpec[] {
  if (!symbol) return []
  const lines: PriceLineSpec[] = []
  const position = symbol.position
  if (position) {
    const short = isNegative(position.amount)
    lines.push({
      price: position.entry_price,
      color: short ? 'loss' : 'profit',
      style: LineStyle.Solid,
      title: `진입 ${short ? '숏' : '롱'} ${position.amount.replace(/^-/, '')}`
    })
  }
  for (const stop of symbol.stops) {
    lines.push({ price: stop.trigger_price, color: 'warn', style: LineStyle.Dashed, title: '손절' })
  }
  return lines
}

interface ChartParts {
  chart: IChartApi
  series: ISeriesApi<'Candlestick'>
  dots: FillDots
  lines: IPriceLine[]
}

// The view range buttons: the latest so many bars, or all of them.
const RANGES: [label: string, bars: number | null][] = [
  ['50봉', 50],
  ['100봉', 100],
  ['전체', null]
]

interface Props {
  symbols: SymbolState[]
  candles: Record<string, SymbolCandles>
  trades: RecentTrades | null
  selected: string // one of `symbols`
  onSelect(symbol: string): void
}

function ChartPanel({ symbols, candles, trades, selected, onSelect }: Props): React.JSX.Element {
  const palette = usePalette()
  const container = useRef<HTMLDivElement>(null)
  const parts = useRef<ChartParts | null>(null)
  const drawn = useRef<{ view: string; candles: SymbolCandles | null }>({ view: '', candles: null })
  const [hover, setHover] = useState<Hover | null>(null)

  const symbol = symbols.find((s) => s.symbol === selected)
  const strategy = symbol?.strategy ?? null
  // A list for another interval is from the strategy this one replaced.
  const sent = candles[selected]
  const closed = strategy && sent?.interval === strategy.interval ? sent : null
  const forming = strategy?.forming_candle ?? null
  const view = `${selected} ${strategy?.interval ?? ''}`

  let note: string | null = null
  if (!strategy) note = '전략을 정하면 그 주기의 차트가 나옵니다'
  else if (!('forming_candle' in strategy))
    note = '이 엔진은 차트 캔들을 보내지 않습니다. 엔진을 새 코드로 재시작하세요'
  else if (!closed) note = '캔들을 기다리는 중입니다'

  const formingOpen = forming?.open_time ?? null
  const linesKey = JSON.stringify(priceLines(symbol))
  const hovered =
    hover === null
      ? []
      : chartFills(trades, selected, barTimes(closed, formingOpen)).filter(
          (fill) => fill.bar === hover.bar
        )

  useEffect(() => {
    const element = container.current
    if (!element) return
    const chart = createChart(element, {
      autoSize: true,
      localization: { locale: 'ko-KR' },
      timeScale: { timeVisible: true, secondsVisible: false },
      // Dragging pans and dragging an axis zooms it. The wheel zooms the time around the pointer,
      // but only with Ctrl: see passWheel.
      handleScroll: { mouseWheel: false },
      handleScale: { mouseWheel: true }
    })
    // The chart sits in the scrolling page, so a plain wheel stops here, before the chart, and
    // scrolls the page. With Ctrl (a touchpad pinch arrives as one) the chart takes it, which
    // also keeps it from zooming the whole window.
    const passWheel = (event: WheelEvent): void => {
      if (!event.ctrlKey) event.stopPropagation()
    }
    element.addEventListener('wheel', passWheel, { capture: true })
    // Set only when the bar or the side changes, not with every move of the pointer.
    chart.subscribeCrosshairMove((param: MouseEventParams<Time>) => {
      const bar = param.point && typeof param.time === 'number' ? param.time : null
      const axis = chart.priceScale('right').width()
      const right =
        param.point && param.point.x < (element.clientWidth - axis) / 2 ? axis + 8 : null
      setHover((old) =>
        bar === null ? null : old?.bar === bar && old.right === right ? old : { bar, right }
      )
    })
    const series = chart.addSeries(CandlestickSeries, { borderVisible: false })
    const dots = new FillDots()
    series.attachPrimitive(dots)
    parts.current = { chart, series, dots, lines: [] }
    drawn.current = { view: '', candles: null }
    return () => {
      element.removeEventListener('wheel', passWheel, { capture: true })
      parts.current = null
      chart.remove()
    }
  }, [])

  useEffect(() => {
    const p = parts.current
    if (!p) return
    p.chart.applyOptions({
      layout: {
        background: { type: ColorType.Solid, color: palette.background },
        textColor: palette.text,
        fontFamily: palette.font,
        fontSize: 11
      },
      grid: { vertLines: { color: palette.grid }, horzLines: { color: palette.grid } },
      rightPriceScale: { borderColor: palette.grid },
      timeScale: { borderColor: palette.grid }
    })
    p.series.applyOptions({
      upColor: palette.profit,
      downColor: palette.loss,
      wickUpColor: palette.profit,
      wickDownColor: palette.loss
    })
  }, [palette])

  // Candles: the whole list when it changes, then the forming candle on its own.
  useEffect(() => {
    const p = parts.current
    if (!p) return
    const latest = closed?.candles.at(-1)
    const newer =
      forming && latest && chartTime(forming.open_time) > chartTime(latest.open_time)
        ? forming
        : null
    const last = drawn.current
    if (closed !== last.candles || view !== last.view) {
      const bars = (closed?.candles ?? []).map(bar)
      if (newer) bars.push(bar(newer))
      if (latest) {
        const precision = decimals(latest)
        p.series.applyOptions({
          priceFormat: { type: 'price', precision, minMove: 10 ** -precision }
        })
      }
      p.series.setData(bars)
      // Another symbol or interval opens at its latest candles with its prices fitted, not
      // scrolled or zoomed as the last one was left. Dragging the price axis turns its
      // autoscale off, which resetting the time scale doesn't undo.
      if (view !== last.view) {
        p.chart.timeScale().resetTimeScale()
        p.series.priceScale().applyOptions({ autoScale: true })
      }
      drawn.current = { view, candles: closed }
    } else if (newer) {
      p.series.update(bar(newer))
    }
  }, [closed, forming, view])

  // Keyed by the lines' text, so they are drawn again only when one changes, not with every state.
  useEffect(() => {
    const p = parts.current
    if (!p) return
    const specs = JSON.parse(linesKey) as PriceLineSpec[]
    for (const line of p.lines) p.series.removePriceLine(line)
    p.lines = specs.map((spec) =>
      p.series.createPriceLine({
        price: Number(spec.price),
        color: palette[spec.color],
        lineWidth: 1,
        lineStyle: spec.style,
        axisLabelVisible: true,
        title: spec.title
      })
    )
    // The price axis fits these lines along with the candles in view, so the entry and stops stay
    // on screen without dragging the axis.
    const prices = specs.map((spec) => Number(spec.price))
    p.series.applyOptions({
      autoscaleInfoProvider: (candles: () => AutoscaleInfo | null) => {
        const info = candles()
        if (!info?.priceRange || prices.length === 0) return info
        const { minValue, maxValue } = info.priceRange
        return {
          ...info,
          priceRange: {
            minValue: Math.min(minValue, ...prices),
            maxValue: Math.max(maxValue, ...prices)
          }
        }
      }
    })
  }, [linesKey, palette])

  // Placed again when a bar is added, not with every update of the forming one.
  useEffect(() => {
    const fills = chartFills(trades, selected, barTimes(closed, formingOpen))
    parts.current?.dots.set(fillDots(fills, palette))
  }, [trades, selected, closed, formingOpen, palette])

  // A range button also fits the prices again, as an axis dragged before would have left them.
  function showBars(bars: number | null): void {
    const p = parts.current
    if (!p) return
    const count = p.series.data().length
    if (bars === null || count <= bars) p.chart.timeScale().fitContent()
    else p.chart.timeScale().setVisibleLogicalRange({ from: count - bars - 0.5, to: count - 0.5 })
    p.series.priceScale().applyOptions({ autoScale: true })
  }

  return (
    <section className="chart-section">
      <div className="section-head">
        <h2>차트</h2>
        <div className="chart-tabs" role="tablist">
          {symbols.map((s) => (
            <button
              key={s.symbol}
              role="tab"
              className="small"
              aria-selected={s.symbol === selected}
              onClick={() => onSelect(s.symbol)}
            >
              {s.symbol}
              <span className="muted"> · {s.strategy?.interval ?? '전략 없음'}</span>
            </button>
          ))}
        </div>
        <div className="chart-range">
          {RANGES.map(([label, bars]) => (
            <button
              key={label}
              className="small"
              disabled={note !== null}
              onClick={() => showBars(bars)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="chart-frame">
        <div className="chart" ref={container} />
        {hover && hovered.length > 0 && (
          <ul
            className="chart-fills"
            style={hover.right === null ? { left: 8 } : { right: hover.right }}
          >
            {hovered.map(({ row }) => (
              <li key={row.order_id}>
                <span className="trade-time">{shortDateTime(row.time)}</span>
                <span className={`role role-${row.role}`}>{ROLE_LABELS[row.role] ?? row.role}</span>
                <span>{SIDE_LABELS[row.side] ?? row.side}</span>
                <span className="mono">
                  {row.quantity} @ {row.price}
                </span>
                {!isZero(row.realized_pnl) && (
                  <span className={`mono ${pnlClass(row.realized_pnl)}`}>
                    {signed(row.realized_pnl)}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
        {note && <div className="chart-note">{note}</div>}
      </div>
      <div className="chart-legend">
        {strategy?.name && (
          <span>
            {selected} · {strategy.interval} · {strategy.name}
          </span>
        )}
        <span className="spacer" />
        <span className="legend-entry">― 진입가</span>
        <span
          className="legend-stop"
          title="손절은 마크 가격으로 발동합니다. 캔들은 마지막 체결가라서, 캔들이 손절선에 닿아도 손절되지 않았을 수 있고 그 반대도 있습니다"
        >
          ┄ 손절가
        </span>
        <span className="legend-fills">
          최근 7일 체결
          {LEGEND_ROLES.map((role) => (
            <span key={role}>
              <span className={`fill-${role}`}>●</span> {ROLE_LABELS[role]}
            </span>
          ))}
        </span>
        {strategy?.stopped && (
          <span className="tag danger" title={strategy.stopped}>
            전략이 멈춰 차트가 갱신되지 않습니다
          </span>
        )}
      </div>
    </section>
  )
}

export default ChartPanel
