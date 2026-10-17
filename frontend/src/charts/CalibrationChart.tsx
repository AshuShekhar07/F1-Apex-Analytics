import { useState } from 'react'

import { pct } from '../format'
import type { Calibration } from '../types'
import { useWidth } from './useWidth'

const STAGES = [
  { key: 'post_qualifying', label: 'After qualifying' },
  { key: 'pre_qualifying', label: 'Before qualifying' },
]

/**
 * Are the chances honest? Races are grouped by the chance we gave the favourite; each dot is
 * one group: how likely we said (across) against how often the favourite then won (up).
 * On the dashed line the chances mean exactly what they say. Dot size = number of races.
 */
export function CalibrationChart({ data }: { data: Calibration }) {
  const [stage, setStage] = useState('post_qualifying')
  const [box, W] = useWidth<HTMLDivElement>(420)
  const s = data.stages[stage]
  const size = Math.min(W, 420)
  const M = 34
  const x = (v: number) => M + v * (size - M - 10)
  const y = (v: number) => size - M - v * (size - M - 10)
  if (!s) return null
  return (
    <div>
      <div className="tabs" style={{ marginBottom: 14 }}>
        {STAGES.filter((st) => data.stages[st.key]).map((st) => (
          <button key={st.key} className={stage === st.key ? 'on' : ''} onClick={() => setStage(st.key)}>{st.label}</button>
        ))}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 420px) minmax(0, 1fr)', gap: 22, alignItems: 'start' }} className="calib">
        <div ref={box} className="chart">
          <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} role="img"
               aria-label="Predicted chance of the favourite against how often the favourite won">
            {[0, 0.25, 0.5, 0.75, 1].map((t) => (
              <g key={t}>
                <line className="gridline" x1={x(0)} x2={x(1)} y1={y(t)} y2={y(t)} />
                <text x={M - 6} y={y(t) + 3} textAnchor="end" fill="var(--text-3)" fontSize="10" fontFamily="var(--mono)">{t * 100}%</text>
                <text x={x(t)} y={size - M + 16} textAnchor="middle" fill="var(--text-3)" fontSize="10" fontFamily="var(--mono)">{t * 100}%</text>
              </g>
            ))}
            <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)} stroke="var(--text-3)" strokeDasharray="4 4" />
            <text x={x(0.98)} y={y(0.98) + 14} textAnchor="end" fill="var(--text-3)" fontSize="10" fontFamily="var(--mono)">honest</text>
            {s.bins.map((b) => (
              <g key={b.lo}>
                <line x1={x(b.mean_p)} x2={x(b.mean_p)} y1={y(b.mean_p)} y2={y(b.favourite_won)} stroke="var(--line-2)" />
                <circle cx={x(b.mean_p)} cy={y(b.favourite_won)} r={4 + Math.sqrt(b.races) * 1.1} fill="var(--red)" fillOpacity={0.85} stroke="var(--surface)" strokeWidth={2}>
                  <title>{`Said ${pct(b.mean_p, 0)} on average · favourite won ${pct(b.favourite_won, 0)} of ${b.races} races`}</title>
                </circle>
              </g>
            ))}
          </svg>
        </div>
        <div style={{ minWidth: 0 }}>
          <div className="table-wrap">
          <table className="timing">
            <thead><tr><th>We said</th><th className="r">Races</th><th className="r">Favourite won</th></tr></thead>
            <tbody>
              {s.bins.map((b) => (
                <tr key={b.lo}>
                  <td className="num">{pct(b.lo, 0)}–{b.hi > 1 ? '100%' : pct(b.hi, 0)} <span className="dim">(avg {pct(b.mean_p, 0)})</span></td>
                  <td className="r num">{b.races}</td>
                  <td className="r num" style={{ fontWeight: 700 }}>{pct(b.favourite_won, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
          <p className="small muted" style={{ marginTop: 12 }}>
            {s.races} races, each predicted only from the races before it. Our favourite won {pct(s.favourite_won, 0)} of
            them; the average chance we gave was {pct(s.mean_favourite_p, 0)}. Dots close to the dashed line mean the
            percentages can be read at face value.
          </p>
        </div>
      </div>
    </div>
  )
}
