import { Link, useNavigate, useParams } from 'react-router-dom'

import { api } from '../api'
import { ErrorBox } from '../components/Status'
import { localDay, pct } from '../format'
import { useAsync } from '../hooks'
import { driverCode } from '../names'
import { SEASONS } from '../seasons'
import { glowColour, normaliseHex } from '../theme/color'
import type { LiveRecord, MatrixResult, SeasonResults } from '../types'

/** What a results cell shows and how it is coloured. */
export function cellOf(r: MatrixResult | null): { text: string; cls: string } {
  if (!r) return { text: '', cls: 'none' }
  if (!r.classified || r.position == null) {
    const s = (r.status ?? '').toLowerCase()
    return { text: s.includes('disqualified') ? 'DSQ' : s.includes('did not start') ? 'DNS' : r.dnf ? 'Ret' : 'NC', cls: 'out' }
  }
  if (r.position <= 3) return { text: String(r.position), cls: `p${r.position}` }
  return { text: String(r.position), cls: r.points > 0 ? 'points' : '' }
}

function countryCode(country: string | null, track: string): string {
  return (country ?? track).replace(/[^A-Za-z]/g, '').slice(0, 3).toUpperCase()
}

export function ResultsPage() {
  const year = Number(useParams().year ?? SEASONS[0])
  const navigate = useNavigate()
  const matrix = useAsync(() => api.seasonResults(year), [year])
  const record = useAsync(() => api.liveRecord(), [])
  const m = matrix.data
  const raced = m?.rounds.filter((r) => r.raced).length ?? 0

  return (
    <div className="page">
      <div className="page-title">
        <div>
          <span className="kicker"><b>{year}</b>{m ? ` · ${raced} of ${m.rounds.length} rounds raced` : ''}</span>
          <h1>Season results</h1>
        </div>
        <select className="select" value={year} aria-label="Season" onChange={(e) => navigate(`/results/${e.target.value}`)}>
          {SEASONS.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>

      {matrix.loading && <div className="skeleton" style={{ height: 200 }} />}
      {matrix.error && <ErrorBox error={matrix.error} />}
      {!matrix.loading && !matrix.error && !m && <div className="panel empty">No races stored for {year}.</div>}

      {m && (
        <>
          <Calendar m={m} />
          <div className="section-head" style={{ marginTop: 26 }}><h2>Results grid</h2><span className="aside">race · sprint in the corner</span></div>
          <div className="panel">
            <div className="table-wrap" data-lenis-prevent>
              <Matrix m={m} />
            </div>
            <div className="panel-foot">
              <div className="matrix-legend" style={{ marginTop: 0 }}>
                <span><i style={{ background: 'var(--gold)' }} />Win</span>
                <span><i style={{ background: 'var(--silver)' }} />2nd</span>
                <span><i style={{ background: 'var(--bronze)' }} />3rd</span>
                <span><i style={{ background: 'color-mix(in srgb, var(--green) 26%, var(--surface-2))' }} />Points</span>
                <span><i style={{ background: 'var(--surface-2)' }} />No points</span>
                <span><i style={{ background: 'color-mix(in srgb, var(--purple) 30%, var(--surface-2))' }} />Retired / not classified</span>
                <span>Small number: sprint finish (points only)</span>
              </div>
            </div>
          </div>
        </>
      )}

      {record.data && <PredictionLog record={record.data} season={year} />}
    </div>
  )
}

function Calendar({ m }: { m: SeasonResults }) {
  const today = localDay()
  const nextRound = m.rounds.find((r) => !r.raced && r.race_date >= today)?.round
  const podium = (round: number) =>
    m.drivers
      .map((d) => ({ d, r: d.results.find((c) => c.round === round)?.race }))
      .filter((x) => x.r?.position != null && x.r.position <= 3 && x.r.classified)
      .sort((a, b) => a.r!.position! - b.r!.position!)
  return (
    <div className="calendar" data-lenis-prevent>
      {m.rounds.map((r) => {
        const pod = podium(r.round)
        const date = new Date(`${r.race_date}T12:00:00`)
        return (
          <Link key={r.round} to={`/race/${r.race_id}`} className={`round-card ${r.round === nextRound ? 'next' : ''} ${!r.raced && r.round !== nextRound ? 'future' : ''}`}>
            <span className="rnd">R{String(r.round).padStart(2, '0')}{r.sprint ? ' · SPRINT' : ''}{r.round === nextRound ? ' · NEXT' : ''}</span>
            <b>{r.country ?? r.track_name}</b>
            <span className="date">{date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })} · {r.track_name}</span>
            <div className="podium">
              {pod.length > 0 ? pod.map(({ d }, i) => (
                <span key={d.id} title={`P${i + 1} ${d.name}`}>
                  <i className="team-bar" style={{ height: 12, background: glowColour(normaliseHex(d.team?.color_hex, '#888')) }} />
                  {driverCode(d.name)}
                </span>
              )) : <span className="dim">{r.raced ? 'No results' : r.round === nextRound ? 'Prediction live' : 'Upcoming'}</span>}
            </div>
          </Link>
        )
      })}
    </div>
  )
}

function Matrix({ m }: { m: SeasonResults }) {
  const rounds = m.rounds
  return (
    <table className="matrix">
      <thead>
        <tr>
          <th style={{ textAlign: 'left' }}>Driver</th>
          {rounds.map((r) => (
            <th key={r.round} title={`${r.track_name} · ${r.race_date}`}>
              <div>{countryCode(r.country, r.track_name)}</div>
              <div className="dim">{r.round}</div>
            </th>
          ))}
          <th style={{ textAlign: 'right' }}>Pts</th>
        </tr>
      </thead>
      <tbody>
        {m.drivers.map((d, i) => {
          const byRound = new Map(d.results.map((c) => [c.round, c]))
          return (
            <tr key={d.id}>
              <td className="who">
                <span className="dim" style={{ display: 'inline-block', width: 22 }}>{i + 1}</span>
                <span className="team-bar" style={{ height: 12, marginRight: 8, verticalAlign: -1, background: glowColour(normaliseHex(d.team?.color_hex, '#888')) }} />
                <span title={d.name}>{driverCode(d.name)}</span>
              </td>
              {rounds.map((r) => {
                const cell = byRound.get(r.round)
                const race = cellOf(cell?.race ?? null)
                const sprint = cell?.sprint && cell.sprint.points > 0 ? cell.sprint.position : null
                return (
                  <td key={r.round} className={race.cls}
                      title={cell ? `${r.track_name}: ${cell.race ? (cell.race.classified ? `P${cell.race.position}` : cell.race.status) : 'no race result'}${cell.sprint ? ` · sprint ${cell.sprint.classified ? `P${cell.sprint.position}` : cell.sprint.status}` : ''}` : undefined}>
                    {race.text}
                    {sprint != null && <sup>{sprint}</sup>}
                  </td>
                )
              })}
              <td className="pts">{Math.round(d.points)}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function PredictionLog({ record, season }: { record: LiveRecord; season: number }) {
  const calls = record.calls.filter((c) => c.season === season).sort((a, b) => b.round - a.round || a.stage.localeCompare(b.stage))
  if (!calls.length) return null
  const post = calls.filter((c) => c.stage === 'post_qualifying')
  return (
    <>
      <div className="section-head" style={{ marginTop: 26 }}>
        <h2>Prediction log</h2>
        <span className="aside">{post.length ? `${post.filter((c) => c.favourite_won).length} of ${post.length} favourites won after qualifying` : ''}</span>
      </div>
      <div className="panel">
        <div className="table-wrap">
          <table className="timing">
            <thead>
              <tr><th>Rnd</th><th>Circuit</th><th>Saved</th><th>Our favourite</th><th className="r">Chance</th><th>Winner</th><th className="r hide-sm">Winner was our</th><th>Call</th></tr>
            </thead>
            <tbody>
              {calls.map((c) => (
                <tr key={`${c.race_id}-${c.stage}`}>
                  <td className="num dim">R{c.round}</td>
                  <td>{c.track}</td>
                  <td className="small muted">{c.stage === 'post_qualifying' ? 'After qualifying' : 'Before qualifying'}</td>
                  <td><span className="code">{driverCode(c.favourite)}</span> <span className="muted small">{c.favourite}</span></td>
                  <td className="r num">{pct(c.favourite_p_win, 0)}</td>
                  <td>{c.winner ? <><span className="code">{driverCode(c.winner)}</span> <span className="muted small">{c.winner}</span></> : '–'}</td>
                  <td className="r num hide-sm">{c.winner_rank ? `#${c.winner_rank}` : '–'}</td>
                  <td className={c.favourite_won ? 'hit' : 'miss'}>{c.favourite_won ? '✓ Right' : c.winner_in_top3 ? 'Top 3' : '✗'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="panel-foot">Every prediction here was saved before its race and scored once the result was in.</div>
      </div>
    </>
  )
}
