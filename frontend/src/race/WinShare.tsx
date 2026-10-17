import { useLayoutEffect, useRef } from 'react'

import { pct } from '../format'
import { gsap, reducedMotion } from '../motion/gsap'
import { glowColour, inkOn, normaliseHex } from '../theme/color'

interface Slice {
  id: number
  code: string
  name: string
  colour: string | null
  p: number
}

/**
 * How the win chance is split across the field: one bar that adds up to 100%. A race the
 * favourite dominates shows one long segment; an open race shows many. Drivers under 3% are
 * grouped as "others".
 */
export function WinShare({ slices, stageKey }: { slices: Slice[]; stageKey: string }) {
  const bar = useRef<HTMLDivElement>(null)
  const sorted = [...slices].sort((a, b) => b.p - a.p)
  const shown = sorted.filter((s) => s.p >= 0.03).slice(0, 8)
  const rest = Math.max(0, 1 - shown.reduce((a, s) => a + s.p, 0))

  useLayoutEffect(() => {
    if (!bar.current || reducedMotion()) return
    gsap.from(bar.current.children, { flexGrow: 0, duration: 0.9, ease: 'power3.out', stagger: 0.04 })
  }, [stageKey])

  return (
    <div>
      <div className="share-bar" ref={bar} role="img"
           aria-label={shown.map((s) => `${s.name} ${pct(s.p, 0)}`).join(', ') + `, others ${pct(rest, 0)}`}>
        {shown.map((s) => {
          const c = glowColour(normaliseHex(s.colour, '#888888'))
          return (
            <div key={s.id} style={{ flexGrow: s.p, flexBasis: 0, background: c }} title={`${s.name}: ${pct(s.p, 1)}`}>
              {s.p >= 0.07 && <span style={{ color: inkOn(c) }}>{s.code}</span>}
            </div>
          )
        })}
        {rest > 0.005 && <div className="rest" style={{ flexGrow: rest, flexBasis: 0 }} title={`Others: ${pct(rest, 1)}`}>{rest >= 0.07 && <span>OTH</span>}</div>}
      </div>
      <div className="share-legend">
        {shown.slice(0, 6).map((s) => (
          <div key={s.id}>
            <span className="team-bar" style={{ background: glowColour(normaliseHex(s.colour, '#888888')), height: 12 }} />
            <span className="code">{s.code}</span>
            <span className="num">{pct(s.p, 0)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
