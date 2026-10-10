import { compoundColor } from '../format'
import type { PreStart } from '../types'
import { StrategyLane } from './StrategyLane'

/** Race-day plans once the front grid's starting tyres are declared: one plan per tyre. */
export function PreStartPlans({ data, totalLaps }: { data: PreStart; totalLaps: number }) {
  return (
    <div>
      <div className="tyre-seq" style={{ flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
        {data.cars.map((c) => (
          <span key={c.grid} className="small" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span className="num dim">P{c.grid}</span>{c.driver}
            <span className="tyre" style={{ borderColor: compoundColor(c.start), width: 15, height: 15, fontSize: '0.5rem' }}>{c.start[0]}</span>
          </span>
        ))}
      </div>
      {Object.entries(data.plans).map(([tyre, plan]) => (
        <div key={tyre} style={{ marginBottom: 14 }}>
          <div className="cap" style={{ marginBottom: 2 }}>Starting on {tyre.toLowerCase()}</div>
          <StrategyLane plan={plan} alternative={null} totalLaps={totalLaps} stopChances={{}} />
        </div>
      ))}
    </div>
  )
}
