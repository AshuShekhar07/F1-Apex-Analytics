import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'

import { api } from '../api'
import { ErrorBox, Loading } from '../components/Status'
import { CountNum } from '../fx/CountNum'
import { useAsync } from '../hooks'
import { Chars } from '../motion/Chars'
import { useTilt } from '../motion/reveal'
import { lockScroll } from '../motion/SmoothScroll'
import { splitName } from '../names'
import type { DriverStats, RosterDriver } from '../types'
import { useAccent, usePageAccent } from '../theme/accent'
import { normaliseHex } from '../theme/color'

const SEASON = 2026

export function DriversPage() {
  const roster = useAsync(() => api.roster(SEASON), [])
  const [team, setTeam] = useState<number | null>(null)
  const [open, setOpen] = useState<RosterDriver | null>(null)
  const { preview } = useAccent()
  const drivers = roster.data?.drivers ?? []
  const teams = useMemo(() => {
    const seen = new Map<number, RosterDriver['team']>()
    drivers.forEach((d) => seen.set(d.team.id, d.team))
    return [...seen.values()]
  }, [drivers])
  const teamColour = teams.find((t) => t.id === team)?.color_hex ?? null
  usePageAccent(open?.team.color_hex ?? teamColour)

  if (roster.loading) return <div className="page"><Loading what="the 2026 grid" /></div>
  if (roster.error) return <div className="page"><ErrorBox error={roster.error} /></div>
  if (!roster.data) return <div className="page"><p className="lead">No 2026 drivers in the database yet.</p></div>

  const shown = team == null ? drivers : drivers.filter((d) => d.team.id === team)
  return (
    <div className="page">
      <span className="eyebrow">The grid · {SEASON}</span>
      <h1 className="display"><Chars text="Drivers" /> <em><Chars text={String(SEASON)} at={0.2} /></em></h1>
      <p className="lead">Every race driver this season, ordered by points. Hover for team colours, click for the full file.</p>

      <div className="team-filter">
        <button className={`chip ${team == null ? 'on' : ''}`} style={{ ['--c' as string]: '#e10600' }} onClick={() => setTeam(null)}>
          All teams
        </button>
        {teams.map((t) => (
          <button
            key={t.id}
            className={`chip ${team === t.id ? 'on' : ''}`}
            style={{ ['--c' as string]: normaliseHex(t.color_hex, '#888888') }}
            onClick={() => setTeam(team === t.id ? null : t.id)}
            onMouseEnter={() => preview(t.color_hex)}
            onMouseLeave={() => preview(null)}
          >
            {t.name}
          </button>
        ))}
      </div>

      <motion.div className="driver-grid" layout>
        <AnimatePresence>
          {shown.map((d, i) => {
            const [first, last] = splitName(d.name)
            return (
              <motion.div
                key={d.id}
                layout
                initial={{ opacity: 0, y: 24 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.9 }}
                transition={{ delay: Math.min(i, 12) * 0.03 }}
                whileHover={{ y: -8, rotate: -0.6 }}
              >
                <DriverCard driver={d} first={first} last={last} onOpen={() => setOpen(d)} onHover={preview} />
              </motion.div>
            )
          })}
        </AnimatePresence>
      </motion.div>
      <p className="muted small" style={{ marginTop: 18 }}>Photos: Wikipedia. {roster.data.note}.</p>

      {/* portalled: the page-transition wrapper is transformed, which would trap the drawer under the top bar */}
      {createPortal(
        <AnimatePresence>{open && <DriverDrawer driver={open} onClose={() => setOpen(null)} />}</AnimatePresence>,
        document.body,
      )}
    </div>
  )
}

/** One card: tilts toward the pointer in 3D and re-types its text on hover. */
function DriverCard({ driver: d, first, last, onOpen, onHover }: {
  driver: RosterDriver
  first: string
  last: string
  onOpen: () => void
  onHover: (hex: string | null) => void
}) {
  const tilt = useTilt<HTMLButtonElement>(9)
  return (
    <button
      ref={tilt}
      className="driver-card"
      style={{ ['--c' as string]: normaliseHex(d.team.color_hex, '#666666') }}
      onClick={onOpen}
      onMouseEnter={() => onHover(d.team.color_hex)}
      onMouseLeave={() => onHover(null)}
      aria-label={`${d.name}, ${d.team.name}`}
      data-cursor="Open"
      data-scramble
    >
      <span className="stripe" />
      <span className="num">{d.number ?? ''}</span>
      {d.photo_url ? <img src={d.photo_url} alt="" loading="lazy" /> : <span className="no-photo">{last[0]}</span>}
      <span className="info">
        <span className="first">{first}</span>
        <span className="last">{last}</span>
        <span className="team">{d.team.name}</span>
      </span>
      <span className="pts">{d.season.points}<small>PTS</small></span>
    </button>
  )
}

function StatGrid({ stats }: { stats: DriverStats }) {
  const cells: [string, string | number][] = [
    ['Points', stats.points], ['Wins', stats.wins], ['Podiums', stats.podiums],
    ['Poles', stats.poles], ['Races', stats.races], ['DNFs', stats.dnfs],
    ['Best finish', stats.best_finish ? `P${stats.best_finish}` : '–'],
    ['Avg finish', stats.average_finish ?? '–'], ['Avg grid (quali)', stats.average_qualifying ?? '–'],
  ]
  return (
    <div className="stat-cards">
      {cells.map(([k, v], i) => (
        <motion.div key={k} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 + i * 0.03 }}>
          <div className="v"><CountNum value={v} delay={0.2 + i * 0.04} /></div>
          <div className="k">{k}</div>
        </motion.div>
      ))}
    </div>
  )
}

function DriverDrawer({ driver, onClose }: { driver: RosterDriver; onClose: () => void }) {
  const profile = useAsync(() => api.driverProfile(driver.id), [driver.id])
  const [first, last] = splitName(driver.name)
  useEffect(() => {
    lockScroll(true)
    return () => lockScroll(false)
  }, [])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  const p = profile.data
  return (
    <>
      <motion.div className="drawer-scrim" onClick={onClose} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} />
      <motion.aside
        data-lenis-prevent
        className="drawer"
        role="dialog"
        aria-label={driver.name}
        style={{ ['--c' as string]: normaliseHex(driver.team.color_hex, '#666666') }}
        initial={{ x: '100%' }}
        animate={{ x: 0 }}
        exit={{ x: '100%' }}
        transition={{ type: 'spring', stiffness: 260, damping: 32 }}
      >
        <button className="btn-sharp small ghost close" onClick={onClose}>Close ✕</button>
        <div className="hero-photo">
          <span className="big-num">{driver.number ?? ''}</span>
          {driver.photo_url && (
            <motion.img src={driver.photo_url} alt="" initial={{ opacity: 0, x: 40 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.15 }} />
          )}
        </div>
        <div className="body">
          <div className="sub">{first}</div>
          <h2>{last}</h2>
          <div className="sub">
            {driver.team.name}{driver.nationality ? ` · ${driver.nationality}` : ''}
            {driver.world_championships > 0 ? ` · ${driver.world_championships}× world champion` : ''}
          </div>

          <h3>{SEASON} season</h3>
          <StatGrid stats={driver.season} />
          <h3>Career since 2018 (in our data)</h3>
          <StatGrid stats={driver.career_since_2018} />

          {p?.last_gp_win && (
            <>
              <h3>Last Grand Prix win</h3>
              <p>{p.last_gp_win.track_name} · {p.last_gp_win.season_year}</p>
            </>
          )}
          {!!p?.team_history.length && (
            <>
              <h3>Teams</h3>
              <ul className="timeline">
                {p.team_history.map((t) => (
                  <li key={`${t.team_name}-${t.start_year}`}>
                    <span>{t.team_name}</span>
                    <span className="muted">{t.start_year === t.end_year ? t.start_year : `${t.start_year}–${t.end_year}`}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
          {!!p?.track_records_held.length && (
            <>
              <h3>Fastest race laps held</h3>
              <ul className="timeline">
                {p.track_records_held.map((r) => (
                  <li key={r.track_name}>
                    <span>{r.track_name}</span>
                    <span className="muted">{Number(r.lap_time).toFixed(3)}s · {r.season_year}</span>
                  </li>
                ))}
              </ul>
              <p className="muted small">{p.track_records_note}</p>
            </>
          )}
        </div>
      </motion.aside>
    </>
  )
}
