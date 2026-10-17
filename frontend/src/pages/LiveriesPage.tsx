import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { api } from '../api'
import { driverLine, liveryColours, metricLines, overallBand } from '../cars'
import {
  compareCars, curve, driverFactor, fairRanking, headToHead, pickPair, sameCircuits, shortCar,
  type Measure, type Mode, type Side, type Verdict,
} from '../compareCars'
import { useWidth } from '../charts/useWidth'
import { gsap, reducedMotion } from '../motion/gsap'
import { ErrorBox } from '../components/Status'
import { useAsync } from '../hooks'
import { LiveryDrawing } from '../race/LiveryDrawing'
import { glowColour, normaliseHex } from '../theme/color'
import type { CarDetail, CarRow, LiveryPhoto } from '../types'

/** Credits file written by fetch_livery_photos_v1.py; missing (no photos yet) is fine. */
let photosOnce: Promise<Record<string, LiveryPhoto>> | null = null
function loadPhotos(): Promise<Record<string, LiveryPhoto>> {
  photosOnce ??= fetch('/livery-photos/photos.json')
    .then((r) => (r.ok ? r.json() : { photos: {} }))
    .then((d: { photos?: Record<string, LiveryPhoto> }) => d.photos ?? {})
    .catch(() => ({}))
  return photosOnce
}

const RATINGS: { key: keyof CarRow['ratings']; label: string }[] = [
  { key: 'quali', label: 'Qualifying pace' },
  { key: 'race', label: 'Race pace' },
  { key: 'speed', label: 'Top speed' },
  { key: 'reliability', label: 'Reliability' },
]

export function LiveriesPage() {
  const [params, setParams] = useSearchParams()
  const cars = useAsync(() => api.cars(), [])
  const photos = useAsync(loadPhotos, [])
  const data = cars.data
  const view = params.get('view') === 'ranking' ? 'ranking' : params.get('view') === 'compare' ? 'compare' : 'season'
  const season = Number(params.get('season')) || data?.seasons[0] || 0
  const shown = useMemo(() => (data?.cars ?? []).filter((c) => c.season === season)
    .sort((a, b) => a.results.position - b.results.position), [data, season])
  const best = useMemo(() => fairRanking(data?.cars ?? []), [data])
  const hash = typeof window !== 'undefined' ? window.location.hash.slice(1) : ''

  useEffect(() => {
    if (view === 'season' && hash && shown.length) document.getElementById(hash)?.scrollIntoView({ block: 'start' })
  }, [view, hash, shown.length])

  return (
    <div className="page">
      <div className="page-title">
        <div>
          <span className="kicker"><b>2018–{data?.seasons[0] ?? ''}</b>{data ? ` · ${data.count} cars` : ''}</span>
          <h1>Cars &amp; liveries</h1>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <div className="tabs" role="tablist">
            <button role="tab" aria-selected={view === 'season'} className={view === 'season' ? 'on' : ''}
                    onClick={() => setParams({ season: String(season) })}>Season</button>
            <button role="tab" aria-selected={view === 'ranking'} className={view === 'ranking' ? 'on' : ''}
                    onClick={() => setParams({ view: 'ranking' })}>Most dominant</button>
            <button role="tab" aria-selected={view === 'compare'} className={view === 'compare' ? 'on' : ''}
                    onClick={() => setParams({ view: 'compare' })}>Compare</button>
          </div>
          {view === 'season' && data && (
            <select className="select" aria-label="Season" value={season} onChange={(e) => setParams({ season: e.target.value })}>
              {data.seasons.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          )}
        </div>
      </div>
      <p className="muted" style={{ margin: '-6px 0 18px', maxWidth: 860 }}>
        Every car since 2018, measured from the timing data. Rules, tyres and fuel changed from era to era, so cars are never
        compared on raw lap times: each one is measured against the cars it actually raced, in its own season.
      </p>

      {cars.error && <ErrorBox error={cars.error} />}
      {cars.loading && <div className="skeleton" style={{ height: 360 }} />}

      {data && view === 'season' && (
        shown.length
          ? <div className="car-grid">{shown.map((c) => <CarCard key={c.key} c={c} photo={photos.data?.[c.key]}
                                                               onCompare={() => setParams({ view: 'compare', a: c.key })} />)}</div>
          : <div className="panel empty">No cars with results in {season}.</div>
      )}
      {data && view === 'ranking' && (
        <Ranking cars={best} onPick={(c) => setParams({ season: String(c.season) }, { replace: false })} />
      )}
      {data && view === 'compare' && (
        <CompareView cars={data.cars} ranked={best} photos={photos.data ?? {}} a={params.get('a')} b={params.get('b')}
                     onPick={(a, b) => setParams({ view: 'compare', a, b })} />
      )}

      {data && (
        <div className="panel panel-pad" style={{ marginTop: 18 }}>
          <p className="small muted" style={{ margin: 0 }}>
            <b style={{ color: 'var(--text)' }}>How cars are measured.</b> "In its era" and everything on the Compare and Most dominant
            views is against the car's own season: the dominance score is how far it stood out from the field it raced, in units of how
            spread out that field was; "an average driver would win" comes from the race model, which separates car and driver. The
            0–100 bars on each card: {data.method.quali} {data.method.race} {data.method.speed} {data.method.reliability} {data.method.ratings}{' '}
            {data.method.character} Photos: freely licensed pictures from Wikimedia Commons, credited under each; where there is none
            yet, a drawing in the livery's colours.
          </p>
        </div>
      )}
    </div>
  )
}

function Meter({ value, colour }: { value: number | null; colour: string }) {
  return (
    <span className="rating-meter" aria-hidden="true">
      <i style={{ width: `${value ?? 0}%`, background: colour }} />
    </span>
  )
}

function CarMedia({ c, photo }: { c: CarRow; photo?: LiveryPhoto }) {
  const name = c.car ?? c.team_name ?? 'Unknown car'
  return (
    <div className="car-media">
      {photo
        ? <img src={photo.src} alt={`${name}, ${c.season}`} loading="lazy" referrerPolicy="no-referrer" />
        : <LiveryDrawing colours={liveryColours(c)} label={name} />}
      <span className="car-overall" title="Overall rating, against every car of 2018 onwards">
        <b>{c.ratings.overall ?? '–'}</b><small>{overallBand(c.ratings.overall)}</small>
      </span>
    </div>
  )
}

function CarCard({ c, photo, onCompare }: { c: CarRow; photo?: LiveryPhoto; onCompare: () => void }) {
  const colour = glowColour(normaliseHex(c.color_hex, '#888888'))
  const r = c.ratings
  const name = c.car ?? c.team_name ?? 'Unknown car'
  const res = c.results
  return (
    <article className="panel car-card" id={c.key}>
      <CarMedia c={c} photo={photo} />
      <div className="car-head">
        <span className="team-bar" style={{ background: colour, height: 34 }} />
        <div className="car-title">
          <h3>{name}</h3>
          <span className="dim small">{c.team_name}{c.pu ? ` · ${c.pu} power` : ''}</span>
        </div>
        <span className={`pos-box ${res.position <= 3 ? `p${res.position}` : ''}`} title="Constructors' championship position">P{res.position}</span>
      </div>
      <div className="car-ratings">
        {RATINGS.map(({ key, label }) => (
          <div key={key} className="rating-row">
            <span className="small">{label}</span>
            <Meter value={r[key]} colour={colour} />
            <span className="num">{r[key] ?? '–'}</span>
          </div>
        ))}
      </div>
      <div className="car-facts small">
        {c.fair?.dominance.car_only && (
          <p className="era-line">
            <span className="dim">In its era:</span>{' '}
            <b className="num">{c.fair.dominance.car_only.z >= 0 ? '+' : '−'}{Math.abs(c.fair.dominance.car_only.z).toFixed(2)}</b> dominance
            {c.fair.pace_rank ? <> · {c.fair.pace_rank[0] === 1 ? 'fastest' : `${ordinal(c.fair.pace_rank[0])}-fastest`} of {c.fair.pace_rank[1]}</> : null}
            {c.fair.model ? <> · an average driver would win <b className="num">{Math.round(c.fair.model.win_share * 100)}%</b></> : null}
          </p>
        )}
        {c.character && <p><span className={`badge char-${c.character.kind}`}>{c.character.label}</span> <span className="muted">{c.character.text}</span></p>}
        <p className="num">
          {Math.round(res.points)} pts · {res.wins} win{res.wins === 1 ? '' : 's'} · {res.poles} pole{res.poles === 1 ? '' : 's'} · {res.podiums} podium{res.podiums === 1 ? '' : 's'}
          {res.one_twos ? ` · ${res.one_twos} one-two${res.one_twos === 1 ? '' : 's'}` : ''}
          <span className="dim"> · {res.races} of {res.season_races} races</span>
        </p>
        {c.drivers.length > 0 && <p><span className="dim">Drivers:</span> {driverLine(c)}</p>}
        {c.livery && <p><span className="dim">Livery:</span> {c.livery}</p>}
        {c.notes.map((n) => <p key={n} className="muted">{n}</p>)}
      </div>
      <details className="car-numbers">
        <summary>The numbers behind the ratings</summary>
        <ul>{metricLines(c).map((l) => <li key={l}>{l}</li>)}</ul>
        {!metricLines(c).length && <p className="dim">Not enough timing data for this car yet.</p>}
      </details>
      <div className="car-actions">
        <button type="button" className="btn" onClick={onCompare}>Compare with another car</button>
      </div>
      {photo && (
        <p className="car-credit">
          Photo: {photo.author} · {photo.license_url ? <a href={photo.license_url} target="_blank" rel="noreferrer">{photo.license}</a> : photo.license}
          {photo.page && <> · <a href={photo.page} target="_blank" rel="noreferrer">Wikimedia Commons</a></>}
        </p>
      )}
    </article>
  )
}

const ordinal = (n: number) => `${n}${n % 100 >= 11 && n % 100 <= 13 ? 'th' : ['th', 'st', 'nd', 'rd'][n % 10] ?? 'th'}`
const signed = (v: number, d = 2) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(d)}`
const colourOf = (c: CarRow) => glowColour(normaliseHex(c.color_hex, '#888888'))

function Ranking({ cars, onPick }: { cars: CarRow[]; onPick: (c: CarRow) => void }) {
  return (
    <div className="panel">
      <div className="panel-head"><h3>The most dominant cars, each against its own season</h3><span className="aside">dominance score · car only</span></div>
      <div className="table-wrap">
        <table className="timing">
          <thead>
            <tr>
              <th>#</th><th>Car</th><th className="r">Dominance</th><th className="r hide-sm">Lap margin</th>
              <th className="r hide-sm">Average driver wins</th><th className="r hide-sm">Max points</th><th className="hide-sm">Era</th><th className="r">Result</th>
            </tr>
          </thead>
          <tbody>
            {cars.map((c, i) => {
              const f = c.fair
              return (
                <tr key={c.key} className="clickable" onClick={() => onPick(c)} title="Open this season">
                  <td className="pos-cell"><span className={`pos-box ${i < 3 ? `p${i + 1}` : ''}`}>{i + 1}</span></td>
                  <td>
                    <div className="driver-cell">
                      <span className="team-bar" style={{ background: colourOf(c) }} />
                      <span className="who"><span className="name">{c.car ?? c.team_name}</span><span className="team">{c.season} · {c.team_name}</span></span>
                    </div>
                  </td>
                  <td className="r num" style={{ fontWeight: 700 }}>{f?.dominance.car_only ? signed(f.dominance.car_only.z) : '–'}</td>
                  <td className="r num hide-sm">{f?.margin.quali_pct != null ? `${signed(f.margin.quali_pct)}%` : '–'}</td>
                  <td className="r num hide-sm">{f?.model ? `${Math.round(f.model.win_share * 100)}%` : '–'}</td>
                  <td className="r num hide-sm">{f?.achievements.points_share_of_max != null ? `${Math.round(f.achievements.points_share_of_max * 100)}%` : '–'}</td>
                  <td className="hide-sm small dim">{f?.era?.label ?? '–'}</td>
                  <td className="r num">P{c.results.position} · {c.results.wins}W</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="small muted panel-pad" style={{ margin: 0 }}>
        Dominance: how far the car stood out from the cars it raced, race by race, in units of how spread out that season's field was
        (both cars averaged, so one star driver weighs half). Lap margin: % of a qualifying lap clear of the next car (+) or behind
        the quickest (−). Average driver wins: with the same average driver in every car of its season, the share of races it
        would win (race model). Max points: the share of the most points a team could score that season.
      </p>
    </div>
  )
}

// --- compare two cars ---------------------------------------------------------------------------

function CompareView({ cars, ranked, photos, a, b, onPick }: {
  cars: CarRow[]; ranked: CarRow[]; photos: Record<string, LiveryPhoto>; a: string | null; b: string | null
  onPick: (a: string, b: string) => void
}) {
  const [mode, setMode] = useState<Mode>('car')
  const [x, y] = pickPair(cars, ranked, a, b)
  const sameSeason = !!x && !!y && x.season === y.season
  const matrix = useAsync(() => (sameSeason ? api.seasonResults(x!.season) : Promise.resolve(null)), [sameSeason ? x!.season : 0])
  const dx = useAsync<CarDetail | null>(() => (x ? api.car(x.key) : Promise.resolve(null)), [x?.key])
  const dy = useAsync<CarDetail | null>(() => (y ? api.car(y.key) : Promise.resolve(null)), [y?.key])
  const seasons = useMemo(() => [...new Set(cars.map((c) => c.season))].sort((p, q) => q - p), [cars])
  const root = useRef<HTMLDivElement>(null)
  const pair = `${x?.key}|${y?.key}`

  // the two cars roll onto the grid, the bars race to their values, the verdicts drop in
  useLayoutEffect(() => {
    const el = root.current
    if (!el || reducedMotion()) return
    const ctx = gsap.context(() => {
      gsap.from('.compare-head', { x: (i: number) => (i === 0 ? -60 : 60), opacity: 0, duration: 0.7, ease: 'power3.out' })
      gsap.from('.verdict-card', { y: 18, opacity: 0, duration: 0.55, stagger: 0.1, delay: 0.25 })
      gsap.from('.mirror-bar i', { scaleX: 0, duration: 0.9, stagger: 0.015, delay: 0.35, ease: 'power3.out' })
    }, el)
    return () => ctx.revert()
  }, [pair])

  if (!x || !y) return <div className="panel empty">Not enough cars with results to compare.</div>
  const cmp = compareCars(x, y, mode)
  const h2h = sameSeason && matrix.data ? headToHead(matrix.data, x.team_id, y.team_id) : null
  const capW = cmp.capability.verdict.winner, achW = cmp.achievement.verdict.winner
  const side = (c: CarRow): Side => (c === x ? 'a' : 'b')
  const options = (other: string) => seasons.map((s) => (
    <optgroup key={s} label={String(s)}>
      {cars.filter((c) => c.season === s).sort((p, q) => p.results.position - q.results.position).map((c) => (
        <option key={c.key} value={c.key} disabled={c.key === other}>{c.season} · {shortCar(c)} · P{c.results.position}</option>
      ))}
    </optgroup>
  ))

  return (
    <div ref={root}>
      <div className="compare-pick">
        <select className="select" aria-label="First car" value={x.key} onChange={(e) => onPick(e.target.value, y.key)}>{options(y.key)}</select>
        <button type="button" className="btn" onClick={() => onPick(y.key, x.key)} aria-label="Swap the two cars" title="Swap">⇄</button>
        <select className="select" aria-label="Second car" value={y.key} onChange={(e) => onPick(x.key, e.target.value)}>{options(x.key)}</select>
      </div>

      <div className={`era-banner ${cmp.sameSeason ? 'same' : cmp.sameEra ? 'same-era' : 'cross'}`}>
        <span className="era-chips">
          {[x, y].map((c) => <span key={c.key} className="era-chip"><b>{c.season}</b> {c.fair?.era?.label ?? ''}</span>)}
        </span>
        <span>{cmp.eraNote}</span>
      </div>

      <div className="compare-heads">
        {[x, y].map((c) => (
          <div key={c.key} className="panel compare-head" style={{ ['--car' as string]: colourOf(c) }}>
            <CarMedia c={c} photo={photos[c.key]} />
            <div className="car-head">
              <span className="team-bar" style={{ background: colourOf(c), height: 34 }} />
              <div className="car-title" title={c.car ?? undefined}>
                <h3>{shortCar(c)}</h3>
                <span className="dim small">{c.season}{c.pu ? ` · ${c.pu} power` : ''} · P{c.results.position}</span>
              </div>
            </div>
            <div className="compare-badges">
              {capW === side(c) && <span className="compare-badge" style={{ background: colourOf(c) }}>More dominant</span>}
              {achW === side(c) && <span className="compare-badge alt">Stronger record</span>}
            </div>
          </div>
        ))}
      </div>

      <div className="verdict-pair">
        <VerdictCard title="Capability" sub="how dominant in its own era" v={cmp.capability.verdict} x={x} y={y} />
        <VerdictCard title="Achievement" sub={`what the team made of it · ${cmp.achievement.score.a}–${cmp.achievement.score.b}`}
                     v={cmp.achievement.verdict} x={x} y={y} />
      </div>

      <div className="grid-main">
        <div className="panel">
          <div className="panel-head">
            <h3>Capability</h3>
            <div className="tabs small-tabs" role="tablist" aria-label="Whose pace">
              <button role="tab" aria-selected={mode === 'car'} className={mode === 'car' ? 'on' : ''} onClick={() => setMode('car')}
                      title="Both cars averaged: a star driver weighs half">Car only</button>
              <button role="tab" aria-selected={mode === 'drivers'} className={mode === 'drivers' ? 'on' : ''} onClick={() => setMode('drivers')}
                      title="The team's quicker car: the better driver's pace counts in full">Car + drivers</button>
            </div>
          </div>
          <MirrorTable rows={cmp.capability.rows} x={x} y={y} />
        </div>
        <div className="panel">
          <div className="panel-head"><h3>What they achieved</h3><span className="aside">normalised for the points systems</span></div>
          <MirrorTable rows={[...cmp.achievement.rows, ...cmp.achievement.info]} x={x} y={y} counted={cmp.achievement.rows.length} />
        </div>
      </div>

      <div className="grid-main">
        <div className="panel">
          <div className="panel-head"><h3>Fingerprint</h3><span className="aside">each against its own season's field</span></div>
          <MirrorTable rows={cmp.fingerprint} x={x} y={y} />
        </div>
        <div className="panel">
          <div className="panel-head"><h3>The drivers</h3><span className="aside">how much they shaped the result</span></div>
          <div className="driver-factor">
            {[x, y].map((c) => (
              <div key={c.key} className="df-col">
                <h4 style={{ color: colourOf(c) }}>{shortCar(c)} <span className="dim">{c.season}</span></h4>
                <p className="small muted">{driverFactor(c)}</p>
                <ul>
                  {(c.fair?.drivers ?? []).filter((d) => d.races > 0).sort((p, q) => q.points - p.points).map((d) => (
                    <li key={d.id}>
                      <b>{d.name}</b> <span className="num">{Math.round(d.points)} pts</span>
                      <span className="small dim">
                        {d.quali_h2h ? ` · qualifying ${d.quali_h2h[0]}–${d.quali_h2h[1]}` : ''}
                        {d.race_h2h && d.race_h2h[0] + d.race_h2h[1] ? ` · races ${d.race_h2h[0]}–${d.race_h2h[1]}` : ''}
                        {d.skill_rank ? ` · ${ordinal(d.skill_rank[0])} of ${d.skill_rank[1]} drivers (model)` : ''}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
          <p className="small dim panel-pad" style={{ margin: 0 }}>
            Head-to-heads are against the teammate. The driver rank is the race model's skill estimate among that season's drivers, with
            the car taken out.
          </p>
        </div>
      </div>

      <div className="panel" style={{ marginTop: 18 }}>
        <div className="panel-head"><h3>Through the season</h3><span className="aside">dominance race by race · 0 = the middle of its field</span></div>
        {(dx.loading || dy.loading) && <div className="skeleton" style={{ height: 220, margin: 14 }} />}
        {dx.data && dy.data && <DevelopmentChart a={dx.data} b={dy.data} />}
      </div>

      {dx.data && dy.data && (
        <SameCircuits x={x} y={y} a={dx.data} b={dy.data} />
      )}

      {sameSeason && (
        <div className="panel" style={{ marginTop: 18 }}>
          <div className="panel-head"><h3>Head to head in {x.season}</h3><span className="aside">best car of each team, every race</span></div>
          <div className="panel-body">
            {matrix.loading && <div className="skeleton" style={{ height: 40 }} />}
            {h2h && (h2h.races ? (
              <>
                <div className="h2h-bar" role="img" aria-label={`${shortCar(x)} ahead ${h2h.a} times, ${shortCar(y)} ${h2h.b}`}>
                  <i style={{ flex: h2h.a || 0.0001, background: colourOf(x) }} />
                  <i style={{ flex: Math.max(h2h.races - h2h.a - h2h.b, 0) || 0.0001, background: 'var(--surface-3)' }} />
                  <i style={{ flex: h2h.b || 0.0001, background: colourOf(y) }} />
                </div>
                <p className="small" style={{ margin: '10px 0 0' }}>
                  {shortCar(x)} finished ahead in <b>{h2h.a}</b> of {h2h.races} races, {shortCar(y)} in <b>{h2h.b}</b>
                  {h2h.races - h2h.a - h2h.b ? `; ${h2h.races - h2h.a - h2h.b} level` : ''}. The one comparison where the two cars raced each other.
                </p>
              </>
            ) : <p className="dim small" style={{ margin: 0 }}>No races where both had a classified finisher.</p>)}
          </div>
        </div>
      )}

      <div className="panel" style={{ marginTop: 18 }}>
        <div className="panel-head"><h3>The report</h3></div>
        <ul className="report-list">{cmp.report.map((line) => <li key={line}>{line}</li>)}</ul>
      </div>

      <details className="panel panel-pad why-fair" style={{ marginTop: 18 }}>
        <summary><b>Why each measure is fair</b></summary>
        <ul>
          {[...cmp.capability.rows, ...cmp.achievement.rows, ...cmp.achievement.info, ...cmp.fingerprint].map((r) => (
            <li key={r.key}><b>{r.label}.</b> {r.why}</li>
          ))}
        </ul>
        <p className="small muted" style={{ margin: 0 }}>
          The capability verdict compares the dominance scores against the race-to-race noise: clear when the gap is at least twice the
          noise, narrow between once and twice, too close to call below. The achievement verdict counts the six records, each once; a tie
          goes to the share of the maximum points when they are clearly apart. Lap times of different seasons are never put side by side.
        </p>
      </details>
    </div>
  )
}

function VerdictCard({ title, sub, v, x, y }: { title: string; sub: string; v: Verdict; x: CarRow; y: CarRow }) {
  const w = v.winner === 'a' ? x : v.winner === 'b' ? y : null
  return (
    <div className="panel verdict-card" style={{ borderTopColor: w ? colourOf(w) : 'var(--line-2)' }}>
      <span className="kicker"><b>{title}</b> · {sub}</span>
      <h2>{v.headline}</h2>
      <span className={`badge conf conf-${v.confidence.replace(' ', '-')}`}>{v.confidence === 'too close' ? 'Too close to call' : v.confidence}</span>
      <p>{v.detail}</p>
    </div>
  )
}

/** One measure per row: the two cars' values on mirrored bars, the better one in its colour. */
function MirrorTable({ rows, x, y, counted }: { rows: Measure[]; x: CarRow; y: CarRow; counted?: number }) {
  return (
    <div className="mirror">
      <div className="mirror-head">
        <span style={{ color: colourOf(x) }}>{shortCar(x)} <span className="dim">{x.season}</span></span>
        <span />
        <span style={{ color: colourOf(y) }}>{shortCar(y)} <span className="dim">{y.season}</span></span>
      </div>
      {rows.map((r, i) => {
        const vals = [r.va, r.vb]
        const lo = Math.min(0, ...vals.filter((v): v is number => v != null))
        const hi = Math.max(0, ...vals.filter((v): v is number => v != null))
        const width = (v: number | null) => (v == null ? 0 : hi === lo ? 50 : 8 + (92 * (v - lo)) / (hi - lo))
        return (
          <div key={r.key} className={`mirror-row ${counted != null && i === counted ? 'sep' : ''} ${counted != null && i >= counted ? 'info' : ''}`} title={r.why}>
            <div className={`mirror-val left ${r.winner === 'a' ? 'win' : ''}`}>{r.a}</div>
            <div className="mirror-mid">
              <span className="mirror-bar left"><i style={{ width: `${width(r.va)}%`, background: r.winner === 'a' ? colourOf(x) : 'var(--surface-3)' }} /></span>
              <span className="mirror-label">{r.label}</span>
              <span className="mirror-bar right"><i style={{ width: `${width(r.vb)}%`, background: r.winner === 'b' ? colourOf(y) : 'var(--surface-3)' }} /></span>
            </div>
            <div className={`mirror-val right ${r.winner === 'b' ? 'win' : ''}`}>{r.b}</div>
          </div>
        )
      })}
    </div>
  )
}

/** Dominance race by race over each season's progress, both cars on one chart. */
function DevelopmentChart({ a, b }: { a: CarDetail; b: CarDetail }) {
  const [box, W] = useWidth<HTMLDivElement>(800)
  const svg = useRef<SVGSVGElement>(null)
  const H = 240, M = { t: 16, r: 16, b: 28, l: 40 }
  const ca = curve(a.races), cb = curve(b.races)
  const ys = [...ca, ...cb].map((p) => p.y)
  const top = Math.max(1, ...ys) * 1.1, bottom = Math.min(-0.5, ...ys) * 1.1
  const X = (v: number) => M.l + v * (W - M.l - M.r)
  const Y = (v: number) => M.t + ((top - v) / (top - bottom)) * (H - M.t - M.b)
  const path = (pts: { x: number; y: number }[]) => pts.map((p, i) => `${i ? 'L' : 'M'}${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`).join('')
  useLayoutEffect(() => {
    if (!svg.current || reducedMotion()) return
    svg.current.querySelectorAll<SVGPathElement>('path.dev-line').forEach((p) => {
      const len = p.getTotalLength()
      gsap.fromTo(p, { strokeDasharray: `${len} ${len}`, strokeDashoffset: len }, { strokeDashoffset: 0, duration: 1.4, ease: 'power2.out' })
    })
  }, [a.key, b.key, W])
  if (!ca.length && !cb.length) return <p className="dim small panel-pad">No race-by-race data for these cars.</p>
  const ticks = [bottom, 0, top / 2, top].map((t) => Math.round(t * 10) / 10)
  return (
    <div ref={box} className="dev-chart">
      <svg ref={svg} width={W} height={H} role="img" aria-label={`Dominance race by race: ${shortCar(a)} ${a.season} and ${shortCar(b)} ${b.season}`}>
        {[...new Set(ticks)].map((t) => (
          <g key={t}>
            <line x1={M.l} x2={W - M.r} y1={Y(t)} y2={Y(t)} className={t === 0 ? 'zero' : 'grid'} />
            <text x={M.l - 6} y={Y(t) + 3} textAnchor="end" className="tick">{t > 0 ? `+${t}` : t}</text>
          </g>
        ))}
        <text x={M.l + 6} y={Y(0) + 13} className="tick">middle of its field</text>
        <text x={M.l} y={H - 6} className="tick">first race</text>
        <text x={W - M.r} y={H - 6} textAnchor="end" className="tick">last race</text>
        {[[a, ca], [b, cb]].map(([c, pts]) => {
          const car = c as CarDetail, list = pts as typeof ca
          return (
            <g key={car.key}>
              <path className="dev-line" d={path(list)} fill="none" stroke={colourOf(car)} strokeWidth={2.2} />
              {list.map((p) => (
                <circle key={p.round} cx={X(p.x)} cy={Y(p.y)} r={3} fill={colourOf(car)}>
                  <title>{`${shortCar(car)} ${car.season}, round ${p.round}${p.track ? ` (${p.track})` : ''}: ${signed(p.y)}`}</title>
                </circle>
              ))}
            </g>
          )
        })}
      </svg>
      <div className="dev-legend small">
        {[a, b].map((c) => <span key={c.key}><i style={{ background: colourOf(c) }} />{shortCar(c)} {c.season}</span>)}
      </div>
    </div>
  )
}

function SameCircuits({ x, y, a, b }: { x: CarRow; y: CarRow; a: CarDetail; b: CarDetail }) {
  const rows = sameCircuits(a.races, b.races)
  return (
    <div className="panel" style={{ marginTop: 18 }}>
      <div className="panel-head"><h3>At the same circuits</h3><span className="aside">each against its own field there · dominance</span></div>
      {rows.length ? (
        <div className="table-wrap">
          <table className="timing compare-table">
            <thead><tr><th>Circuit</th><th className="r">{shortCar(x)} <span className="dim">{x.season}</span></th><th className="r">{shortCar(y)} <span className="dim">{y.season}</span></th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.track_id}>
                  <td className="cat">{r.track}</td>
                  <td className={`r num ${r.winner === 'a' ? 'win' : ''}`} style={r.winner === 'a' ? { boxShadow: `inset -3px 0 0 ${colourOf(x)}` } : undefined}>{signed(r.a)}</td>
                  <td className={`r num ${r.winner === 'b' ? 'win' : ''}`} style={r.winner === 'b' ? { boxShadow: `inset -3px 0 0 ${colourOf(y)}` } : undefined}>{signed(r.b)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="dim small panel-pad" style={{ margin: 0 }}>These two seasons share no circuit with race-by-race data.</p>}
      <p className="small dim panel-pad" style={{ margin: 0 }}>
        Lap times at the same circuit still cannot be compared across seasons (the cars, tyres and often the layout changed), so each
        number is how far the car stood out from its own field at that circuit.
      </p>
    </div>
  )
}
