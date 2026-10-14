import { shortName } from '../format'
import {
  groupResult, groupRows, marginWords, titleScenario, type Contender, type Kind, type RivalNeed, type RoundLeft,
} from '../titleScenarios'
import type { SeasonResults } from '../types'

/** Rounds still to run, the next first; a sprint already run this weekend is no longer open. */
export function roundsLeft(matrix: SeasonResults): RoundLeft[] {
  const sprintDone = (round: number) => matrix.drivers.some((d) => d.results.some((c) => c.round === round && c.sprint))
  return matrix.rounds.filter((r) => !r.raced)
    .map((r) => ({ round: r.round, track: r.track_name, sprint: r.sprint && !sprintDone(r.round) }))
}

function condition(n: RivalNeed, kind: Kind): string {
  const who = shortName(n.rival.name, kind)
  return n.need === 'no-points' ? `${who} does not score` : `${who} finishes P${n.need} or lower`
}

/** What the next round can decide: the exact results that settle the title there, from the
    points system alone. */
export function TitleScenarios({ kind, matrix, standings }: { kind: Kind; matrix: SeasonResults; standings: Contender[] }) {
  const rounds = roundsLeft(matrix)
  const s = titleScenario(standings, rounds, matrix.season, kind)
  if (!s || s.mode === 'decided') return null
  const lead = shortName(s.leader.name, kind)
  const where = s.round.track

  return (
    <div className="panel">
      <div className="panel-head"><h3>What {where} decides</h3><span className="aside">round {s.round.round}</span></div>
      <div className="panel-body scenario">
        {s.mode === 'not-this-round' && (
          <p className="scenario-lead">
            The title cannot be decided in {where}.{' '}
            {s.earliest
              ? <>The earliest {lead} can seal it is <b>{s.earliest.track}</b> (round {s.earliest.round}), by {kind === 'drivers' ? 'winning' : 'finishing 1-2 in'} every
                race{rounds.some((r) => r.sprint && r.round <= s.earliest!.round) ? ' and sprint' : ''} until then with every rival scoring nothing.</>
              : <>It stays open until the final round.</>}
          </p>
        )}

        {s.mode === 'positions' && (
          <>
            <p className="scenario-lead"><b>{lead}</b> is champion in {where} if:</p>
            <ul className="scenario-list">
              {groupRows(s.rows).map((g) => {
                const conds = g.needs.filter((n) => n.need !== 'any').map((n) => condition(n, kind))
                const res = groupResult(g)
                return (
                  <li key={`${g.from}-${g.to}`}>
                    <span className="pos-box">{res.label}</span>
                    <span>{lead} {res.text}{conds.length ? <> and {conds.join(' and ')}</> : <>, whatever anyone else does</>}</span>
                  </li>
                )
              })}
            </ul>
            <p className="dim small" style={{ margin: '10px 0 0' }}>
              Anything else and the fight goes on{rounds[1] ? ` to ${rounds[1].track}` : ''}. In points, it is settled there
              if {s.margins.map((m) => marginWords(lead, shortName(m.rival.name, kind), m.needed)).join(', and ')}.
            </p>
          </>
        )}

        {s.mode === 'margin' && (
          <>
            <p className="scenario-lead">
              <b>{lead}</b> {kind === 'drivers' ? 'is' : 'are'} champion in {where} if, in points{s.round.sprint ? ' across the sprint and the race' : ''}:
            </p>
            <ul className="scenario-list">
              {[...s.margins].sort((a, b) => b.needed - a.needed).map((m) => (
                <li key={m.rival.id}><span className="pos-box">{m.needed > 0 ? `+${m.needed}` : `−${-m.needed}`}</span>
                  <span>{marginWords(lead, shortName(m.rival.name, kind), m.needed)}</span></li>
              ))}
            </ul>
            <p className="dim small" style={{ margin: '10px 0 0' }}>
              The most anyone can gain on a rival there is {s.maxMargin} points{kind === 'constructors' ? ' (a 1-2 with the rival scoring nothing)' : ''}.
            </p>
          </>
        )}

        <p className="dim small" style={{ margin: '10px 0 0' }}>
          Points arithmetic only, no prediction. A tie on points goes to the most wins; a tie is only counted here
          when {lead} would win it for certain.
        </p>
      </div>
    </div>
  )
}
