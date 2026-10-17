import { compoundColor, compoundLetter } from '../format'
import { driverCode } from '../names'
import { glowColour, normaliseHex } from '../theme/color'
import type { LongRuns as LongRunsData } from '../types'

/** Friday race-trim pace: each driver's long-run gap to the quickest, as bars. */
export function LongRuns({ data }: { data: LongRunsData }) {
  if (data.wet) return <p className="muted small" style={{ margin: 0 }}>Practice was wet: no dry long-run pace this weekend.</p>
  if (!data.drivers.length) return <p className="muted small" style={{ margin: 0 }}>No long runs in practice yet.</p>
  const worst = Math.max(...data.drivers.map((d) => d.gap_s), 0.1)
  return (
    <div>
      {data.drivers.map((d, i) => {
        const c = glowColour(normaliseHex(d.color_hex, '#888'))
        const laps = Object.values(d.compounds).reduce((a, x) => a + x.laps, 0)
        return (
          <div key={d.driver_id} style={{ display: 'grid', gridTemplateColumns: '22px 40px 1fr 66px auto', gap: 8, alignItems: 'center', height: 24 }}
               title={`${d.driver_name}: ${laps} long-run laps`}>
            <span className="num dim small">{i + 1}</span>
            <span className="code" style={{ borderLeft: `3px solid ${c}`, paddingLeft: 6, fontSize: '0.8rem' }}>{driverCode(d.driver_name ?? '???')}</span>
            <span className="meter" style={{ height: 6 }}><i style={{ width: `${Math.max(2, (1 - d.gap_s / (worst * 1.05)) * 100)}%`, background: c }} /></span>
            <span className="num small r" style={{ textAlign: 'right' }}>{d.gap_s === 0 ? 'Best' : `+${d.gap_s.toFixed(3)}s`}</span>
            <span className="tyre-seq">
              {Object.keys(d.compounds).map((k) => <span key={k} className="tyre" style={{ borderColor: compoundColor(k), width: 15, height: 15, fontSize: '0.5rem' }}>{compoundLetter(k)}</span>)}
            </span>
          </div>
        )
      })}
      <p className="dim small" style={{ margin: '10px 0 0' }}>
        {data.session} long runs, gap per lap{data.reference_compound ? ` (${data.reference_compound.toLowerCase()} reference ${data.reference_lap?.toFixed(3)}s)` : ''}.
        Fuel loads are team choices: a guide, not a verdict.
      </p>
    </div>
  )
}
