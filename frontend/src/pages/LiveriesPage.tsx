import { useEffect, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

import { api } from '../api'
import { driverLine, liveryColours, metricLines, overallBand, ranking } from '../cars'
import { ErrorBox } from '../components/Status'
import { useAsync } from '../hooks'
import { LiveryDrawing } from '../race/LiveryDrawing'
import { glowColour, normaliseHex } from '../theme/color'
import type { CarRow, LiveryPhoto } from '../types'

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
  const view = params.get('view') === 'ranking' ? 'ranking' : 'season'
  const season = Number(params.get('season')) || data?.seasons[0] || 0
  const shown = useMemo(() => (data?.cars ?? []).filter((c) => c.season === season)
    .sort((a, b) => a.results.position - b.results.position), [data, season])
  const best = useMemo(() => ranking(data?.cars ?? []), [data])
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
                    onClick={() => setParams({ view: 'ranking' })}>All-time ranking</button>
          </div>
          {view === 'season' && data && (
            <select className="select" aria-label="Season" value={season} onChange={(e) => setParams({ season: e.target.value })}>
              {data.seasons.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          )}
        </div>
      </div>
      <p className="muted" style={{ margin: '-6px 0 18px', maxWidth: 820 }}>
        Every car since 2018, rated from the timing data: qualifying pace, race pace, top speed and reliability, each
        0–100 against every car of 2018 onwards, so a car that dominated its field rates near 100.
      </p>

      {cars.error && <ErrorBox error={cars.error} />}
      {cars.loading && <div className="skeleton" style={{ height: 360 }} />}

      {data && view === 'season' && (
        shown.length
          ? <div className="car-grid">{shown.map((c) => <CarCard key={c.key} c={c} photo={photos.data?.[c.key]} />)}</div>
          : <div className="panel empty">No cars with results in {season}.</div>
      )}
      {data && view === 'ranking' && (
        <Ranking cars={best} onPick={(c) => setParams({ season: String(c.season) }, { replace: false })} />
      )}

      {data && (
        <div className="panel panel-pad" style={{ marginTop: 18 }}>
          <p className="small muted" style={{ margin: 0 }}>
            <b style={{ color: 'var(--text)' }}>How the ratings work.</b> {data.method.quali} {data.method.race} {data.method.speed}{' '}
            {data.method.reliability} {data.method.ratings} {data.method.character} Photos: freely licensed pictures from
            Wikimedia Commons, credited under each; where there is none yet, a drawing in the livery's colours.
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

function CarCard({ c, photo }: { c: CarRow; photo?: LiveryPhoto }) {
  const colour = glowColour(normaliseHex(c.color_hex, '#888888'))
  const r = c.ratings
  const name = c.car ?? c.team_name ?? 'Unknown car'
  const res = c.results
  return (
    <article className="panel car-card" id={c.key}>
      <div className="car-media">
        {photo
          ? <img src={photo.src} alt={`${name}, ${c.season}`} loading="lazy" referrerPolicy="no-referrer" />
          : <LiveryDrawing colours={liveryColours(c)} label={name} />}
        <span className="car-overall" title="Overall rating, against every car of 2018 onwards">
          <b>{r.overall ?? '–'}</b><small>{overallBand(r.overall)}</small>
        </span>
      </div>
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
      {photo && (
        <p className="car-credit">
          Photo: {photo.author} · {photo.license_url ? <a href={photo.license_url} target="_blank" rel="noreferrer">{photo.license}</a> : photo.license}
          {photo.page && <> · <a href={photo.page} target="_blank" rel="noreferrer">Wikimedia Commons</a></>}
        </p>
      )}
    </article>
  )
}

function Ranking({ cars, onPick }: { cars: CarRow[]; onPick: (c: CarRow) => void }) {
  return (
    <div className="panel">
      <div className="panel-head"><h3>Every car, best first</h3><span className="aside">overall rating</span></div>
      <div className="table-wrap">
        <table className="timing">
          <thead>
            <tr>
              <th>#</th><th>Car</th><th className="r">Overall</th><th className="r hide-sm">Quali</th><th className="r hide-sm">Race</th>
              <th className="r hide-sm">Speed</th><th className="r hide-sm">Reliab.</th><th className="hide-sm">Character</th><th className="r">Result</th>
            </tr>
          </thead>
          <tbody>
            {cars.map((c, i) => (
              <tr key={c.key} className="clickable" onClick={() => onPick(c)} title="Open this season">
                <td className="pos-cell"><span className={`pos-box ${i < 3 ? `p${i + 1}` : ''}`}>{i + 1}</span></td>
                <td>
                  <div className="driver-cell">
                    <span className="team-bar" style={{ background: glowColour(normaliseHex(c.color_hex, '#888888')) }} />
                    <span className="who"><span className="name">{c.car ?? c.team_name}</span><span className="team">{c.season} · {c.team_name}</span></span>
                  </div>
                </td>
                <td className="r num" style={{ fontWeight: 700 }}>{c.ratings.overall ?? '–'}</td>
                <td className="r num hide-sm">{c.ratings.quali ?? '–'}</td>
                <td className="r num hide-sm">{c.ratings.race ?? '–'}</td>
                <td className="r num hide-sm">{c.ratings.speed ?? '–'}</td>
                <td className="r num hide-sm">{c.ratings.reliability ?? '–'}</td>
                <td className="hide-sm small">{c.character?.label ?? '–'}</td>
                <td className="r num">P{c.results.position} · {c.results.wins}W</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
