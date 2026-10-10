import { driverCode } from '../names'
import { glowColour, normaliseHex } from '../theme/color'
import type { TeammatePairs } from '../types'

type Pair = TeammatePairs['pairs'][number]

function Split({ a, b, colour }: { a: number; b: number; colour: string }) {
  const total = a + b
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '28px 1fr 28px', alignItems: 'center', gap: 8 }}>
      <span className="num" style={{ fontWeight: 700, textAlign: 'right' }}>{a}</span>
      <div style={{ display: 'flex', height: 8, borderRadius: 2, overflow: 'hidden', background: 'var(--surface-3)' }}>
        {total > 0 && <>
          <div style={{ flexGrow: a, background: colour }} />
          <div style={{ flexGrow: b, background: 'var(--text-3)' }} />
        </>}
      </div>
      <span className="num" style={{ fontWeight: 700 }}>{b}</span>
    </div>
  )
}

/** One team's teammate battle: qualifying, race (both classified) and points, side by side. */
export function HeadToHead({ pair }: { pair: Pair }) {
  const [a, b] = pair.drivers
  const colour = glowColour(normaliseHex(pair.team.color_hex, '#888'))
  return (
    <div className="panel">
      <div className="panel-head">
        <h3 style={{ display: 'flex', alignItems: 'center', gap: 8 }}><span className="team-bar" style={{ background: colour }} />{pair.team.name}</h3>
        <span className="aside">{pair.rounds} round{pair.rounds === 1 ? '' : 's'}</span>
      </div>
      <div className="panel-body">
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 10 }}>
          <span><span className="code">{driverCode(a.name)}</span> <span className="muted small">{a.name}</span></span>
          <span><span className="muted small">{b.name}</span> <span className="code">{driverCode(b.name)}</span></span>
        </div>
        {[['Qualifying', a.quali_wins, b.quali_wins], ['Race', a.race_wins, b.race_wins], ['Points', a.points, b.points]].map(([label, x, y]) => (
          <div key={label as string} style={{ marginBottom: 8 }}>
            <div className="cap" style={{ textAlign: 'center', marginBottom: 4 }}>{label}</div>
            <Split a={x as number} b={y as number} colour={colour} />
          </div>
        ))}
      </div>
    </div>
  )
}
