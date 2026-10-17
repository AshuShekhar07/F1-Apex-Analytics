import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'

import { api } from '../api'
import { ErrorBox } from '../components/Status'
import { CountNum } from '../fx/CountNum'
import { useAsync } from '../hooks'
import { lockScroll } from '../motion/SmoothScroll'
import { HeadToHead } from '../race/HeadToHead'
import { driverCode, splitName } from '../names'
import { useAccent, usePageAccent } from '../theme/accent'
import { glowColour, normaliseHex } from '../theme/color'
import type { DriverStats, RosterDriver, WetRanking as WetRankingData } from '../types'
import { LightsOut } from '../components/LightsOut'
import { Empty } from '../components/Empty'

const SEASON = 2026

export function DriversPage() {
  const roster = useAsync(() => api.roster(SEASON), [])
  const mates = useAsync(() => api.teammates(SEASON), [])
  const wet = useAsync(() => api.wetRanking(), [])
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

      {roster.loading && <LightsOut height={300} />}
      {roster.error && <ErrorBox error={roster.error} />}
      {!roster.loading && !roster.error && !roster.data && <Empty panel icon="tyre" title={`No ${SEASON} drivers in the database yet`} />}

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

          {mates.data && mates.data.pairs.length > 0 && (
            <>
              <div className="section-head" style={{ marginTop: 30 }}><h2>Teammate battles</h2><span className="aside">{SEASON} · same car, who was quicker</span></div>
              <div className="h2h-grid">
                {mates.data.pairs.filter((p) => team == null || p.team.id === team).map((p) => <HeadToHead key={`${p.team.id}-${p.drivers[0].id}-${p.drivers[1].id}`} pair={p} />)}
              </div>
              <p className="dim small" style={{ marginTop: 10 }}>{mates.data.note}.</p>
            </>
          )}

          {wet.data && (
            <WetRanking data={wet.data} current={new Set(drivers.map((d) => d.id))} colours={new Map(drivers.map((d) => [d.id, d.team.color_hex]))} />
          )}
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

/** Who gains most on their own teammate when it rains: average places ahead of the teammate
    in wet races minus the same in dry races (current drivers with enough wet races). */
function WetRanking({ data, current, colours }: { data: WetRankingData; current: Set<number>; colours: Map<number, string | null> }) {
  const rows = data.race_rankings.filter((r) => current.has(r.driver_id)).slice(0, 10)
  if (!rows.length) return null
  const top = Math.max(...rows.map((r) => Math.abs(r.specialist_score)), 0.1)
  return (
    <>
      <div className="section-head" style={{ marginTop: 30 }}><h2>Wet-weather specialists</h2><span className="aside">races since 2018 · vs own teammate</span></div>
      <div className="panel">
        <table className="timing">
          <thead><tr><th>#</th><th>Driver</th><th>Rain effect</th><th className="r hide-sm">Wet races</th><th className="r hide-sm">Dry races</th></tr></thead>
          <tbody>
            {rows.map((r, i) => {
              const c = glowColour(normaliseHex(colours.get(r.driver_id), '#888'))
              const up = r.specialist_score >= 0
              return (
                <tr key={r.driver_id}>
                  <td className="num dim">{i + 1}</td>
                  <td><div className="driver-cell"><span className="team-bar" style={{ background: c }} /><span className="code">{driverCode(r.driver_name ?? '???')}</span><span className="name">{r.driver_name}</span></div></td>
                  <td>
                    <div className="prob-cell" style={{ gridTemplateColumns: '64px 1fr' }}>
                      <span className={`num ${up ? 'hit' : 'miss'}`}>{up ? '+' : ''}{r.specialist_score.toFixed(2)}</span>
                      <span className="meter"><i style={{ width: `${(Math.abs(r.specialist_score) / top) * 100}%`, background: up ? 'var(--up)' : 'var(--down)' }} /></span>
                    </div>
                  </td>
                  <td className="r num hide-sm">{r.wet_sessions_counted}</td>
                  <td className="r num hide-sm">{r.dry_sessions_counted}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
        <div className="panel-foot">Rain effect = places gained on the teammate per wet race, minus the same in dry races. Positive: relatively stronger in the wet.</div>
      </div>
    </>
  )
}
