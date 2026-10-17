import { useMemo, useRef } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'

import { api } from '../api'
import { LineChart, type Series } from '../charts/LineChart'
import { ErrorBox } from '../components/Status'
import { pct } from '../format'
import { useAsync } from '../hooks'
import { TitleMaths } from '../race/TitleMaths'
import { SeasonReplay } from '../race/SeasonReplay'
import { TitleScenarios } from '../race/TitleScenarios'
import { driverCode } from '../names'
import { SEASONS } from '../seasons'
import { glowColour, normaliseHex } from '../theme/color'
import type { Championship, ChampionshipHistory, SeasonResults } from '../types'
import { LightsOut } from '../components/LightsOut'
import { Empty } from '../components/Empty'
import { useFlip } from '../motion/useFlip'

type Kind = 'drivers' | 'constructors'

interface StandingRow {
  id: number
  name: string
  short: string
  sub: string | null
  colour: string | null
  points: number
  wins: number
  podiums: number | null
  projected: number | null
  title: number | null
  dashed: boolean
}

function teamCode(name: string): string {
  return name.replace(/[^A-Za-z ]/g, '').split(' ').filter(Boolean)[0]?.slice(0, 3).toUpperCase() ?? '???'
}

function standings(kind: Kind, m: SeasonResults | null | undefined, c: Championship | null | undefined): StandingRow[] {
  const proj = new Map((c?.[kind] ?? []).map((e) => [(kind === 'drivers' ? e.driver_id : e.team_id) ?? -1, e]))
  if (m) {
    if (kind === 'drivers') {
      const seenTeam = new Set<number>()
      return m.drivers.map((d) => {
        const teamId = d.team?.id ?? -1
        const dashed = seenTeam.has(teamId)
        seenTeam.add(teamId)
        const e = proj.get(d.id)
        return { id: d.id, name: d.name, short: driverCode(d.name), sub: d.team?.name ?? null, colour: d.team?.color_hex ?? null,
          points: d.points, wins: d.wins, podiums: d.podiums, projected: e?.expected_points ?? null, title: e?.p_title ?? null, dashed }
      })
    }
    return m.teams.map((t) => {
      const e = proj.get(t.id)
      return { id: t.id, name: t.name ?? '?', short: teamCode(t.name ?? '?'), sub: null, colour: t.color_hex, points: t.points,
        wins: t.wins, podiums: null, projected: e?.expected_points ?? null, title: e?.p_title ?? null, dashed: false }
    })
  }
  return (c?.[kind] ?? []).map((e) => ({
    id: (kind === 'drivers' ? e.driver_id : e.team_id) ?? 0, name: (kind === 'drivers' ? e.driver_name : e.team_name) ?? '?',
    short: kind === 'drivers' ? driverCode(e.driver_name ?? '???') : teamCode(e.team_name ?? '?'),
    sub: kind === 'drivers' ? e.team_name ?? null : null, colour: e.color_hex ?? null, points: e.points_now, wins: 0, podiums: null,
    projected: e.expected_points, title: e.p_title, dashed: false,
  }))
}

function progression(kind: Kind, m: SeasonResults, rows: StandingRow[], top: number): Series[] {
  const raced = m.rounds.filter((r) => r.raced).map((r) => r.round)
  return rows.slice(0, top).map((row) => {
    let total = 0
    const perRound = kind === 'drivers'
      ? new Map(m.drivers.find((d) => d.id === row.id)!.results.map((c) => [c.round, (c.race?.points ?? 0) + (c.sprint?.points ?? 0)]))
      : new Map(m.teams.find((t) => t.id === row.id)!.by_round.map((b) => [b.round, b.points]))
    return { id: row.id, label: row.name, short: row.short, colour: row.colour, dashed: row.dashed,
      points: raced.map((r) => ({ x: r, y: (total += perRound.get(r) ?? 0) })) }
  })
}

function titleSeries(kind: Kind, h: ChampionshipHistory, rows: StandingRow[], top: number): Series[] {
  const byId = new Map(rows.map((r) => [r.id, r]))
  return [...h[kind]]
    .sort((a, b) => (b.rounds[b.rounds.length - 1]?.p_title ?? 0) - (a.rounds[a.rounds.length - 1]?.p_title ?? 0))
    .slice(0, top)
    .map((s) => {
      const row = byId.get(s.id)
      return { id: s.id, label: s.name, short: row?.short ?? s.name.slice(0, 3).toUpperCase(), colour: row?.colour ?? null,
        dashed: row?.dashed, points: s.rounds.map((r) => ({ x: r.after_round, y: r.p_title })) }
    })
}

export function ChampionshipPage() {
  const year = Number(useParams().year ?? SEASONS[0])
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const kind: Kind = params.get('view') === 'constructors' ? 'constructors' : 'drivers'
  const champ = useAsync(() => api.championship(year), [year])
  const history = useAsync(() => api.championshipHistory(year), [year])
  const matrix = useAsync(() => api.seasonResults(year), [year])
  const c = champ.data
  const m = matrix.data
  const rows = useMemo(() => standings(kind, m, c), [kind, m, c])
  const tbody = useRef<HTMLTableSectionElement>(null)
  useFlip(tbody, [kind, rows.map((r) => r.id).join(',')])
  const leader = rows[0]?.points ?? 0
  const maxTitle = Math.max(...rows.map((r) => r.title ?? 0), 0.0001)
  const conf = c?.confidence[kind]

  return (
    <div className="page">
      <div className="page-title">
        <div>
          <span className="kicker"><b>{year}</b>{c ? ` · after round ${c.after_round} · ${c.weekends_left} race weekend${c.weekends_left === 1 ? '' : 's'} left` : ''}</span>
          <h1>World Championship</h1>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <div className="tabs" role="tablist">
            {(['drivers', 'constructors'] as Kind[]).map((k) => (
              <button key={k} role="tab" aria-selected={kind === k} className={kind === k ? 'on' : ''}
                      onClick={() => setParams(k === 'constructors' ? { view: k } : {})}>
                {k === 'drivers' ? 'Drivers' : 'Constructors'}
              </button>
            ))}
          </div>
          <select className="select" value={year} aria-label="Season"
                  onChange={(e) => navigate(`/championship/${e.target.value}${kind === 'constructors' ? '?view=constructors' : ''}`)}>
            {SEASONS.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
      </div>

      {(champ.error || matrix.error) && <ErrorBox error={(champ.error ?? matrix.error)!} />}
      {(champ.loading || matrix.loading) && <LightsOut height={300} />}

      {!champ.loading && !matrix.loading && rows.length === 0 && (
        year < SEASONS[0] ? <Empty panel icon="flag" title={`No ${year} results stored`} />
          : <Empty panel icon="lights" title={`No ${year} results yet`}>The standings and the title projection appear after the season's first race.</Empty>
      )}

      {rows.length > 0 && (
        <div className="grid-main">
          <div className="panel">
            <div className="panel-head">
              <h3>{kind === 'drivers' ? "Drivers' standings" : "Constructors' standings"}</h3>
              {conf && <span className={`badge ${conf.level}`}>Projection confidence: {conf.level}</span>}
            </div>
            <div className="table-wrap">
              <table className="timing">
                <thead>
                  <tr>
                    <th>Pos</th>
                    <th>{kind === 'drivers' ? 'Driver' : 'Team'}</th>
                    <th className="r">Pts</th>
                    <th className="r hide-sm">Gap</th>
                    <th className="r hide-sm">Wins</th>
                    <th className="r hide-sm" title="Expected final points (simulation)">Proj.</th>
                    <th>Title</th>
                  </tr>
                </thead>
                <tbody ref={tbody}>
                  {rows.map((r, i) => {
                    const colour = glowColour(normaliseHex(r.colour, '#888888'))
                    return (
                      <tr key={r.id} data-flip={`${kind}-${r.id}`}>
                        <td className="pos-cell"><span className={`pos-box ${i < 3 ? `p${i + 1}` : ''}`}>{i + 1}</span></td>
                        <td>
                          <div className="driver-cell">
                            <span className="team-bar" style={{ background: colour }} />
                            {kind === 'drivers' && <span className="code">{r.short}</span>}
                            <span className="who"><span className="name">{r.name}</span>{r.sub && <span className="team">{r.sub}</span>}</span>
                          </div>
                        </td>
                        <td className="r num" style={{ fontWeight: 700 }}>{Math.round(r.points)}</td>
                        <td className="r num dim hide-sm">{i === 0 ? '—' : `-${Math.round(leader - r.points)}`}</td>
                        <td className="r num hide-sm">{r.wins || ''}</td>
                        <td className="r num muted hide-sm">{r.projected != null ? Math.round(r.projected) : '–'}</td>
                        <td>
                          {r.title != null && r.title >= 0.0005 ? (
                            <div className="title-cell">
                              <span className="num">{pct(r.title, r.title < 0.1 ? 1 : 0)}</span>
                              <span className="meter"><i style={{ width: `${(r.title / maxTitle) * 100}%`, background: colour }} /></span>
                            </div>
                          ) : <span className="dim num">{r.title != null ? '<0.1%' : '–'}</span>}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            {conf && <div className="panel-foot">{[conf.basis, conf.chance_basis].filter(Boolean).join(' · ')}</div>}
          </div>

          <div className="stack">
            {history.data && history.data[kind].length > 0 && (
              <div className="panel">
                <div className="panel-head"><h3>Title chance by round</h3><span className="aside">simulated after each round</span></div>
                <div className="panel-body">
                  <LineChart series={titleSeries(kind, history.data, rows, 5)} xLabel={(x) => `R${x}`} yFormat={(v) => pct(v, 0)}
                             yMax={1} height={260} caption="Title chance after each round" />
                </div>
              </div>
            )}
            {m && m.latest_round && (
              <div className="panel">
                <div className="panel-head"><h3>Points progression</h3><span className="aside">incl. sprints</span></div>
                <div className="panel-body">
                  <LineChart series={progression(kind, m, rows, kind === 'drivers' ? 6 : 5)} xLabel={(x) => `R${x}`}
                             yFormat={(v) => `${Math.round(v)}`} height={260} caption="Cumulative points" />
                </div>
              </div>
            )}
            {m && m.latest_round && <SeasonReplay matrix={m} kind={kind} />}
            {m && <TitleScenarios kind={kind} matrix={m} standings={rows.map((r) => ({ id: r.id, name: r.name, points: r.points, wins: r.wins }))} />}
            {m && <TitleMaths kind={kind} matrix={m} standings={rows.map((r) => ({ id: r.id, name: r.name, points: r.points }))} />}
            <div className="panel panel-pad">
              <p className="small muted" style={{ margin: 0 }}>
                <b style={{ color: 'var(--text)' }}>How the projection works.</b> Points so far plus every remaining race
                simulated 20,000 times with the real points system, each car and driver rated from earlier results only.
                Tested on every round of 2018–2025: when it gave a driver 90%+ for the title, that driver became champion
                in 80 of 80 cases; for constructors 87.7% of 106.
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
