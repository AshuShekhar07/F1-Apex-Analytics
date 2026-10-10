import { Suspense, lazy, useEffect, useMemo, useRef, useState } from 'react'

import { api } from '../api'
import { CIRCUITS } from '../circuits'
import { CircuitOutline } from '../fx/CircuitOutline'
import { CountNum } from '../fx/CountNum'
import { useAsync } from '../hooks'
import { useLiveWeekend } from '../live'
import { gsap, reducedMotion, useGSAP } from '../motion/gsap'
import { driverCode } from '../names'
import { glowColour, normaliseHex } from '../theme/color'
import { matchTrack } from '../trackMatch'

const TrackScene = lazy(() => import('../three/TrackScene'))

const ROLE_LABEL: Record<string, string> = { fastest: 'Fastest', mid: 'Medium', slowest: 'Slowest' }

/** The name fans use, where the circuit file's location is not it. */
const SHORT: Record<string, string> = {
  'it-1914': 'Mugello', 'be-1925': 'Spa', 'br-1940': 'Interlagos', 'ae-2009': 'Abu Dhabi', 'de-1927': 'Nürburgring',
  'fr-1969': 'Paul Ricard', 'us-2012': 'Austin (COTA)',
}
const shortName = (c: { id: string; location: string }) => SHORT[c.id] ?? c.location
const LIST = [...CIRCUITS].sort((a, b) => shortName(a).localeCompare(shortName(b)))

export function TracksPage() {
  const { prediction } = useLiveWeekend()
  const nextTrack = prediction.data?.track
  const nextId = useMemo(
    () => (nextTrack ? CIRCUITS.find((c) => matchTrack(c.name, c.location, [{ id: 1, name: nextTrack }]))?.id : undefined),
    [nextTrack],
  )
  const [picked, setPicked] = useState<string | null>(null)
  const id = picked ?? nextId ?? 'sg-2008'
  const circuit = CIRCUITS.find((c) => c.id === id) ?? CIRCUITS[0]
  const [view, setView] = useState<'3d' | 'map'>('3d')
  const dbTracks = useAsync(() => api.tracks(), [])
  const match = useMemo(
    () => (dbTracks.data ? matchTrack(circuit.name, circuit.location, dbTracks.data) : null),
    [circuit, dbTracks.data],
  )
  const stats = useAsync(() => (match ? api.trackStats(match.id) : Promise.resolve(null)), [match?.id])
  const list = useRef<HTMLElement>(null)
  const panel = useRef<HTMLElement>(null)

  useGSAP(
    () => {
      if (reducedMotion() || !panel.current) return
      gsap.fromTo(panel.current.querySelectorAll('.kv > div, .sector-row, .list-rows li'),
        { opacity: 0, y: 8 }, { opacity: 1, y: 0, duration: 0.4, ease: 'power2.out', stagger: 0.025 })
    },
    { scope: panel, dependencies: [id, stats.data] },
  )
  useEffect(() => {
    list.current?.querySelector('button.on')?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [id])

  const total = circuit.sectors.reduce((a, s) => a + s.lengthM, 0)
  const winners = stats.data?.historical_winners ?? []
  const most = useMemo(() => {
    const counts = new Map<string, number>()
    winners.forEach((w) => counts.set(w.driver_name, (counts.get(w.driver_name) ?? 0) + 1))
    return [...counts.entries()].sort((a, b) => b[1] - a[1])[0]
  }, [winners])

  return (
    <div className="page" style={{ paddingBottom: 30 }}>
      <div className="page-title" style={{ marginBottom: 16 }}>
        <div>
          <span className="kicker"><b>Circuits</b> · {CIRCUITS.length} tracks</span>
          <h1>Tracks</h1>
        </div>
        <div className="tabs" role="tablist" aria-label="View">
          <button role="tab" aria-selected={view === '3d'} className={view === '3d' ? 'on' : ''} onClick={() => setView('3d')}>3D</button>
          <button role="tab" aria-selected={view === 'map'} className={view === 'map' ? 'on' : ''} onClick={() => setView('map')}>Map</button>
        </div>
      </div>
      <div className="tracks-layout">
        <nav className="track-list" aria-label="Circuits" ref={list} data-lenis-prevent>
          {LIST.map((c) => (
            <button key={c.id} className={c.id === id ? 'on' : ''} onClick={() => setPicked(c.id)} title={c.name}>
              <span className="cc">{c.id.slice(0, 2).toUpperCase()}</span>
              <span className="nm">
                {shortName(c)}{c.id === nextId && <span className="badge live" style={{ height: 16, marginLeft: 6, fontSize: '0.55rem' }}>Next</span>}
                <small>{c.name}</small>
              </span>
            </button>
          ))}
        </nav>

        <div className="track-stage">
          {view === '3d' ? (
            <Suspense fallback={<div className="hint">Loading 3D…</div>}>
              <TrackScene key={circuit.id} circuit={circuit} />
            </Suspense>
          ) : (
            <div style={{ position: 'absolute', inset: '70px 24px 40px', display: 'grid', placeItems: 'center' }}>
              <div style={{ height: '100%', aspectRatio: '1' }}><CircuitOutline key={circuit.id} circuit={circuit} sectors width={8} /></div>
            </div>
          )}
          <div className="title">
            <span className="kicker">{circuit.location}</span>
            <h2>{circuit.name}</h2>
          </div>
          <div className="hint">{view === '3d' ? 'Drag to rotate · move the mouse to tilt' : 'Sector colours: purple fastest, green medium, yellow slowest'}</div>
        </div>

        <aside className="track-panel panel" ref={panel} data-lenis-prevent>
          <div className="kv">
            <div><div className="v"><CountNum value={circuit.lengthM ? (circuit.lengthM / 1000).toFixed(3) : null} /></div><div className="k">Lap length km</div></div>
            <div><div className="v">{circuit.firstGp ?? '–'}</div><div className="k">First Grand Prix</div></div>
            <div><div className="v"><CountNum value={circuit.altitudeM} /></div><div className="k">Altitude m</div></div>
            <div><div className="v"><CountNum value={stats.data?.total_races_recorded} /></div><div className="k">Races in our data</div></div>
          </div>
          <div className="panel-body">
            <div className="cap" style={{ marginBottom: 6 }}>Sectors</div>
            {circuit.sectors.map((s, i) => (
              <div className="sector-row" key={i}>
                <i style={{ background: s.hex }} />
                <span>
                  Sector {i + 1} <span className="dim">· {ROLE_LABEL[s.role] ?? s.role}</span>
                  <div className="small dim">{(s.lengthM / 1000).toFixed(2)} km · {Math.round((s.lengthM / total) * 100)}% of the lap</div>
                </span>
                <span className="num">{s.avgSpeedKmh.toFixed(0)}<span className="dim small"> km/h</span></span>
              </div>
            ))}
            {stats.data?.lap_record && (
              <div style={{ marginTop: 16 }}>
                <div className="cap">Lap record</div>
                <div className="num" style={{ fontSize: '1.2rem', fontWeight: 700, marginTop: 4 }}>{stats.data.lap_record}</div>
              </div>
            )}
            <div className="cap" style={{ margin: '18px 0 4px' }}>Winners here {most && most[1] > 1 ? `· most: ${most[0]} (${most[1]})` : ''}</div>
            {winners.length ? (
              <ul className="list-rows">
                {winners.slice(0, 8).map((w) => (
                  <li key={`${w.season_year}-${w.driver_name}`}>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span className="team-bar" style={{ height: 14, background: glowColour(normaliseHex(w.color_hex, '#888')) }} />
                      <span className="code">{driverCode(w.driver_name)}</span>
                      <span className="muted">{w.driver_name}</span>
                    </span>
                    <span className="num dim">{w.season_year}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="small dim">{dbTracks.loading || stats.loading ? 'Loading…' : 'No races at this circuit in our data (2018 onwards).'}</p>
            )}
            <p className="small dim" style={{ marginTop: 14 }}>Sector speeds: average from the circuit pipeline's lap data.</p>
          </div>
        </aside>
      </div>
    </div>
  )
}
