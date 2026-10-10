import { useLayoutEffect, useRef, useState } from 'react'

import { gsap, reducedMotion } from '../motion/gsap'
import { glowColour, normaliseHex } from '../theme/color'

interface Line {
  id: number
  code: string
  name: string
  colour: string | null
  from: number
  to: number
}

const ROW = 19
const PAD_T = 24
const W = 360

/**
 * Starting grid (left) to the model's predicted finish (right), one line per driver in team
 * colours. Steep lines are the drivers the model expects to move. Hover a line to isolate it.
 */
export function SlopeChart({ lines, leftLabel = 'Grid', rightLabel = 'Predicted' }: { lines: Line[]; leftLabel?: string; rightLabel?: string }) {
  const svg = useRef<SVGSVGElement>(null)
  const [focus, setFocus] = useState<number | null>(null)
  const n = Math.max(...lines.map((l) => Math.max(l.from, l.to)), 1)
  const h = PAD_T + n * ROW + 6
  const y = (p: number) => PAD_T + (p - 0.5) * ROW
  const x0 = 64, x1 = W - 64

  useLayoutEffect(() => {
    if (!svg.current || reducedMotion()) return
    const paths = svg.current.querySelectorAll<SVGLineElement>('line.slope')
    paths.forEach((p) => {
      const len = Math.hypot(Number(p.getAttribute('x2')) - Number(p.getAttribute('x1')), Number(p.getAttribute('y2')) - Number(p.getAttribute('y1')))
      gsap.fromTo(p, { strokeDasharray: len, strokeDashoffset: len }, { strokeDashoffset: 0, duration: 0.9, ease: 'power2.out', delay: 0.15 })
    })
  }, [lines])

  const mover = (l: Line) => l.from - l.to
  return (
    <div className="chart">
      <svg ref={svg} viewBox={`0 0 ${W} ${h}`} role="img" aria-label="Starting grid against predicted finishing position"
           onMouseLeave={() => setFocus(null)}>
        <text x={x0} y={12} textAnchor="middle" className="label dim">{leftLabel.toUpperCase()}</text>
        <text x={x1} y={12} textAnchor="middle" className="label dim">{rightLabel.toUpperCase()}</text>
        {Array.from({ length: n }, (_, i) => (
          <g key={i}>
            <text x={x0 - 34} y={y(i + 1) + 3.5} textAnchor="end" className="tick"><tspan fill="var(--text-3)" fontSize="9" fontFamily="var(--mono)">{i + 1}</tspan></text>
            <text x={x1 + 34} y={y(i + 1) + 3.5} textAnchor="start" className="tick"><tspan fill="var(--text-3)" fontSize="9" fontFamily="var(--mono)">{i + 1}</tspan></text>
          </g>
        ))}
        {lines.map((l) => {
          const c = glowColour(normaliseHex(l.colour, '#888888'))
          const dim = focus != null && focus !== l.id
          const big = Math.abs(mover(l)) >= 3
          return (
            <g key={l.id} opacity={dim ? 0.12 : 1} onMouseEnter={() => setFocus(l.id)} style={{ cursor: 'default' }}>
              <title>{`${l.name}: grid ${l.from} → predicted P${l.to}`}</title>
              <line className="slope" x1={x0} y1={y(l.from)} x2={x1} y2={y(l.to)} stroke={c} strokeWidth={big || focus === l.id ? 2.4 : 1.4} strokeLinecap="round" />
              <circle cx={x0} cy={y(l.from)} r={2.6} fill={c} />
              <circle cx={x1} cy={y(l.to)} r={2.6} fill={c} />
              <text x={x0 - 8} y={y(l.from) + 3.5} textAnchor="end" className="label">{l.code}</text>
              <text x={x1 + 8} y={y(l.to) + 3.5} textAnchor="start" className="label">{l.code}</text>
              <line x1={x0} y1={y(l.from)} x2={x1} y2={y(l.to)} stroke="transparent" strokeWidth={10} />
            </g>
          )
        })}
      </svg>
    </div>
  )
}
