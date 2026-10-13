import { useEffect, useState } from 'react'
import { Link, NavLink } from 'react-router-dom'

import { musicState, onMusicChange, setMusicEnabled, type MusicState } from '../fx/music'
import { ago, localDay, sessionStates, type SessionState } from '../format'
import { useLiveWeekend } from '../live'

const NAV = [
  { to: '/', label: 'Race', end: true },
  { to: '/championship', label: 'Championship' },
  { to: '/results', label: 'Results' },
  { to: '/teams', label: 'Teams' },
  { to: '/drivers', label: 'Drivers' },
  { to: '/tracks', label: 'Tracks' },
  { to: '/records', label: 'Records' },
]

export function SiteHeader() {
  return (
    <header className="site-head">
      <div className="head-row">
        <Link to="/" className="brand" aria-label="Apex21 home">
          <img src="/apex21-logo.png" alt="Apex21" />
        </Link>
        <nav className="main-nav" aria-label="Main">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}>{n.label}</NavLink>
          ))}
        </nav>
        <div className="head-tools">
          <Freshness />
          <MusicButton />
        </div>
      </div>
      <WeekendStrip />
    </header>
  )
}

/** When the pipeline last loaded data, from /health. */
function Freshness() {
  const { health } = useLiveWeekend()
  const h = health.data
  if (!h || h.last_pipeline_run_minutes_ago == null || !h.last_pipeline_run) return null
  const failed = h.last_pipeline_run.status === 'failed'
  const stale = h.last_pipeline_run_minutes_ago > 26 * 60
  return (
    <span className={`freshness ${failed ? 'failed' : stale ? 'stale' : ''}`} title="Last data update by the pipeline">
      <i />
      {failed ? `Update failed ${ago(h.last_pipeline_run_minutes_ago)}` : `Data ${ago(h.last_pipeline_run_minutes_ago)}`}
    </span>
  )
}

function MusicButton() {
  const [state, setState] = useState<MusicState>(musicState)
  useEffect(() => onMusicChange(setState), [])
  const on = state !== 'off'
  return (
    <button
      type="button"
      className={`icon-btn ${state}`}
      onClick={() => setMusicEnabled(!on)}
      aria-pressed={on}
      aria-label={on ? 'Background music on: click to mute' : 'Background music muted: click to play'}
      title={state === 'waiting' ? 'Music starts after your first click' : undefined}
    >
      <span className="eq" aria-hidden="true"><i /><i /><i /></span>
      {on ? 'Music' : 'Muted'}
    </button>
  )
}

const STATE_TEXT: Record<SessionState, string> = { done: 'Done', waiting: 'Awaiting data', today: 'Today', upcoming: '' }

/** The current race weekend under the header: the event and every session's state. */
function WeekendStrip() {
  const { prediction, sessions } = useLiveWeekend()
  const p = prediction.data
  const s = sessions.data
  if (!p) return null
  const states = s ? sessionStates(s.sessions, s.race_date, s.weekend_format, s.season, localDay()) : []
  const date = new Date(`${p.race_date}T12:00:00`)
  return (
    <div className="weekend-strip">
      <div className="strip-row">
        <Link to="/" className="strip-event">
          <span className="rnd">R{String(p.round).padStart(2, '0')}</span>
          <b>{p.track}</b>
          <span>{date.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })}</span>
        </Link>
        {s && (
          <div className="sessions" role="list" aria-label="Weekend sessions">
            {s.sessions.map((x, i) => {
              const st = states[i]
              const time = x.start_time_utc
                ? new Date(`${x.start_time_utc}Z`).toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' })
                : x.type === 'R' ? date.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' }) : '—'
              return (
                <div key={x.type} role="listitem" className={`session-chip ${st}`} title={x.name}>
                  <b>{x.type === 'R' ? 'Race' : x.type === 'S' ? 'Sprint' : x.type}</b>
                  <span>{st === 'done' ? '✓ Done' : STATE_TEXT[st] || time}</span>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
