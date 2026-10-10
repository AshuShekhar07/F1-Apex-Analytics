import { driverCode } from '../names'
import { glowColour, normaliseHex } from '../theme/color'
import type { QualifyingRow } from '../types'

/** 83.456 -> "1:23.456" */
export function lapTime(seconds: number | null | undefined): string {
  if (seconds == null) return ''
  const s = Number(seconds)
  const m = Math.floor(s / 60)
  return m ? `${m}:${(s - m * 60).toFixed(3).padStart(6, '0')}` : s.toFixed(3)
}

/** Qualifying as on a timing screen: Q1 / Q2 / Q3 times, the gap to pole, and where each
    driver was knocked out (fastest time per segment in purple). */
export function QualifyingTable({ rows }: { rows: QualifyingRow[] }) {
  const best = (k: 'q1_time' | 'q2_time' | 'q3_time') => Math.min(...rows.map((r) => (r[k] == null ? Infinity : Number(r[k]))))
  const fastest = { q1_time: best('q1_time'), q2_time: best('q2_time'), q3_time: best('q3_time') }
  return (
    <div className="table-wrap">
      <table className="timing">
        <thead>
          <tr><th>Pos</th><th>Driver</th><th className="r">Q1</th><th className="r">Q2</th><th className="r">Q3</th><th className="r">Gap</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.driver_id} style={r.final_position === 10 || r.final_position === 15 ? { borderBottom: '2px solid var(--line-2)' } : undefined}>
              <td className="pos-cell"><span className={`pos-box ${r.final_position && r.final_position <= 3 ? `p${r.final_position}` : ''}`}>{r.final_position ?? '–'}</span></td>
              <td>
                <div className="driver-cell">
                  <span className="team-bar" style={{ background: glowColour(normaliseHex(r.color_hex, '#888')) }} />
                  <span className="code">{driverCode(r.driver_name)}</span>
                  <span className="who"><span className="name">{r.driver_name}</span><span className="team">{r.team_name}</span></span>
                </div>
              </td>
              {(['q1_time', 'q2_time', 'q3_time'] as const).map((k) => (
                <td key={k} className="r num" style={{ color: r[k] != null && Number(r[k]) === fastest[k] ? 'var(--purple)' : r[k] == null ? 'var(--text-3)' : undefined }}>
                  {lapTime(r[k])}
                </td>
              ))}
              <td className="r num muted">{r.gap_to_pole ? `+${Number(r.gap_to_pole).toFixed(3)}` : r.final_position === 1 ? 'Pole' : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
