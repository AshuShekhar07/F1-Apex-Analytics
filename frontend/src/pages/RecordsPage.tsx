import { useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../api'
import { ErrorBox } from '../components/Status'
import { useAsync } from '../hooks'
import { driverCode } from '../names'
import { glowColour, normaliseHex } from '../theme/color'
import type { RecordLeader, RecordRace, RecordsHistory, Streak, StreakKind } from '../types'

const KINDS: [StreakKind, string, string][] = [
  ['wins', 'Wins', 'Grands Prix won in a row'],
  ['podiums', 'Podiums', 'top-three finishes in a row'],
  ['points', 'Points', 'points finishes in a row'],
  ['finishes', 'Finishes', 'races finished (classified) in a row'],
  ['poles', 'Poles', 'fastest in qualifying, race after race'],
]

const colour = (hex: string | null | undefined) => glowColour(normaliseHex(hex, '#888888'))
const raceLabel = (r: RecordRace) => `${r.season} R${r.round} ${r.track}`

/** Records and streaks over every Grand Prix in the database. */
export function RecordsPage() {
  const q = useAsync(() => api.recordsHistory(), [])
  const [kind, setKind] = useState<StreakKind>('wins')
  const data = q.data

  if (q.loading) return <div className="page"><div className="skeleton" style={{ height: 320 }} /></div>
  if (q.error) return <div className="page"><ErrorBox error={q.error} /></div>
  if (!data || !data.races) return <div className="page"><div className="panel empty">No Grands Prix in the database yet.</div></div>

  const streaks = data.streaks[kind]
  const longestBar = Math.max(1, ...streaks.longest.map((s) => s.length), ...streaks.active.map((s) => s.length))
  const overall = data.pole_to_win_overall
  const grandChelems = data.hat_tricks.filter((h) => h.grand_chelem)
  const lead = (k: keyof RecordsHistory['leaders']) => data.leaders[k]?.[0]

  return (
    <div className="page">
      <div className="page-title">
        <div>
          <span className="kicker"><b>{data.first_season}–{data.last_season}</b> · {data.races} Grands Prix in our data</span>
          <h1>Records</h1>
        </div>
      </div>

      <div className="panel" style={{ marginBottom: 26 }}>
        <div className="kv rec-kv">
          <Tile value={lead('wins')?.count} label={`Most wins · ${lead('wins')?.driver_name ?? '–'}`} />
          <Tile value={lead('poles')?.count} label={`Most poles · ${lead('poles')?.driver_name ?? '–'}`} />
          <Tile value={data.hat_tricks.length} label={`Hat-tricks · ${grandChelems.length} Grand Chelem${grandChelems.length === 1 ? '' : 's'}`} />
          <Tile value={overall?.races ? `${Math.round((overall.pole_won / overall.races) * 100)}%` : '–'}
                label={overall ? `Pole won the race · ${overall.pole_won} of ${overall.races}` : 'Pole won the race'} />
        </div>
      </div>

      <div className="section-head"><h2>Streaks</h2><span className="aside">{KINDS.find((k) => k[0] === kind)?.[2]}</span></div>
      <div className="trace-controls">
        <div className="tabs" role="tablist" aria-label="Streak">
          {KINDS.map(([k, label]) => (
            <button key={k} role="tab" aria-selected={kind === k} className={kind === k ? 'on' : ''} onClick={() => setKind(k)}>{label}</button>
          ))}
        </div>
      </div>
      <div className="cols-2" style={{ alignItems: 'start' }}>
        <StreakList title={`Longest since ${data.first_season}`} rows={streaks.longest} max={longestBar} empty="None yet." />
        <StreakList title="Still going" aside={data.latest_race ? `after ${raceLabel(data.latest_race)}` : undefined}
                    rows={streaks.active} max={longestBar} empty="Nobody is on one right now." />
      </div>

      <div className="section-head" style={{ marginTop: 30 }}><h2>Hat-tricks &amp; Grand Chelems</h2><span className="aside">pole, win and fastest lap</span></div>
      <div className="panel">
        {data.hat_tricks.length ? (
          <div className="table-wrap">
            <table className="timing">
              <thead><tr><th>Grand Prix</th><th>Driver</th><th className="r">Led every lap</th></tr></thead>
              <tbody>
                {data.hat_tricks.map((h) => (
                  <tr key={h.race_id}>
                    <td><Link to={`/race/${h.race_id}`}>{h.season} R{h.round} {h.track}</Link></td>
                    <td><div className="driver-cell"><span className="team-bar" style={{ background: colour(h.color_hex) }} /><span className="code">{driverCode(h.driver_name)}</span><span className="name hide-sm">{h.driver_name}</span></div></td>
                    <td className="r">
                      {h.grand_chelem === true ? <span className="badge gold">Grand Chelem</span>
                        : h.grand_chelem === false ? <span className="dim small">No</span>
                        : <span className="dim small" title="No lap-by-lap order stored for this race">Unknown</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <div className="empty">No hat-tricks in the data.</div>}
        <div className="panel-foot">Hat-trick: pole, win and fastest lap in the same Grand Prix. A Grand Chelem also leads every lap.</div>
      </div>

      <div className="section-head" style={{ marginTop: 30 }}><h2>Leaders</h2><span className="aside">since {data.first_season}</span></div>
      <div className="rec-leaders">
        <LeaderList title="Wins" rows={data.leaders.wins} />
        <LeaderList title="Poles" rows={data.leaders.poles} />
        <LeaderList title="Podiums" rows={data.leaders.podiums} />
        <LeaderList title="Fastest laps" rows={data.leaders.fastest_laps} />
      </div>

      <div className="section-head" style={{ marginTop: 30 }}><h2>Pole to win</h2><span className="aside">how often qualifying P1 won the race</span></div>
      <div className="cols-2" style={{ alignItems: 'start' }}>
        <div className="panel">
          <div className="panel-head"><h3>By circuit</h3><span className="aside">{data.pole_to_win.length} circuits</span></div>
          <div className="table-wrap">
            <table className="timing">
              <thead><tr><th>Circuit</th><th className="r">Races</th><th>Pole won</th></tr></thead>
              <tbody>
                {data.pole_to_win.map((t) => {
                  const share = t.pole_won / t.races
                  return (
                    <tr key={t.track_id}>
                      <td>{t.track}</td>
                      <td className="r num">{t.races}</td>
                      <td>
                        <span className="ot-bar"><span className="track"><i style={{ width: `${share * 100}%`, background: 'var(--text-2)' }} /></span>
                          <b className="num">{Math.round(share * 100)}%</b><span className="dim small num">{t.pole_won}/{t.races}</span></span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
        <div className="panel">
          <div className="panel-head"><h3>Won from furthest back</h3><span className="aside">grid slot of the winner</span></div>
          <ul className="list-rows rec-back">
            {data.furthest_back_wins.map((b) => (
              <li key={b.race_id}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span className="team-bar" style={{ height: 14, background: colour(b.color_hex) }} />
                  <span className="code">{driverCode(b.driver_name)}</span>
                  <Link to={`/race/${b.race_id}`} className="muted">{b.season} {b.track}</Link>
                </span>
                <span className="num"><b>{b.pit_lane ? 'Pit lane' : `P${b.grid}`}</b></span>
              </li>
            ))}
          </ul>
        </div>
      </div>
      <p className="dim small" style={{ marginTop: 14 }}>{data.note}</p>
    </div>
  )
}

function Tile({ value, label }: { value: string | number | undefined; label: string }) {
  return <div><div className="v num">{value ?? '–'}</div><div className="k">{label}</div></div>
}

function StreakList({ title, aside, rows, max, empty }: { title: string; aside?: string; rows: Streak[]; max: number; empty: string }) {
  return (
    <div className="panel">
      <div className="panel-head"><h3>{title}</h3>{aside && <span className="aside">{aside}</span>}</div>
      {rows.length ? (
        <ol className="streak-list">
          {rows.map((s, i) => (
            <li key={`${s.driver_id}-${s.from.race_id}`}>
              <span className="num dim">{i + 1}</span>
              <span className="who"><span className="team-bar" style={{ background: colour(s.color_hex) }} /><b className="code">{driverCode(s.driver_name)}</b><span className="name hide-sm">{s.driver_name}</span></span>
              <span className="bar"><i style={{ width: `${(s.length / max) * 100}%`, background: colour(s.color_hex) }} /></span>
              <b className="num len">{s.length}</b>
              <span className="span dim small">{raceLabel(s.from)}{s.length > 1 ? ` → ${raceLabel(s.to)}` : ''}</span>
            </li>
          ))}
        </ol>
      ) : <div className="empty">{empty}</div>}
    </div>
  )
}

function LeaderList({ title, rows }: { title: string; rows: RecordLeader[] }) {
  const max = Math.max(1, ...rows.map((r) => r.count))
  return (
    <div className="panel">
      <div className="panel-head"><h3>{title}</h3></div>
      <ol className="streak-list compact">
        {rows.map((r, i) => (
          <li key={r.driver_id}>
            <span className="num dim">{i + 1}</span>
            <span className="who"><span className="team-bar" style={{ background: colour(r.color_hex) }} /><b className="code">{driverCode(r.driver_name)}</b></span>
            <span className="bar"><i style={{ width: `${(r.count / max) * 100}%`, background: colour(r.color_hex) }} /></span>
            <b className="num len">{r.count}</b>
          </li>
        ))}
      </ol>
    </div>
  )
}
