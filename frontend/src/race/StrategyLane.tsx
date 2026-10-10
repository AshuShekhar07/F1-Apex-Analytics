import { useLayoutEffect, useRef } from 'react'

import { compoundColor, pct } from '../format'
import { gsap, reducedMotion } from '../motion/gsap'
import type { PitWindow } from '../types'

interface Plan {
  sequence: string[]
  pit_windows: PitWindow[]
}

/** Stints from the typical stop laps: [start, end] per compound. */
export function planStints(plan: Plan, totalLaps: number): { compound: string; from: number; to: number }[] {
  const stops = [...plan.pit_windows].sort((a, b) => a.stop - b.stop).map((w) => w.typical_lap)
  return plan.sequence.map((compound, i) => ({
    compound,
    from: i === 0 ? 1 : stops[i - 1] + 1,
    to: i < stops.length ? stops[i] : totalLaps,
  }))
}

function Lane({ plan, totalLaps, label, share, dim }: { plan: Plan; totalLaps: number; label: string; share: number | null; dim?: boolean }) {
  const x = (lap: number) => `${((lap - 1) / Math.max(1, totalLaps - 1)) * 100}%`
  const w = (a: number, b: number) => `${((b - a + 1) / totalLaps) * 100}%`
  return (
    <div style={{ opacity: dim ? 0.6 : 1 }}>
      <div className="lane-label">
        <b>{label}</b>
        <span className="num muted">{plan.sequence.map((c) => c[0]).join('–')}{share != null ? ` · ${pct(share, 0)} of front-grid cars here` : ''}</span>
      </div>
      <div className="lane" role="img"
           aria-label={`${label}: ${planStints(plan, totalLaps).map((s) => `${s.compound} laps ${s.from}-${s.to}`).join(', ')}`}>
        {planStints(plan, totalLaps).map((s, i) => (
          <div key={i} className="stint" style={{ left: x(s.from), width: w(s.from, s.to), background: compoundColor(s.compound) }}>
            {s.compound}
          </div>
        ))}
        {plan.pit_windows.map((win) => (
          <div key={win.stop} className="window" style={{ left: x(win.first_lap), width: w(win.first_lap, win.last_lap) }}
               title={`Stop ${win.stop}: laps ${win.first_lap}–${win.last_lap}, typically lap ${win.typical_lap}`} />
        ))}
      </div>
      <div className="lap-axis" style={{ justifyContent: 'flex-start', gap: 0, position: 'relative', height: 12 }}>
        {plan.pit_windows.map((win) => (
          <span key={win.stop} style={{ position: 'absolute', left: x(win.first_lap) }}>L{win.first_lap}–{win.last_lap}</span>
        ))}
      </div>
    </div>
  )
}

/**
 * The predicted winning strategy for the front of the grid on a lap axis: stints in their
 * tyre colours, pit windows hatched, the runner-up plan below, and the stop-count odds.
 */
export function StrategyLane({ plan, alternative, totalLaps, stopChances }: {
  plan: Plan & { front_grid_share: number | null }
  alternative: (Plan & { front_grid_share: number }) | null
  totalLaps: number
  stopChances: Record<string, number>
}) {
  const root = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    if (!root.current || reducedMotion()) return
    gsap.from(root.current.querySelectorAll('.lane .stint'), { scaleX: 0, transformOrigin: 'left', duration: 0.6, stagger: 0.08, ease: 'power2.out' })
  }, [plan])
  const ticks = [1, ...Array.from({ length: Math.floor(totalLaps / 10) }, (_, i) => (i + 1) * 10).filter((l) => l < totalLaps - 3), totalLaps]
  return (
    <div className="strategy-lane" ref={root}>
      <Lane plan={plan} totalLaps={totalLaps} label="Plan A" share={plan.front_grid_share} />
      {alternative && <Lane plan={alternative} totalLaps={totalLaps} label="Plan B" share={alternative.front_grid_share} dim />}
      <div className="lap-axis" style={{ marginTop: 14 }}>
        {ticks.map((t) => <span key={t}>L{t}</span>)}
      </div>
      {Object.keys(stopChances).length > 0 && (
        <div className="stop-odds">
          {Object.entries(stopChances).sort(([a], [b]) => Number(a) - Number(b)).map(([n, v]) => (
            <div key={n}>
              <span>{n}-stop</span>
              <div className="prob-cell" style={{ gridTemplateColumns: '40px 1fr' }}>
                <span className="num">{pct(v, 0)}</span>
                <span className="meter"><i style={{ width: `${v * 100}%`, background: 'var(--text-2)' }} /></span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
