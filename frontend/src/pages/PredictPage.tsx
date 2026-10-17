import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../api'
import { ErrorBox } from '../components/Status'
import { pct, shortName } from '../format'
import { useAsync } from '../hooks'
import { driverCode } from '../names'
import {
  championKey, closesAt, fitOrder, formatClose, isClosed, isLocked, moveBy, moveTo, orderPoints, raceKey, scoreOrder, startingOrder,
  validChampionPick, validRacePick, withOrder, withWinner, type ChampionPick, type Finisher, type OrderScore, type RacePick,
} from '../picks'
import { roundsLeft } from '../race/TitleScenarios'
import { SEASONS } from '../seasons'
import { listPicks, readPick, writePick } from '../storage'
import { glowColour, normaliseHex } from '../theme/color'
import {
  aliveRivals, applyRound, maxPerRound, outOfReach, available, RACE_POINTS, type Contender, type Kind,
} from '../titleScenarios'
import type { RaceDetail, SeasonResults, WinnerDriver, WinnerPrediction } from '../types'
import { LightsOut } from '../components/LightsOut'
import { useFlip } from '../motion/useFlip'

const colourOf = (hex: string | null | undefined) => glowColour(normaliseHex(hex, '#888888'))

/** The clock, ticking every 30 s, so picks close on screen without a reload. */
function useNow(): Date {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const t = window.setInterval(() => setNow(new Date()), 30_000)
    return () => window.clearInterval(t)
  }, [])
  return now
}

export function PredictPage() {
  const next = useAsync(() => api.nextWinnerPrediction(), [])
  const p = next.data
  const season = p?.season ?? SEASONS[0]
  const matrix = useAsync(() => api.seasonResults(season), [season])
  const now = useNow()

  return (
    <div className="page">
      <div className="page-title">
        <div>
          <span className="kicker"><b>{p ? `Round ${p.round}` : season}</b>{p ? ` · ${p.track} · ${p.season}` : ''}</span>
          <h1>Your predictions</h1>
        </div>
        {p && <ClosingBadge raceDate={p.race_date} now={now} />}
      </div>
      <p className="muted" style={{ margin: '-6px 0 18px', maxWidth: 760 }}>
        Pick the winner and the whole finishing order of the next Grand Prix, and this season's constructors' champion.
        Lock them in before the race; afterwards you see how close you were, next to Apex21's own forecast.
        No account: your picks are saved in this browser only.
      </p>

      {(next.error || matrix.error) && <ErrorBox error={(next.error ?? matrix.error)!} />}
      {(next.loading || matrix.loading) && <LightsOut height={320} />}

      {!next.loading && !p && <div className="panel empty">No upcoming race to predict yet: the calendar opens with the next season.</div>}
      {p && matrix.data !== undefined && <RacePicks p={p} matrix={matrix.data} now={now} />}

      <PastPicks currentRaceId={p?.race_id ?? null} now={now} />

      {!matrix.loading && matrix.data && <ConstructorsPick matrix={matrix.data} />}
    </div>
  )
}

function ClosingBadge({ raceDate, now }: { raceDate: string; now: Date }) {
  if (isClosed(raceDate, now)) return <span className="badge low">Picks closed</span>
  const hours = (closesAt(raceDate).getTime() - now.getTime()) / 3_600_000
  return (
    <span className={`badge ${hours < 24 ? 'medium' : ''}`} title="00:00 UTC on race day, before the start at every circuit">
      Picks close {formatClose(raceDate)}
    </span>
  )
}

// --- the next race ----------------------------------------------------------------------------

function RacePicks({ p, matrix, now }: { p: WinnerPrediction; matrix: SeasonResults | null; now: Date }) {
  const key = raceKey(p.race_id)
  const stage = p.stages[p.stage]
  const lineup = useMemo(() => stage?.drivers ?? [], [stage])
  const byId = useMemo(() => new Map(lineup.map((d) => [d.driver_id, d])), [lineup])
  const blank: RacePick = { v: 1, raceId: p.race_id, season: p.season, round: p.round, track: p.track, raceDate: p.race_date,
    winner: null, order: null, lockedAt: null, savedAt: new Date().toISOString() }
  const [stored, setPick] = useState<RacePick>(() => {
    const saved = readPick<unknown>(key)
    return validRacePick(saved) && saved.raceId === p.race_id ? saved : blank
  })
  const locked = isLocked(stored, now)
  // an open order follows line-up changes (a stand-in driver); a locked one stays as it was
  const pick = useMemo(() => (stored.order && !locked
    ? { ...stored, order: fitOrder(stored.order, lineup.map((d) => d.driver_id)) } : stored), [stored, locked, lineup])
  const [selected, setSelected] = useState<number | null>(null)
  const orderRef = useRef<HTMLDivElement>(null)
  useFlip(orderRef, [pick.order?.join(',')])
  const gridKnown = lineup.length > 0 && lineup.every((d) => d.grid != null)
  const standingsOrder = useMemo(() => [...(matrix?.drivers ?? [])].sort((a, b) => b.points - a.points).map((d) => d.id), [matrix])

  const save = (next: RacePick) => {
    const stamped = { ...next, savedAt: new Date().toISOString() }
    setPick(stamped)
    writePick(key, stamped)
  }
  const chooseWinner = (id: number) => !locked && save(withWinner(pick, id))
  const startOrder = () => {
    const base = startingOrder(lineup, standingsOrder)
    save(withOrder(pick, pick.winner ? moveTo(base, pick.winner, 0) : base))
  }
  const reorder = (order: number[]) => !locked && save(withOrder(pick, order))
  const clickRow = (id: number) => {
    if (locked || !pick.order) return
    if (selected == null) return setSelected(id)
    if (selected !== id) reorder(moveTo(pick.order, selected, pick.order.indexOf(id)))
    setSelected(null)
  }
  const lock = () => pick.winner && save({ ...pick, lockedAt: new Date().toISOString() })
  const reset = () => {
    setSelected(null)
    setPick(blank)
    writePick(key, null)
  }

  if (!lineup.length) return <div className="panel empty">The line-up for {p.track} is not known yet.</div>
  const favourite = stage ? [...stage.drivers].sort((a, b) => b.p_win - a.p_win)[0] : null
  const yours = pick.winner != null ? byId.get(pick.winner) : undefined

  return (
    <div className="grid-main predict-grid" style={{ marginBottom: 18 }}>
      <div className="panel">
        <div className="panel-head">
          <h3>Finishing order</h3>
          <span className="aside">{pick.order ? (locked ? 'locked' : 'tap a driver, then the place to move them to') : 'optional'}</span>
        </div>
        {pick.order ? (
          <div className="order-list" role="list" ref={orderRef}>
            {pick.order.map((id, i) => {
              const d = byId.get(id)
              if (!d) return null
              return (
                <div key={id} data-flip={id} role="listitem" className={`order-row ${selected === id ? 'sel' : ''} ${selected != null && selected !== id ? 'target' : ''}`}
                     onClick={() => clickRow(id)}>
                  <span className={`pos-box ${i < 3 ? `p${i + 1}` : ''}`}>{i + 1}</span>
                  <span className="team-bar" style={{ background: colourOf(d.color_hex) }} />
                  <span className="code">{driverCode(d.driver_name ?? '???')}</span>
                  <span className="who"><span className="name">{d.driver_name}</span><span className="team">{d.team_name}</span></span>
                  <span className="pts num dim">{RACE_POINTS[i] ? `+${RACE_POINTS[i]}` : ''}</span>
                  {!locked && (
                    <span className="nudge" onClick={(e) => e.stopPropagation()}>
                      <button type="button" aria-label={`Move ${d.driver_name} up`} disabled={i === 0} onClick={() => reorder(moveBy(pick.order!, id, -1))}>▲</button>
                      <button type="button" aria-label={`Move ${d.driver_name} down`} disabled={i === pick.order!.length - 1}
                              onClick={() => reorder(moveBy(pick.order!, id, 1))}>▼</button>
                    </span>
                  )}
                </div>
              )
            })}
          </div>
        ) : (
          <div className="panel-body">
            <p style={{ marginTop: 0 }}>
              Go further than the winner: put all {lineup.length} drivers in the order you think they finish. After the race you
              see how many you got exactly right, how many within one place, and the podium.
            </p>
            {locked
              ? <p className="dim small" style={{ marginBottom: 0 }}>Not made for this race.</p>
              : <button type="button" className="btn" onClick={startOrder}>Build my order{gridKnown ? ' (from the grid)' : ' (from the standings)'}</button>}
          </div>
        )}
      </div>

      <div className="stack">
        <div className="panel">
          <div className="panel-head"><h3>Race winner</h3><span className="aside">{yours ? driverCode(yours.driver_name ?? '???') : 'pick one'}</span></div>
          <div className="pick-grid" role="radiogroup" aria-label="Race winner">
            {[...lineup].sort((a, b) => (a.team_name ?? '').localeCompare(b.team_name ?? '')).map((d) => (
              <button key={d.driver_id} type="button" role="radio" aria-checked={pick.winner === d.driver_id} disabled={locked && pick.winner !== d.driver_id}
                      className={`pick-chip ${pick.winner === d.driver_id ? 'on' : ''}`} onClick={() => chooseWinner(d.driver_id)}>
                <span className="team-bar" style={{ background: colourOf(d.color_hex) }} />
                <span className="code">{driverCode(d.driver_name ?? '???')}</span>
                <span className="dim small">{shortName(d.driver_name ?? '', 'drivers')}</span>
              </button>
            ))}
          </div>
          <div className="panel-body pick-actions">
            {locked ? (
              <>
                <span className="badge high">{pick.lockedAt ? `Locked ${new Date(pick.lockedAt).toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' })}` : 'Closed'}</span>
                {yours && favourite && (
                  <span className="small muted">
                    Apex21 gives {shortName(yours.driver_name ?? '', 'drivers')} {yours.p_win < 0.001 ? 'under 0.1%' : pct(yours.p_win, 1)} to win
                    {favourite.driver_id !== yours.driver_id ? <> (its favourite: {shortName(favourite.driver_name ?? '', 'drivers')}, {pct(favourite.p_win, 0)})</> : ' — its favourite too'}.
                  </span>
                )}
              </>
            ) : (
              <>
                <button type="button" className="btn primary" disabled={!pick.winner} onClick={lock}>Lock my picks</button>
                {(pick.winner || pick.order) && <button type="button" className="btn" onClick={reset}>Reset</button>}
                <span className="small dim">{pick.winner ? 'Saved on this device. Locking makes it final.' : 'Choose a winner to lock your picks.'}</span>
              </>
            )}
          </div>
        </div>

        {pick.order && matrix && <TitleEffect matrix={matrix} order={pick.order} round={p.round} track={p.track} lineup={lineup} />}
      </div>
    </div>
  )
}

/** The championship after the race if the visitor's order comes true (race points only). */
function TitleEffect({ matrix, order, round, track, lineup }: { matrix: SeasonResults; order: number[]; round: number; track: string; lineup: WinnerDriver[] }) {
  const rounds = roundsLeft(matrix)
  if (!rounds.length || rounds[0].round !== round) return null
  const after = rounds.slice(1)
  const pts = orderPoints(order, RACE_POINTS)
  const teamOf = new Map(matrix.drivers.map((d) => [d.id, d.team?.id ?? -1]))
  const lines = (['drivers', 'constructors'] as Kind[]).map((kind) => {
    const standings: Contender[] = kind === 'drivers'
      ? matrix.drivers.map((d) => ({ id: d.id, name: d.name, points: d.points, wins: d.wins }))
      : matrix.teams.map((t) => ({ id: t.id, name: t.name ?? '?', points: t.points, wins: t.wins }))
    const add = kind === 'drivers' ? pts : new Map<number, number>()
    if (kind === 'constructors') for (const [id, v] of pts) add.set(teamOf.get(id) ?? -1, (add.get(teamOf.get(id) ?? -1) ?? 0) + v)
    const winner = kind === 'drivers' ? order[0] : teamOf.get(order[0]) ?? null
    const table = applyRound(standings, add, winner ?? null)
    // a sprint this weekend is not in the order: it stays open as points still to win
    const sprintOpen = rounds[0].sprint ? maxPerRound(matrix.season, kind).sprint : 0
    const left = available(after, matrix.season, kind) + sprintOpen
    const sprints = after.filter((r) => r.sprint).length + (rounds[0].sprint ? 1 : 0)
    const champion = table.length > 1 && aliveRivals(standings, rounds, matrix.season, kind).length > 0
      && table.slice(1).every((r) => outOfReach(table[0], r, left, after.length, sprints))
    return { kind, table, left, champion }
  })
  const missing = lineup.filter((d) => !order.includes(d.driver_id)).length

  return (
    <div className="panel">
      <div className="panel-head"><h3>If your order happens</h3><span className="aside">championship after {track}</span></div>
      <div className="panel-body">
        {lines.map(({ kind, table, left, champion }) => (
          <p key={kind} style={{ margin: '0 0 8px' }}>
            {champion
              ? <><b>{shortName(table[0].name, kind)}</b> {kind === 'drivers' ? 'is' : 'are'} {kind === 'drivers' ? "world champion" : "constructors' champions"} in {track}.</>
              : <><b>{shortName(table[0].name, kind)}</b> lead{kind === 'drivers' ? 's' : ''} {shortName(table[1]?.name ?? '', kind)} by{' '}
                {Math.round(table[0].points - (table[1]?.points ?? 0))} with {left} still to win.</>}
          </p>
        ))}
        <p className="dim small" style={{ margin: 0 }}>Race points from your order{rounds[0].sprint ? '; the sprint is left open' : ''}.{missing ? ` ${missing} driver(s) not placed.` : ''}</p>
      </div>
    </div>
  )
}

// --- after the race ---------------------------------------------------------------------------

function finishersOf(race: RaceDetail): Finisher[] {
  const rows = [...race.results].sort((a, b) => (a.finishing_position ?? 99) - (b.finishing_position ?? 99))
  return rows.map((r, i) => ({
    driverId: r.driver_id, position: r.finishing_position ?? i + 1,
    classified: r.status == null || /^(Finished|Lapped|\+\d+ Laps?)$/i.test(r.status),
  }))
}

function PastPicks({ currentRaceId, now }: { currentRaceId: number | null; now: Date }) {
  const [showAll, setShowAll] = useState(false)
  const picks = useMemo(() => listPicks<RacePick>('predict:race:')
    .map(([, v]) => v)
    .filter(validRacePick)
    .filter((v) => v.raceId !== currentRaceId && (v.winner != null || v.order != null) && isClosed(v.raceDate, now))
    .sort((a, b) => b.raceDate.localeCompare(a.raceDate)), [currentRaceId, now])
  if (!picks.length) return null
  const shown = showAll ? picks : picks.slice(0, 4)
  return (
    <>
      <div className="section-head" style={{ marginTop: 8 }}><h2>How you did</h2><span className="aside">your picks against the result</span></div>
      {shown.map((pk, i) => <PastPick key={pk.raceId} pick={pk} open={i === 0} />)}
      {picks.length > shown.length && <button type="button" className="btn" onClick={() => setShowAll(true)}>Show all {picks.length} races</button>}
    </>
  )
}

function PastPick({ pick, open }: { pick: RacePick; open: boolean }) {
  const race = useAsync(() => api.race(pick.raceId), [pick.raceId])
  const forecast = useAsync(() => api.forecast(pick.raceId), [pick.raceId])
  const [expanded, setExpanded] = useState(open)
  const r = race.data
  const finishers = r ? finishersOf(r) : []
  const names = new Map((r?.results ?? []).map((x) => [x.driver_id, x]))
  const yours = finishers.length ? scoreOrder(pick.order ?? (pick.winner != null ? [pick.winner] : []), finishers, pick.winner) : null
  const fc = forecast.data
  const modelOrder = fc ? [...fc.drivers].sort((a, b) => a.predicted_position - b.predicted_position).map((d) => d.driver_id) : null
  const modelWinner = fc ? [...fc.drivers].sort((a, b) => b.p_win - a.p_win)[0]?.driver_id ?? null : null
  const model = finishers.length && modelOrder ? scoreOrder(modelOrder, finishers, modelWinner) : null
  const winnerRow = finishers.find((f) => f.position === 1)
  const winnerName = winnerRow ? names.get(winnerRow.driverId)?.driver_name : null
  const pickedName = pick.winner != null ? names.get(pick.winner)?.driver_name ?? 'your pick' : null

  return (
    <div className="panel" style={{ marginBottom: 12 }}>
      <div className="panel-head">
        <h3><Link to={`/race/${pick.raceId}`}>{pick.season} R{pick.round} {pick.track}</Link></h3>
        <span className="aside">
          {race.loading ? 'loading…' : !finishers.length ? 'waiting for the result' : yours?.winnerRight ? 'winner right' : 'winner missed'}
        </span>
      </div>
      {race.error && <div className="panel-body"><ErrorBox error={race.error} /></div>}
      {!race.loading && !finishers.length && !race.error && (
        <div className="panel-body dim small">The result is not in yet; it appears here once the race data is loaded.</div>
      )}
      {yours && (
        <>
          <div className="kv pick-score">
            <div><div className={`v ${yours.winnerRight ? 'hit' : 'miss'}`}>{yours.winnerRight ? '✓' : '✗'}</div>
              <div className="k">Winner · you said {pickedName ? shortName(pickedName, 'drivers') : '–'}, {winnerName ? shortName(winnerName, 'drivers') : '?'} won</div></div>
            {pick.order && <ScoreCells s={yours} />}
          </div>
          {model && (
            <div className="panel-foot">
              Apex21's forecast for this race: winner {model.winnerRight ? 'right' : 'missed'}
              {pick.order ? <> · {model.exact} exact · {model.withinOne} within one · podium {model.podium}/3 · {model.avgMiss?.toFixed(1)} places off on average</> : null}.
            </div>
          )}
          {pick.order && (
            <div className="panel-body" style={{ paddingTop: 10 }}>
              <button type="button" className="btn" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>
                {expanded ? 'Hide' : 'Show'} your order against the result
              </button>
            </div>
          )}
          {pick.order && expanded && <OrderTable score={yours} names={names} />}
        </>
      )}
    </div>
  )
}

function ScoreCells({ s }: { s: OrderScore }) {
  return (
    <>
      <div><div className="v num">{s.exact}<small>/{s.compared}</small></div><div className="k">Exact positions</div></div>
      <div><div className="v num">{s.withinOne}<small>/{s.compared}</small></div><div className="k">Within one place</div></div>
      <div><div className="v num">{s.podium}<small>/3</small></div><div className="k">Podium drivers in your top 3</div></div>
      <div><div className="v num">{s.avgMiss != null ? s.avgMiss.toFixed(1) : '–'}</div><div className="k">Places off on average</div></div>
    </>
  )
}

function OrderTable({ score, names }: { score: OrderScore; names: Map<number, RaceDetail['results'][number]> }) {
  return (
    <div className="table-wrap">
      <table className="timing">
        <thead><tr><th>You</th><th>Driver</th><th className="r">Finished</th><th className="r">Off by</th></tr></thead>
        <tbody>
          {score.rows.map((r) => {
            const d = names.get(r.driverId)
            return (
              <tr key={r.driverId}>
                <td className="pos-cell"><span className={`pos-box ${r.predicted <= 3 ? `p${r.predicted}` : ''}`}>{r.predicted}</span></td>
                <td>
                  <div className="driver-cell">
                    <span className="team-bar" style={{ background: colourOf(d?.color_hex) }} />
                    <span className="code">{driverCode(d?.driver_name ?? '???')}</span>
                    <span className="who"><span className="name">{d?.driver_name ?? 'did not race'}</span></span>
                  </div>
                </td>
                <td className="r num">{r.actual == null ? '–' : r.classified ? `P${r.actual}` : <span className="dim">{d?.display ?? 'DNF'}</span>}</td>
                <td className={`r num ${r.diff === 0 ? 'hit' : ''}`}>
                  {r.diff == null ? '–' : r.diff === 0 ? 'exact' : `${Math.abs(r.diff)} ${r.diff > 0 ? 'lower' : 'higher'}`}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// --- the season ------------------------------------------------------------------------------

function ConstructorsPick({ matrix }: { matrix: SeasonResults }) {
  const season = matrix.season
  const key = championKey(season)
  const champ = useAsync(() => api.championship(season), [season])
  const [pick, setPick] = useState<ChampionPick | null>(() => {
    const saved = readPick<unknown>(key)
    return validChampionPick(saved) && saved.season === season ? saved : null
  })
  const [choice, setChoice] = useState<number | null>(pick?.teamId ?? null)
  const teams = [...matrix.teams].sort((a, b) => b.points - a.points)
  const rounds = roundsLeft(matrix)
  const over = rounds.length === 0 && (matrix.latest_round ?? 0) > 0
  const standings: Contender[] = teams.map((t) => ({ id: t.id, name: t.name ?? '?', points: t.points, wins: t.wins }))
  const settled = over || (standings.length > 1 && aliveRivals(standings, rounds, season, 'constructors').length === 0)
  const locked = pick?.lockedAt != null || settled
  const chances = new Map((champ.data?.constructors ?? []).map((e) => [e.team_id ?? -1, e.p_title]))
  const picked = teams.find((t) => t.id === (pick?.teamId ?? choice))

  const lock = () => {
    if (choice == null) return
    const next: ChampionPick = { v: 1, season, teamId: choice, afterRound: matrix.latest_round ?? 0, lockedAt: new Date().toISOString(),
      savedAt: new Date().toISOString() }
    setPick(next)
    writePick(key, next)
  }

  return (
    <div className="panel" id="constructors" style={{ marginTop: 18 }}>
      <div className="panel-head">
        <h3>{season} constructors' champion</h3>
        <span className="aside">{settled ? (over ? 'season over' : 'title decided') : pick?.lockedAt ? `locked after round ${pick.afterRound}` : 'pick a team'}</span>
      </div>
      {settled && (
        <div className="panel-body">
          <b>{shortName(teams[0]?.name ?? '?', 'constructors')}</b> {over ? 'won' : 'have won'} the {season} constructors' title.{' '}
          {pick ? (pick.teamId === teams[0]?.id ? <span className="hit">You called it after round {pick.afterRound}.</span>
            : <span className="miss">You picked {shortName(picked?.name ?? '?', 'constructors')} after round {pick.afterRound}.</span>)
            : <span className="dim">No pick was made.</span>}
        </div>
      )}
      {!settled && (
        <>
          <div className="pick-grid teams" role="radiogroup" aria-label="Constructors' champion">
            {teams.map((t) => {
              const on = (pick?.teamId ?? choice) === t.id
              return (
                <button key={t.id} type="button" role="radio" aria-checked={on} disabled={locked && !on}
                        className={`pick-chip ${on ? 'on' : ''}`} onClick={() => !locked && setChoice(t.id)}>
                  <span className="team-bar" style={{ background: colourOf(t.color_hex) }} />
                  <span>{shortName(t.name ?? '?', 'constructors')}</span>
                  <span className="dim small num">{Math.round(t.points)} pts</span>
                </button>
              )
            })}
          </div>
          <div className="panel-body pick-actions">
            {pick?.lockedAt ? (
              <span className="small muted">
                Your pick: <b>{shortName(picked?.name ?? '?', 'constructors')}</b>, locked after round {pick.afterRound}.
                {chances.has(pick.teamId) && <> Apex21 gives them {pct(chances.get(pick.teamId)!, chances.get(pick.teamId)! < 0.1 ? 1 : 0)} for the title.</>}
              </span>
            ) : (
              <>
                <button type="button" className="btn primary" disabled={choice == null} onClick={lock}>Lock my pick</button>
                <span className="small dim">Once locked it stays until the title is decided; the round you locked it after is kept.</span>
              </>
            )}
          </div>
        </>
      )}
    </div>
  )
}
