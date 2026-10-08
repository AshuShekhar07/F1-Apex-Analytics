import { AnimatePresence, motion } from 'motion/react'
import { Suspense, lazy, useEffect, useMemo, useRef, useState } from 'react'

import { api } from '../api'
import { CIRCUITS } from '../circuits'
import { CountNum } from '../fx/CountNum'
import { useAsync } from '../hooks'
import { Chars } from '../motion/Chars'
import { gsap, reducedMotion, useGSAP } from '../motion/gsap'
import { usePageAccent } from '../theme/accent'
import { normaliseHex } from '../theme/color'
import { matchTrack } from '../trackMatch'

const TrackScene = lazy(() => import('../three/TrackScene'))

const ROLE_LABEL: Record<string, string> = { fastest: 'Fastest', mid: 'Middle', slowest: 'Slowest' }

/** The name fans use, where the circuit file's location is not it. */
const SHORT: Record<string, string> = {
  'it-1914': 'Mugello', 'be-1925': 'Spa', 'br-1940': 'Interlagos', 'ae-2009': 'Abu Dhabi', 'de-1927': 'Nürburgring',
  'fr-1969': 'Paul Ricard', 'us-2012': 'Austin (COTA)',
}
const shortName = (c: { id: string; location: string }) => SHORT[c.id] ?? c.location
const LIST = [...CIRCUITS].sort((a, b) => shortName(a).localeCompare(shortName(b)))

export function TracksPage() {
  const [id, setId] = useState(() => (CIRCUITS.find((c) => c.id === 'sg-2008') ?? CIRCUITS[0]).id)
  const circuit = CIRCUITS.find((c) => c.id === id) ?? CIRCUITS[0]
  const dbTracks = useAsync(() => api.tracks(), [])
  const match = useMemo(
    () => (dbTracks.data ? matchTrack(circuit.name, circuit.location, dbTracks.data) : null),
    [circuit, dbTracks.data],
  )
  const stats = useAsync(() => (match ? api.trackStats(match.id) : Promise.resolve(null)), [match?.id])
  const fastest = circuit.sectors.find((s) => s.role === 'fastest')
  usePageAccent(fastest?.hex ?? null)

  const list = useRef<HTMLElement>(null)
  const panel = useRef<HTMLElement>(null)
  // the stats panel re-deals its rows for every circuit
  useGSAP(
    () => {
      if (reducedMotion() || !panel.current) return
      gsap.fromTo(panel.current.querySelectorAll('.kv > div, .sector, h3, .winner-row'),
        { opacity: 0, x: 24 },
        { opacity: 1, x: 0, duration: 0.6, ease: 'expo.out', stagger: 0.035 })
    },
    { scope: panel, dependencies: [id, stats.data] },
  )
  useEffect(() => {
    list.current?.querySelector('button.on')?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [id])

  const total = circuit.sectors.reduce((a, s) => a + s.lengthM, 0)
  const winners = stats.data?.historical_winners ?? []

  return (
    <div className="page" style={{ paddingBottom: 30 }}>
      <span className="eyebrow">Circuits · 3D</span>
      <div className="tracks-layout" style={{ marginTop: 12 }}>
        <nav className="track-list" aria-label="Circuits" ref={list} data-lenis-prevent>
          {LIST.map((c) => (
            <button key={c.id} className={c.id === id ? 'on' : ''} onClick={() => setId(c.id)} title={c.name}>
              <span className="cc">{c.id.slice(0, 2)}</span>
              <span className="nm">
                {shortName(c)}
                <small>{c.name}</small>
              </span>
            </button>
          ))}
        </nav>

        <div className="track-stage chamfer" data-cursor="Drag">
          <Suspense fallback={<div className="hint">Loading 3D…</div>}>
            <TrackScene key={circuit.id} circuit={circuit} />
          </Suspense>
          <div className="title">
            <AnimatePresence mode="wait">
              <motion.div key={circuit.id} initial={{ opacity: 0, x: -20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 20 }}>
                <span className="eyebrow">{circuit.location}</span>
                <h2><Chars text={circuit.name} at={-0.25} /></h2>
              </motion.div>
            </AnimatePresence>
          </div>
          <div className="hint">Drag to rotate · move the mouse to tilt</div>
        </div>

        <aside className="track-panel chamfer" ref={panel} data-lenis-prevent>
          <div className="kv">
            <div><div className="v"><CountNum value={circuit.lengthM ? (circuit.lengthM / 1000).toFixed(3) : null} /></div><div className="k">Length km</div></div>
            <div><div className="v">{circuit.firstGp ?? '–'}</div><div className="k">First GP</div></div>
            <div><div className="v"><CountNum value={circuit.altitudeM} /></div><div className="k">Altitude m</div></div>
            <div><div className="v"><CountNum value={stats.data?.total_races_recorded} /></div><div className="k">Races in our data</div></div>
          </div>

          <h3>Sectors</h3>
          {circuit.sectors.map((s, i) => (
            <div className="sector" key={i}>
              <i style={{ background: s.hex, boxShadow: `0 0 10px ${s.hex}` }} />
              <span>
                Sector {i + 1} <span className="muted">· {ROLE_LABEL[s.role] ?? s.role}</span>
                <div className="muted small">{(s.lengthM / 1000).toFixed(2)} km · {Math.round((s.lengthM / total) * 100)}% of the lap</div>
              </span>
              <strong><CountNum value={s.avgSpeedKmh.toFixed(0)} delay={0.1 + i * 0.08} /><span className="muted small"> km/h</span></strong>
            </div>
          ))}

          {stats.data?.lap_record && (
            <>
              <h3>Lap record</h3>
              <p style={{ margin: 0, fontWeight: 800 }}>{stats.data.lap_record}</p>
            </>
          )}

          <h3>Winners here</h3>
          {winners.length ? (
            winners.slice(0, 8).map((w) => (
              <div className="winner-row" key={`${w.season_year}-${w.driver_name}`}>
                <span><i style={{ background: normaliseHex(w.color_hex, '#888') }} />{w.driver_name}</span>
                <span className="muted">{w.season_year}</span>
              </div>
            ))
          ) : (
            <p className="muted small">
              {dbTracks.loading || stats.loading ? 'Loading…' : 'No races at this circuit in our data (2018 onwards).'}
            </p>
          )}
          <p className="muted small" style={{ marginTop: 14 }}>
            Sector speeds: average from the circuit pipeline's lap data. Colours: purple fastest, green middle, yellow slowest.
          </p>
        </aside>
      </div>
    </div>
  )
}
