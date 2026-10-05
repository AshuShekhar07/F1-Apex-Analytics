import { useEffect, useRef, useState } from 'react'

import { pct, pctLabel, roundLabel, shortName, type ChartSeries } from '../format'

// The SVG is drawn at the container's real width (1 unit = 1 CSS pixel), so text keeps
// its size on a phone instead of shrinking with the whole drawing.
const H = 260
const M = { top: 12, right: 112, bottom: 30, left: 40 }
const PH = H - M.top - M.bottom
const LABEL_GAP = 13
const MIN_WIDTH = 300

/** Spread end labels so none overlap, keeping their order and staying inside the plot. */
export function spread(ys: number[], top = M.top, bottom = M.top + PH, gap = LABEL_GAP): number[] {
  const order = ys.map((y, i) => ({ y, i })).sort((a, b) => a.y - b.y)
  for (let k = 1; k < order.length; k++) order[k].y = Math.max(order[k].y, order[k - 1].y + gap)
  if (order.length) order[order.length - 1].y = Math.min(order[order.length - 1].y, bottom)
  for (let k = order.length - 2; k >= 0; k--) order[k].y = Math.min(order[k].y, order[k + 1].y - gap)
  if (order.length) order[0].y = Math.max(order[0].y, top)
  for (let k = 1; k < order.length; k++) order[k].y = Math.max(order[k].y, order[k - 1].y + gap)
  const out = new Array<number>(ys.length)
  for (const o of order) out[o.i] = o.y
  return out
}

/**
 * Title chance of the leading entries after each round. Colour follows the entity (fixed
 * slot by its order in `series`); a legend, end labels, a crosshair tooltip and a table
 * view carry the same numbers, so colour is never the only key.
 */
export function TitleChart({ series, title, kind }: { series: ChartSeries[]; title: string; kind: 'drivers' | 'constructors' }) {
  const rounds = [...new Set(series.flatMap((s) => s.points.map((p) => p.x)))].sort((a, b) => a - b)
  const [active, setActive] = useState<number | null>(null)
  const [W, setW] = useState(640)
  const svgRef = useRef<SVGSVGElement>(null)
  const boxRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const box = boxRef.current
    if (!box) return
    const measure = () => setW(Math.max(MIN_WIDTH, Math.round(box.clientWidth)))
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(box)
    return () => observer.disconnect()
  }, [])
  if (!rounds.length) return null
  const narrow = W < 480
  const right = narrow ? 100 : M.right
  const PW = W - M.left - right

  const xMin = rounds[0]
  const xMax = Math.max(rounds[rounds.length - 1], xMin + 1)
  const x = (r: number) => M.left + ((r - xMin) / (xMax - xMin)) * PW
  const y = (p: number) => M.top + (1 - p) * PH
  const valueAt = (s: ChartSeries, r: number) => s.points.find((p) => p.x === r)?.y
  const last = rounds[rounds.length - 1]
  const labelY = spread(series.map((s) => y(valueAt(s, last) ?? 0)))
  const step = Math.max(1, Math.ceil(rounds.length / (narrow ? 4 : 8)))
  // every step-th round, and the last round in place of a tick that would crowd it
  const ticks = rounds.filter((_, i) => i % step === 0)
  if (ticks[ticks.length - 1] !== last) {
    if (rounds.indexOf(last) - rounds.indexOf(ticks[ticks.length - 1]) < step / 2 + 0.5) ticks.pop()
    ticks.push(last)
  }

  function pick(clientX: number) {
    const box = svgRef.current?.getBoundingClientRect()
    if (!box) return
    const vx = ((clientX - box.left) / box.width) * W
    const nearest = rounds.reduce((best, r) => (Math.abs(x(r) - vx) < Math.abs(x(best) - vx) ? r : best), rounds[0])
    setActive(nearest)
  }

  function onKey(e: React.KeyboardEvent) {
    const i = active === null ? rounds.length - 1 : rounds.indexOf(active)
    if (e.key === 'ArrowLeft') setActive(rounds[Math.max(0, i - 1)])
    else if (e.key === 'ArrowRight') setActive(rounds[Math.min(rounds.length - 1, i + 1)])
    else if (e.key === 'Escape') setActive(null)
    else return
    e.preventDefault()
  }

  const tipLeft = active === null ? 0 : (x(active) / W) * 100
  return (
    <figure className="title-chart">
      <figcaption>
        <span className="chart-title">{title}</span>
        <span className="chart-legend">
          {series.map((s, i) => (
            <span key={s.id}><i className={`swatch s${i + 1}`} />{s.name}</span>
          ))}
        </span>
      </figcaption>
      <div className="chart-box" ref={boxRef}>
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          width={W}
          height={H}
          role="img"
          aria-label={`${title}. Use the left and right arrow keys to read each round.`}
          tabIndex={0}
          onPointerMove={(e) => pick(e.clientX)}
          onPointerLeave={() => setActive(null)}
          onFocus={() => setActive(last)}
          onBlur={() => setActive(null)}
          onKeyDown={onKey}
        >
          {[0, 0.25, 0.5, 0.75, 1].map((t) => (
            <g key={t}>
              <line className="grid" x1={M.left} x2={M.left + PW} y1={y(t)} y2={y(t)} />
              <text className="tick" x={M.left - 6} y={y(t) + 4} textAnchor="end">{t * 100}%</text>
            </g>
          ))}
          {ticks.map((r) => (
            <text key={r} className="tick" x={x(r)} y={H - 10} textAnchor="middle">{r === 0 ? 'Pre' : `R${r}`}</text>
          ))}
          {active !== null && <line className="crosshair" x1={x(active)} x2={x(active)} y1={M.top} y2={M.top + PH} />}
          {series.map((s, i) => (
            <g key={s.id} className={`series s${i + 1}`}>
              <polyline points={s.points.map((p) => `${x(p.x)},${y(p.y)}`).join(' ')} />
              {active !== null && valueAt(s, active) !== undefined && (
                <circle cx={x(active)} cy={y(valueAt(s, active)!)} r={4.5} />
              )}
              <text className="end-label" x={M.left + PW + 8} y={labelY[i] + 4}>
                {shortName(s.name, kind)} {pctLabel(valueAt(s, last) ?? 0)}
              </text>
            </g>
          ))}
        </svg>
        {active !== null && (
          <div className="chart-tip" style={{ left: `${Math.min(Math.max(tipLeft, 18), 70)}%` }} role="status">
            <div className="muted small">{roundLabel(active)}</div>
            {[...series]
              .map((s, i) => ({ s, i, v: valueAt(s, active) }))
              .sort((a, b) => (b.v ?? -1) - (a.v ?? -1))
              .map(({ s, i, v }) => (
                <div key={s.id} className="tip-row">
                  <i className={`swatch s${i + 1}`} />
                  <strong>{v === undefined ? '–' : pct(v, 1)}</strong> <span className="muted">{s.name}</span>
                </div>
              ))}
          </div>
        )}
      </div>
      <details className="chart-table">
        <summary>Show as a table</summary>
        <div className="table-wrap">
          <table className="compact">
            <thead>
              <tr>
                <th>Round</th>
                {series.map((s) => <th key={s.id}>{s.name}</th>)}
              </tr>
            </thead>
            <tbody>
              {rounds.map((r) => (
                <tr key={r}>
                  <td>{roundLabel(r)}</td>
                  {series.map((s) => {
                    const v = valueAt(s, r)
                    return <td key={s.id}>{v === undefined ? '–' : pct(v, 1)}</td>
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  )
}
