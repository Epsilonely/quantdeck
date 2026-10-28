import { useEffect, useRef, useState } from 'react'
import {
  CandlestickSeries,
  ColorType,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type CandlestickData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp
} from 'lightweight-charts'
import type {
  ChartCandle,
  RecentTrades,
  SymbolCandles,
  SymbolState,
  TradeRole
} from '../../../shared/engine'
import { ROLE_LABELS, isNegative } from '../format'

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
  info: string
  ok: string
  warn: string
  danger: string
  muted: string
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
    info: color('--info'),
    ok: color('--ok'),
    warn: color('--warn'),
    danger: color('--danger'),
    muted: color('--text-muted')
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

// The trades panel's badge colors, so a marker and its row read alike.
const ROLE_COLORS: Record<TradeRole, keyof Palette> = {
  entry: 'info',
  exit: 'ok',
  stop: 'warn',
  kill: 'danger',
  liquidation: 'danger',
  adl: 'danger',
  other: 'muted'
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

/** A marker per fill of `symbol` on the bar it happened in, oldest first. */
function tradeMarkers(
  trades: RecentTrades | null,
  symbol: string,
  times: UTCTimestamp[],
  palette: Palette
): SeriesMarker<Time>[] {
  if (!trades || times.length === 0) return []
  return trades.trades
    .filter((row) => row.symbol === symbol && chartTime(row.time) >= times[0])
    .map((row): SeriesMarker<Time> => {
      const buy = row.side === 'BUY'
      return {
        time: barAt(times, chartTime(row.time)),
        position: buy ? 'belowBar' : 'aboveBar',
        shape: buy ? 'arrowUp' : 'arrowDown',
        color: palette[ROLE_COLORS[row.role] ?? 'muted'],
        text: ROLE_LABELS[row.role] ?? row.role
      }
    })
    .sort((a, b) => (a.time as number) - (b.time as number))
}

interface PriceLineSpec {
  price: string
  color: keyof Palette
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
  markers: ISeriesMarkersPluginApi<Time>
  lines: IPriceLine[]
}

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

  useEffect(() => {
    const element = container.current
    if (!element) return
    const chart = createChart(element, {
      autoSize: true,
      localization: { locale: 'ko-KR' },
      timeScale: { timeVisible: true, secondsVisible: false },
      // The wheel scrolls the page, which the chart sits in; dragging pans, and dragging an axis
      // zooms.
      handleScroll: { mouseWheel: false },
      handleScale: { mouseWheel: false }
    })
    const series = chart.addSeries(CandlestickSeries, { borderVisible: false })
    parts.current = { chart, series, markers: createSeriesMarkers(series, []), lines: [] }
    drawn.current = { view: '', candles: null }
    return () => {
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
      // Another symbol or interval opens at its latest candles, not where the last one was left.
      if (view !== last.view) p.chart.timeScale().resetTimeScale()
      drawn.current = { view, candles: closed }
    } else if (newer) {
      p.series.update(bar(newer))
    }
  }, [closed, forming, view])

  // Keyed by the lines' text, so they are drawn again only when one changes, not with every state.
  useEffect(() => {
    const p = parts.current
    if (!p) return
    for (const line of p.lines) p.series.removePriceLine(line)
    p.lines = (JSON.parse(linesKey) as PriceLineSpec[]).map((spec) =>
      p.series.createPriceLine({
        price: Number(spec.price),
        color: palette[spec.color],
        lineWidth: 1,
        lineStyle: spec.style,
        axisLabelVisible: true,
        title: spec.title
      })
    )
  }, [linesKey, palette])

  // Placed again when a bar is added, not with every update of the forming one.
  useEffect(() => {
    const times = barTimes(closed, formingOpen)
    parts.current?.markers.setMarkers(tradeMarkers(trades, selected, times, palette))
  }, [trades, selected, closed, formingOpen, palette])

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
      </div>
      <div className="chart-frame">
        <div className="chart" ref={container} />
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
        <span>▲▼ 최근 7일 체결</span>
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
