import { pointsSystem, titleMath, type Standing } from '../titleMath'
import type { SeasonResults } from '../types'

/** What the title fight still allows: points available, who is still in it, and whether the
    leader could seal it at the next round. Arithmetic on the real points system only. */
export function TitleMaths({ kind, matrix, standings }: { kind: 'drivers' | 'constructors'; matrix: SeasonResults; standings: Standing[] }) {
  const left = matrix.rounds.filter((r) => !r.raced)
  if (!left.length || standings.length < 2) return null
  // a sprint already run this weekend no longer counts as available
  const sprintDone = (round: number) => matrix.drivers.some((d) => d.results.some((c) => c.round === round && c.sprint))
  const sprints = left.filter((r) => r.sprint && !sprintDone(r.round)).length
  const next = left[0]
  const m = titleMath(standings, left.length, sprints, pointsSystem(matrix.season, kind), next.sprint && !sprintDone(next.round))
  const alive = m.rows.filter((r) => r.alive)
  const leader = m.rows[0]
  return (
    <div className="panel">
      <div className="panel-head"><h3>Title maths</h3><span className="aside">{left.length} race{left.length === 1 ? '' : 's'}{sprints ? ` + ${sprints} sprint${sprints === 1 ? '' : 's'}` : ''} left</span></div>
      <div className="record-list">
        <div><div className="big">{m.available}</div><div className="cap">Points still available{kind === 'constructors' ? ' (both cars)' : ''}</div></div>
        <div><div className="big">{alive.length}</div><div className="cap">{kind === 'drivers' ? 'Drivers' : 'Teams'} still able to win</div></div>
      </div>
      <div className="panel-body">
        <p style={{ margin: '0 0 10px' }}>
          {m.decided
            ? <><b>{leader.name}</b> cannot be caught: the title is decided.</>
            : m.canClinchNext
              ? <><b>{leader.name}</b> can seal it at the next round ({next.track_name}) with a maximum score there if nobody else scores.</>
              : <><b>{leader.name}</b> leads by {m.rows[1].gap} with {m.available} still available.</>}
        </p>
        <table className="timing">
          <tbody>
            {m.rows.slice(0, 6).map((r) => (
              <tr key={r.id}>
                <td>{r.name}</td>
                <td className="r num dim">{r.gap ? `-${r.gap}` : 'Leader'}</td>
                <td className="r num">max {Math.round(r.maxFinal)}</td>
                <td className={r.alive ? 'hit' : 'miss'} style={{ textAlign: 'right' }}>{r.alive ? 'In it' : 'Out'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="dim small" style={{ margin: '10px 0 0' }}>
          {kind === 'drivers'
            ? `"Max" is the points total if they win every race${sprints ? ' and sprint' : ''} left.`
            : `"Max" is the points total if they finish 1-2 in every race${sprints ? ' and sprint' : ''} left.`} A tie on points goes to the most wins.
        </p>
      </div>
    </div>
  )
}
