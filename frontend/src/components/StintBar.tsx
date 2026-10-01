import { compoundColor, compoundLetter } from '../format'

interface BarStint {
  compound: string | null
  start_lap: number
  end_lap: number
}

/** One horizontal bar per strategy; each stint's width is proportional to its laps. */
export function StintBar({ stints, totalLaps }: { stints: BarStint[]; totalLaps: number }) {
  return (
    <div className="stint-bar" role="img"
         aria-label={stints.map((s) => `${s.compound ?? 'unknown'} laps ${s.start_lap}-${s.end_lap}`).join(', ')}>
      {stints.map((s) => {
        const laps = s.end_lap - s.start_lap + 1
        return (
          <div key={`${s.start_lap}-${s.compound}`} className="stint"
               style={{ width: `${(100 * laps) / totalLaps}%`, background: compoundColor(s.compound) }}
               title={`${s.compound ?? 'Unknown'}: laps ${s.start_lap}–${s.end_lap} (${laps} laps)`}>
            <span>{compoundLetter(s.compound)}</span>
          </div>
        )
      })}
    </div>
  )
}

export function CompoundLegend() {
  return (
    <div className="legend">
      {['SOFT', 'MEDIUM', 'HARD', 'INTERMEDIATE', 'WET'].map((c) => (
        <span key={c}>
          <i style={{ background: compoundColor(c) }} /> {c.toLowerCase()}
        </span>
      ))}
    </div>
  )
}
