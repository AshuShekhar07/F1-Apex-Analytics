import { useLayoutEffect, useMemo, useRef, useState } from 'react'

import { gsap, reducedMotion } from '../motion/gsap'
import { useWidth } from './useWidth'
import { glowColour, normaliseHex } from '../theme/color'

export interface Series {
  id: number | string
  label: string
  short: string
  colour: string | null
  dashed?: boolean
  points: { x: number; y: number }[]
}

const M = { t: 12, r: 58, b: 26, l: 44 }

function niceMax(v: number): number {
  if (v <= 0) return 1
  const p = 10 ** Math.floor(Math.log10(v))
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((m) => m >= v) ?? v
}

/** Spread end labels so they never overlap (min gap in px), keeping their order. */
function spread(ys: number[], gap: number, lo: number, hi: number): number[] {
  const order = ys.map((y, i) => [y, i] as const).sort((a, b) => a[0] - b[0])
  const out = new Array<number>(ys.length)
  let last = -Infinity
  for (const [y, i] of order) {
    const v = Math.max(y, last + gap)
    out[i] = v
    last = v
  }
  const over = last - hi
  if (over > 0) for (let i = 0; i < out.length; i++) out[i] = Math.max(lo, out[i] - over)
  return out
}

/**
 * Lines over the rounds of a season, in team colours (a team's second driver dashed), with
 * labels at the line ends, a crosshair that reads every series at the hovered round, and a
 * data table for anyone who prefers numbers.
 */
export function LineChart({ series, xLabel, yFormat, yMax, height = 300, caption }: {
  series: Series[]
  xLabel: (x: number) => string
  yFormat: (y: number) => string
  yMax?: number
  height?: number
  caption: string
}) {
  const svg = useRef<SVGSVGElement>(null)
  const [box, W] = useWidth<HTMLElement>(560)
  const [hover, setHover] = useState<number | null>(null)
  const xs = useMemo(() => [...new Set(series.flatMap((s) => s.points.map((p) => p.x)))].sort((a, b) => a - b), [series])
  const top = yMax ?? niceMax(Math.max(...series.flatMap((s) => s.points.map((p) => p.y)), 0))
  const H = height
  const x = (v: number) => M.l + (xs.length < 2 ? 0 : ((v - xs[0]) / (xs[xs.length - 1] - xs[0])) * (W - M.l - M.r))
  const y = (v: number) => M.t + (1 - v / top) * (H - M.t - M.b)
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * top)
  const ends = series.map((s) => (s.points.length ? y(s.points[s.points.length - 1].y) : 0))
  const labelYs = spread(ends, 12, M.t, H - M.b)

  useLayoutEffect(() => {
    if (!svg.current || reducedMotion()) return
    svg.current.querySelectorAll<SVGPathElement>('path.series').forEach((p, i) => {
      const len = p.getTotalLength()
      const dashed = p.dataset.dashed === '1'
      gsap.fromTo(p, { strokeDasharray: `${len} ${len}`, strokeDashoffset: len }, {
        strokeDashoffset: 0, duration: 1.1, delay: i * 0.05, ease: 'power2.out',
        onComplete: () => p.setAttribute('stroke-dasharray', dashed ? '5 4' : 'none'),
      })
    })
  }, [series, W])

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    const px = ((e.clientX - r.left) / r.width) * W
    let best = xs[0], bestD = Infinity
    for (const v of xs) {
      const d = Math.abs(x(v) - px)
      if (d < bestD) { best = v; bestD = d }
    }
    setHover(best ?? null)
  }

  const at = (s: Series, v: number) => s.points.find((p) => p.x === v)?.y
  const tipRows = hover == null ? [] : series
    .map((s) => ({ s, v: at(s, hover) }))
    .filter((r): r is { s: Series; v: number } => r.v !== undefined)
    .sort((a, b) => b.v - a.v)
  const hx = hover == null ? 0 : x(hover)

  return (
    <figure className="chart" style={{ margin: 0, position: 'relative' }} ref={box}>
      <svg ref={svg} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={caption}
           onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line className="gridline" x1={M.l} x2={W - M.r} y1={y(t)} y2={y(t)} />
            <text x={M.l - 8} y={y(t) + 3} textAnchor="end" fill="var(--text-3)" fontSize="10" fontFamily="var(--mono)">{yFormat(t)}</text>
          </g>
        ))}
        {xs.map((v, i) => (i % Math.ceil(xs.length / 12) === 0 || i === xs.length - 1) && (
          <text key={v} x={x(v)} y={H - 8} textAnchor="middle" fill="var(--text-3)" fontSize="10" fontFamily="var(--mono)">{xLabel(v)}</text>
        ))}
        {hover != null && <line x1={hx} x2={hx} y1={M.t} y2={H - M.b} stroke="var(--text-3)" strokeDasharray="3 3" />}
        {series.map((s, i) => {
          const c = glowColour(normaliseHex(s.colour, '#888888'))
          const d = s.points.map((p, k) => `${k ? 'L' : 'M'}${x(p.x).toFixed(1)},${y(p.y).toFixed(1)}`).join(' ')
          const last = s.points[s.points.length - 1]
          return (
            <g key={s.id}>
              <path className="series" data-dashed={s.dashed ? '1' : '0'} d={d} fill="none" stroke={c} strokeWidth={2}
                    strokeLinejoin="round" strokeLinecap="round" strokeDasharray={s.dashed ? '5 4' : undefined} />
              {last && (
                <>
                  <circle cx={x(last.x)} cy={y(last.y)} r={3} fill={c} />
                  <text x={W - M.r + 8} y={labelYs[i] + 3.5} fill="var(--text)" fontSize="10.5" fontWeight="700" fontFamily="var(--mono)">{s.short}</text>
                </>
              )}
              {hover != null && at(s, hover) !== undefined && (
                <circle cx={hx} cy={y(at(s, hover)!)} r={4} fill={c} stroke="var(--surface)" strokeWidth={2} />
              )}
            </g>
          )
        })}
      </svg>
      {hover != null && tipRows.length > 0 && (
        <div className="chart-tip" style={{ left: `${Math.min(80, (hx / W) * 100)}%`, top: 8, transform: hx / W > 0.5 ? 'translateX(-105%)' : 'translateX(12px)' }}>
          <div className="t">{xLabel(hover)}</div>
          {tipRows.slice(0, 8).map(({ s, v }) => (
            <div className="row" key={s.id}>
              <span className="team-bar" style={{ background: glowColour(normaliseHex(s.colour, '#888888')), height: 10 }} />
              <span>{s.label}</span>
              <span className="num">{yFormat(v)}</span>
            </div>
          ))}
        </div>
      )}
      <div className="chart-legend">
        {series.map((s) => (
          <span key={s.id} style={{ color: glowColour(normaliseHex(s.colour, '#888888')) }}>
            <i className={s.dashed ? 'dash' : ''} style={{ background: 'currentColor' }} />
            <span style={{ color: 'var(--text-2)' }}>{s.label}</span>
          </span>
        ))}
      </div>
      <details className="notes" style={{ marginTop: 10 }}>
        <summary className="small">Data table</summary>
        <div className="table-wrap">
          <table className="timing">
            <thead><tr><th>{caption}</th>{xs.map((v) => <th key={v} className="r">{xLabel(v)}</th>)}</tr></thead>
            <tbody>
              {series.map((s) => (
                <tr key={s.id}><td>{s.label}</td>{xs.map((v) => <td key={v} className="r num">{at(s, v) !== undefined ? yFormat(at(s, v)!) : ''}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  )
}
