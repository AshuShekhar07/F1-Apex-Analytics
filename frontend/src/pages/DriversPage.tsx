import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'

import { api } from '../api'
import { ErrorBox } from '../components/Status'
import { CountNum } from '../fx/CountNum'
import { useAsync } from '../hooks'
import { lockScroll } from '../motion/SmoothScroll'
import { splitName } from '../names'
import { useAccent, usePageAccent } from '../theme/accent'
import { glowColour, normaliseHex } from '../theme/color'
import type { DriverStats, RosterDriver } from '../types'

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
  usePageAccent(open?.team.color_hex ?? teams.find((t) => t.id === team)?.color_hex ?? null)

  const shown = team == null ? drivers : drivers.filter((d) => d.team.id === team)
  return (
    <div className="page">
      <div className="page-title">
        <div>
          <span className="kicker"><b>{SEASON}</b> · {drivers.length || '–'} race drivers</span>
          <h1>Drivers</h1>
        </div>
      </div>

      {roster.loading && <div className="skeleton" style={{ height: 300 }} />}
      {roster.error && <ErrorBox error={roster.error} />}
      {!roster.loading && !roster.error && !roster.data && <div className="panel empty">No {SEASON} drivers in the database yet.</div>}

      {roster.data && (
        <>
          <div className="filter-row">
            <button className={`chip ${team == null ? 'on' : ''}`} onClick={() => setTeam(null)}>All teams</button>
            {teams.map((t) => (
              <button key={t.id} className={`chip ${team === t.id ? 'on' : ''}`} onClick={() => setTeam(team === t.id ? null : t.id)}
                      onMouseEnter={() => preview(t.color_hex)} onMouseLeave={() => preview(null)}>
                <i style={{ background: glowColour(normaliseHex(t.color_hex, '#888')) }} />{t.name}
              </button>
            ))}
          </div>

          <motion.div className="driver-grid" layout>
            <AnimatePresence>
              {shown.map((d) => {
                const [first, last] = splitName(d.name)
                const rank = drivers.indexOf(d) + 1
                return (
                  <motion.div key={d.id} layout initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
                              transition={{ duration: 0.3, delay: Math.min(rank, 12) * 0.02 }}>
                    <button className="driver-card" style={{ ['--c' as string]: glowColour(normaliseHex(d.team.color_hex, '#666666')) }}
                            onClick={() => setOpen(d)} aria-label={`${d.name}, ${d.team.name}`}>
                      <span className="rank">P{rank}</span>
                      <span className="num">{d.number ?? ''}</span>
                      {d.photo_url && <img src={d.photo_url} alt="" loading="lazy" />}
                      <span className="info">
                        <span className="first">{first}</span>
                        <span className="last" style={{ display: 'block' }}>{last}</span>
                        <span className="row"><span>{d.team.name}</span><span><b>{d.season.points}</b> pts</span></span>
                      </span>
                    </button>
                  </motion.div>
                )
              })}
            </AnimatePresence>
          </motion.div>
          <p className="dim small" style={{ marginTop: 18 }}>Ordered by {SEASON} points. Photos: Wikipedia. {roster.data.note}.</p>
        </>
      )}

      {createPortal(
        <AnimatePresence>{open && <DriverDrawer driver={open} onClose={() => setOpen(null)} />}</AnimatePresence>,
        document.body,
      )}
    </div>
  )
}

function StatGrid({ stats }: { stats: DriverStats }) {
  const cells: [string, string | number][] = [
    ['Points', stats.points], ['Wins', stats.wins], ['Podiums', stats.podiums],
    ['Poles', stats.poles], ['Races', stats.races], ['DNFs', stats.dnfs],
    ['Best finish', stats.best_finish ? `P${stats.best_finish}` : '–'],
    ['Avg finish', stats.average_finish ?? '–'], ['Avg quali', stats.average_qualifying ?? '–'],
  ]
  return (
    <div className="stat-grid">
      {cells.map(([k, v], i) => (
        <div key={k}>
          <div className="v"><CountNum value={v} delay={0.15 + i * 0.03} /></div>
          <div className="k">{k}</div>
        </div>
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
        style={{ ['--c' as string]: glowColour(normaliseHex(driver.team.color_hex, '#666666')) }}
        initial={{ x: '100%' }}
        animate={{ x: 0 }}
        exit={{ x: '100%' }}
        transition={{ type: 'tween', duration: 0.35, ease: [0.2, 0.8, 0.2, 1] }}
      >
        <button className="btn close" onClick={onClose}>Close ✕</button>
        <div className="hero">
          <span className="num">{driver.number ?? ''}</span>
          {driver.photo_url && <img src={driver.photo_url} alt="" />}
        </div>
        <div className="body">
          <div className="muted">{first}</div>
          <h2>{last}</h2>
          <div className="muted small">
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
              <ul className="list-rows"><li><span>{p.last_gp_win.track_name}</span><span className="num dim">{p.last_gp_win.season_year} · R{p.last_gp_win.round_number}</span></li></ul>
            </>
          )}
          {!!p?.team_history.length && (
            <>
              <h3>Teams</h3>
              <ul className="list-rows">
                {p.team_history.map((t) => (
                  <li key={`${t.team_name}-${t.start_year}`}>
                    <span>{t.team_name}</span>
                    <span className="num dim">{t.start_year === t.end_year ? t.start_year : `${t.start_year}–${t.end_year}`}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
          {!!p?.track_records_held.length && (
            <>
              <h3>Fastest race laps held</h3>
              <ul className="list-rows">
                {p.track_records_held.map((r) => (
                  <li key={r.track_name}>
                    <span>{r.track_name}</span>
                    <span className="num dim">{Number(r.lap_time).toFixed(3)}s · {r.season_year}</span>
                  </li>
                ))}
              </ul>
              <p className="dim small">{p.track_records_note}</p>
            </>
          )}
        </div>
      </motion.aside>
    </>
  )
}
