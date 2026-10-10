import { useLayoutEffect, useMemo, useRef, useState } from 'react'

import { gsap, reducedMotion } from '../motion/gsap'
import { useWidth } from './useWidth'
import { driverCode } from '../names'
import { glowColour, normaliseHex } from '../theme/color'
import type { PositionBattle } from '../types'

const ROW = 17
const M = { t: 14, r: 44, b: 24, l: 44 }

/**
 * The race lap by lap: every driver's position on every lap (the grid as lap 0), in team
 * colours with the second car of a team dashed. Hover a line or a name to follow one driver.
 */
export function LapChart({ battle }: { battle: PositionBattle }) {
  const svg = useRef<SVGSVGElement>(null)
  const [box, W] = useWidth<HTMLDivElement>(900)
  const [focus, setFocus] = useState<number | null>(null)
  const drivers = useMemo(() => {
    const seen = new Set<string>()
    return [...battle.results]
      .filter((d) => d.positions.some((p) => p.position != null))
      .sort((a, b) => (a.finishing_position ?? 99) - (b.finishing_position ?? 99))
      .map((d) => {
        const dashed = seen.has(d.team_name)
        seen.add(d.team_name)
        const pts = d.positions.filter((p) => p.position != null) as { lap: number; position: number }[]
        if (d.starting_grid_position) pts.unshift({ lap: 0, position: d.starting_grid_position })
        return { ...d, dashed, pts }
      })
  }, [battle])
  const laps = Math.max(1, ...drivers.flatMap((d) => d.pts.map((p) => p.lap)))
  const n = Math.max(drivers.length, ...drivers.flatMap((d) => d.pts.map((p) => p.position)))
  const H = M.t + n * ROW + M.b
  const x = (lap: number) => M.l + (lap / laps) * (W - M.l - M.r)
  const y = (pos: number) => M.t + (pos - 0.5) * ROW

  useLayoutEffect(() => {
    if (!svg.current || reducedMotion()) return
    const clip = svg.current.querySelector('#lapclip rect')
    if (clip) gsap.fromTo(clip, { attr: { width: 0 } }, { attr: { width: 4000 }, duration: 1.8, ease: 'power2.inOut' })
  }, [battle])

  return (
    <div className="chart" ref={box} onMouseLeave={() => setFocus(null)}>
      <svg ref={svg} viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Positions lap by lap">
        <defs><clipPath id="lapclip"><rect x={0} y={0} width={4000} height={H} /></clipPath></defs>
        {Array.from({ length: Math.floor(laps / 10) + 1 }, (_, i) => i * 10).map((l) => (
          <g key={l}>
            <line className="gridline" x1={x(l)} x2={x(l)} y1={M.t} y2={H - M.b} />
            <text x={x(l)} y={H - 8} textAnchor="middle" fill="var(--text-3)" fontSize="10" fontFamily="var(--mono)">{l === 0 ? 'Grid' : `L${l}`}</text>
          </g>
        ))}
        <g clipPath="url(#lapclip)">
          {drivers.map((d) => {
            const c = glowColour(normaliseHex(d.color_hex, '#888888'))
            const dim = focus != null && focus !== d.driver_id
            const path = d.pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.lap).toFixed(1)},${y(p.position).toFixed(1)}`).join(' ')
            return (
              <g key={d.driver_id} opacity={dim ? 0.1 : 1} onMouseEnter={() => setFocus(d.driver_id)}>
                <path d={path} fill="none" stroke="transparent" strokeWidth={8} />
                <path d={path} fill="none" stroke={c} strokeWidth={focus === d.driver_id ? 2.6 : 1.5} strokeLinejoin="round"
                      strokeDasharray={d.dashed ? '4 3' : undefined} />
              </g>
            )
          })}
        </g>
        {drivers.map((d) => {
          const first = d.pts[0]
          const last = d.pts[d.pts.length - 1]
          const dim = focus != null && focus !== d.driver_id
          const code = driverCode(d.driver_name)
          // stopped early: fewer laps than the race and not classified (lapped cars are classified)
          const out = last != null && last.lap < laps && !(d.status === 'Finished' || (d.status ?? '').startsWith('+') || (d.status ?? '').includes('Lap'))
          return (
            <g key={d.driver_id} opacity={dim ? 0.25 : 1} onMouseEnter={() => setFocus(d.driver_id)} style={{ cursor: 'default' }}>
              {first && <text x={M.l - 6} y={y(first.position) + 3.5} textAnchor="end" fill="var(--text-2)" fontSize="10" fontWeight="700" fontFamily="var(--mono)">{code}</text>}
              {last && (
                <text x={x(last.lap) + 6} y={y(last.position) + 3.5} fill={out ? 'var(--down)' : 'var(--text)'} fontSize="10" fontWeight="700" fontFamily="var(--mono)">
                  {code}{out ? ' ✕' : ''}
                </text>
              )}
            </g>
          )
        })}
      </svg>
      <p className="dim small" style={{ margin: '6px 0 0' }}>Lap 0 is the starting grid. A red name with ✕ marks where a car retired.</p>
    </div>
  )
}
