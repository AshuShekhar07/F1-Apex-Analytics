import { useMemo } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { api } from '../api'
import { LineChart } from '../charts/LineChart'
import { ErrorBox } from '../components/Status'
import { useAsync } from '../hooks'
import { driverCode } from '../names'
import { HeadToHead } from '../race/HeadToHead'
import { SEASONS } from '../seasons'
import { assetUrl } from '../storage'
import { usePageAccent } from '../theme/accent'
import { glowColour, normaliseHex } from '../theme/color'
import type { SeasonResults } from '../types'
import { cellOf } from './ResultsPage'

function teamDrivers(m: SeasonResults, teamId: number) {
  return m.drivers.filter((d) => d.results.some((c) => c.team_id === teamId))
}

/** Every team of a season, in championship order. */
export function TeamsPage() {
  const year = Number(useParams().year ?? SEASONS[0])
  const navigate = useNavigate()
  const matrix = useAsync(() => api.seasonResults(year), [year])
  const m = matrix.data
  return (
    <div className="page">
      <div className="page-title">
        <div>
          <span className="kicker"><b>{year}</b>{m ? ` · ${m.teams.length} teams` : ''}</span>
          <h1>Teams</h1>
        </div>
        <select className="select" value={year} aria-label="Season" onChange={(e) => navigate(`/teams/${e.target.value}`)}>
          {SEASONS.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>
      {matrix.loading && <div className="skeleton" style={{ height: 240 }} />}
      {matrix.error && <ErrorBox error={matrix.error} />}
      {m && (
        <div className="team-grid">
          {m.teams.map((t, i) => {
            const c = glowColour(normaliseHex(t.color_hex, '#666'))
            const logo = assetUrl(t.logo_url)
            return (
              <Link key={t.id} to={`/teams/${year}/${t.id}`} className="team-card" style={{ ['--c' as string]: c }}>
                <span className="rank">P{i + 1}</span>
                {logo && <img src={logo} alt="" loading="lazy" />}
                <b>{t.name}</b>
                <div className="drivers">
                  {teamDrivers(m, t.id).map((d) => <span key={d.id} className="code">{driverCode(d.name)}</span>)}
                </div>
                <div className="nums">
                  <span><b>{Math.round(t.points)}</b> pts</span>
                  <span><b>{t.wins}</b> win{t.wins === 1 ? '' : 's'}</span>
                </div>
              </Link>
            )
          })}
        </div>
      )}
    </div>
  )
}

/** One team's season: totals, race-by-race results, the teammate battle and points. */
export function TeamPage() {
  const params = useParams()
  const year = Number(params.year ?? SEASONS[0])
  const id = Number(params.id)
  const navigate = useNavigate()
  const summary = useAsync(() => api.teamSeason(id, year), [id, year])
  const matrix = useAsync(() => api.seasonResults(year), [year])
  const mates = useAsync(() => api.teammates(year), [year])
  const s = summary.data
  const m = matrix.data
  usePageAccent(s?.color_hex ?? null)

  const drivers = useMemo(() => (m ? teamDrivers(m, id) : []), [m, id])
  const position = m ? m.teams.findIndex((t) => t.id === id) + 1 : 0
  const pair = mates.data?.pairs.find((p) => p.team.id === id)
  const colour = glowColour(normaliseHex(s?.color_hex, '#888'))

  const series = useMemo(() => {
    if (!m) return []
    const raced = m.rounds.filter((r) => r.raced).map((r) => r.round)
    return drivers.map((d, i) => {
      let total = 0
      const per = new Map(d.results.filter((c) => c.team_id === id).map((c) => [c.round, (c.race?.points ?? 0) + (c.sprint?.points ?? 0)]))
      return { id: d.id, label: d.name, short: driverCode(d.name), colour: s?.color_hex ?? null, dashed: i > 0,
        points: raced.filter((r) => per.has(r)).map((r) => ({ x: r, y: (total += per.get(r) ?? 0) })) }
    })
  }, [m, drivers, id, s?.color_hex])

  if (summary.loading || matrix.loading) return <div className="page"><div className="skeleton" style={{ height: 240 }} /></div>
  if (summary.error) return <div className="page"><ErrorBox error={summary.error} /></div>
  if (!s) return <div className="page"><div className="panel empty">No results for this team in {year}. <Link to={`/teams/${year}`}>All teams</Link></div></div>

  return (
    <div className="page">
      <div className="page-title">
        <div>
          <span className="kicker"><b>{year}</b>{position ? ` · P${position} in the constructors' championship` : ''}</span>
          <h1 style={{ borderLeft: `6px solid ${colour}`, paddingLeft: 14 }}>{s.team_name}</h1>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <select className="select" value={year} aria-label="Season" onChange={(e) => navigate(`/teams/${e.target.value}/${id}`)}>
            {SEASONS.map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
          <Link to={`/teams/${year}`} className="btn">All teams</Link>
        </div>
      </div>

      <div className="panel" style={{ marginBottom: 18 }}>
        <div className="kv" style={{ gridTemplateColumns: 'repeat(6, minmax(0, 1fr))' }}>
          <div><div className="v">{Math.round(s.total_points)}</div><div className="k">Points ({Math.round(s.sprint_points)} sprint)</div></div>
          <div><div className="v">{s.wins}</div><div className="k">Wins</div></div>
          <div><div className="v">{s.podiums}</div><div className="k">Podiums</div></div>
          <div><div className="v">{s.average_finish ?? '–'}</div><div className="k">Avg finish</div></div>
          <div><div className="v">{s.average_qualifying ?? '–'}</div><div className="k">Avg qualifying</div></div>
          <div><div className="v">{s.dnfs}</div><div className="k">DNFs</div></div>
        </div>
      </div>

      <div className="grid-main">
        <div className="panel">
          <div className="panel-head"><h3>Race by race</h3><span className="aside">race · sprint in the corner</span></div>
          {m && (
            <div className="table-wrap">
              <table className="matrix" style={{ width: '100%' }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: 'left' }}>Round</th>
                    {drivers.map((d) => <th key={d.id}>{driverCode(d.name)}</th>)}
                    <th style={{ textAlign: 'right' }}>Team pts</th>
                  </tr>
                </thead>
                <tbody>
                  {m.rounds.filter((r) => r.raced).map((r) => {
                    const pts = m.teams.find((t) => t.id === id)?.by_round.find((b) => b.round === r.round)?.points ?? 0
                    return (
                      <tr key={r.round}>
                        <td className="who"><span className="dim">R{r.round}</span> <Link to={`/race/${r.race_id}`}>{r.track_name}</Link></td>
                        {drivers.map((d) => {
                          const cell = d.results.find((c) => c.round === r.round && c.team_id === id)
                          const race = cellOf(cell?.race ?? null)
                          const sprint = cell?.sprint && cell.sprint.points > 0 ? cell.sprint.position : null
                          return <td key={d.id} className={race.cls}>{race.text}{sprint != null && <sup>{sprint}</sup>}</td>
                        })}
                        <td className="pts">{pts ? Math.round(pts) : ''}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
        <div className="stack">
          {pair && <HeadToHead pair={pair} />}
          {series.length > 0 && (
            <div className="panel">
              <div className="panel-head"><h3>Points by driver</h3><span className="aside">for this team, incl. sprints</span></div>
              <div className="panel-body">
                <LineChart series={series} xLabel={(x) => `R${x}`} yFormat={(v) => `${Math.round(v)}`} height={240} caption="Cumulative points" />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
