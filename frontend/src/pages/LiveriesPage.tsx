import { useEffect, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

import { api } from '../api'
import { driverLine, liveryColours, metricLines, overallBand, ranking } from '../cars'
import { compareCars, headToHead, label, pickPair, shortCar } from '../compareCars'
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
  const view = params.get('view') === 'ranking' ? 'ranking' : params.get('view') === 'compare' ? 'compare' : 'season'
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
      <p className="muted" style={{ margin: '-6px 0 18px', maxWidth: 820 }}>
        Every car since 2018, rated from the timing data: qualifying pace, race pace, top speed and reliability, each
        0–100 against every car of 2018 onwards, so a car that dominated its field rates near 100.
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

// --- compare two cars ---------------------------------------------------------------------------

function CompareView({ cars, ranked, photos, a, b, onPick }: {
  cars: CarRow[]; ranked: CarRow[]; photos: Record<string, LiveryPhoto>; a: string | null; b: string | null
  onPick: (a: string, b: string) => void
}) {
  const [x, y] = pickPair(cars, ranked, a, b)
  const sameSeason = !!x && !!y && x.season === y.season
  const matrix = useAsync(() => (sameSeason ? api.seasonResults(x!.season) : Promise.resolve(null)), [sameSeason ? x!.season : 0])
  const seasons = useMemo(() => [...new Set(cars.map((c) => c.season))].sort((p, q) => q - p), [cars])
  if (!x || !y) return <div className="panel empty">Not enough cars with results to compare.</div>
  const cmp = compareCars(x, y)
  const h2h = sameSeason && matrix.data ? headToHead(matrix.data, x.team_id, y.team_id) : null
  const colourOf = (c: CarRow) => glowColour(normaliseHex(c.color_hex, '#888888'))
  const winner = cmp.winner === 'a' ? x : cmp.winner === 'b' ? y : null
  const options = (other: string) => seasons.map((s) => (
    <optgroup key={s} label={String(s)}>
      {cars.filter((c) => c.season === s).sort((p, q) => p.results.position - q.results.position).map((c) => (
        <option key={c.key} value={c.key} disabled={c.key === other}>{c.season} · {shortCar(c)} · P{c.results.position} · {c.ratings.overall ?? '–'}</option>
      ))}
    </optgroup>
  ))

  return (
    <>
      <div className="compare-pick">
        <select className="select" aria-label="First car" value={x.key} onChange={(e) => onPick(e.target.value, y.key)}>{options(y.key)}</select>
        <button type="button" className="btn" onClick={() => onPick(y.key, x.key)} aria-label="Swap the two cars" title="Swap">⇄</button>
        <select className="select" aria-label="Second car" value={y.key} onChange={(e) => onPick(x.key, e.target.value)}>{options(x.key)}</select>
      </div>

      <div className="compare-heads">
        {[x, y].map((c, i) => (
          <div key={c.key} className={`panel compare-head ${winner === c ? 'won' : ''}`} style={winner === c ? { borderColor: colourOf(c) } : undefined}>
            <CarMedia c={c} photo={photos[c.key]} />
            <div className="car-head">
              <span className="team-bar" style={{ background: colourOf(c), height: 34 }} />
              <div className="car-title" title={c.car ?? undefined}>
                <h3>{shortCar(c)}</h3>
                <span className="dim small">{c.season}{c.pu ? ` · ${c.pu} power` : ''}</span>
              </div>
              <span className="compare-score num" title="Categories won">{i === 0 ? cmp.score.a : cmp.score.b}</span>
            </div>
            {winner === c && <div className="compare-badge" style={{ background: colourOf(c) }}>The better car</div>}
          </div>
        ))}
      </div>

      <div className="panel verdict-banner" style={{ borderLeftColor: winner ? colourOf(winner) : 'var(--line-2)' }}>
        <span className="kicker"><b>Verdict</b>{cmp.decidedBy === 'overall rating' ? ' · decided on the overall rating' : ` · ${cmp.score.a}–${cmp.score.b} on categories`}</span>
        <h2>{winner ? `${label(winner)} is the better car` : 'Too close to call'}</h2>
        <p>{verdictLine(cmp, x, y)}</p>
      </div>

      <div className="grid-main">
        <div className="panel">
          <div className="panel-head"><h3>Category by category</h3><span className="aside">✓ = better · each category counts once</span></div>
          <div className="table-wrap">
            <table className="timing compare-table">
              <thead><tr><th>Category</th><th>{shortCar(x)} <span className="dim">{x.season}</span></th><th>{shortCar(y)} <span className="dim">{y.season}</span></th></tr></thead>
              <tbody>
                {cmp.rows.filter((r) => r.verdict).map((r) => <CompareRowView key={r.key} r={r} x={x} y={y} colourOf={colourOf} />)}
                <tr className="compare-sep"><td colSpan={3}>Shown for information, not counted</td></tr>
                {cmp.rows.filter((r) => !r.verdict).map((r) => <CompareRowView key={r.key} r={r} x={x} y={y} colourOf={colourOf} />)}
              </tbody>
            </table>
          </div>
        </div>
        <div className="stack">
          <div className="panel">
            <div className="panel-head"><h3>The report</h3></div>
            <ul className="report-list">{cmp.report.slice(1).map((line) => <li key={line}>{line}</li>)}</ul>
          </div>
          {sameSeason && (
            <div className="panel">
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
        </div>
      </div>
      <div className="panel panel-pad" style={{ marginTop: 18 }}>
        <p className="small muted" style={{ margin: 0 }}>
          <b style={{ color: 'var(--text)' }}>How the verdict is made.</b> Eight categories, each counting once: qualifying pace and race pace
          (against the median team of the car's own season), win rate, pole rate, podiums per race, one-twos and reliability (per race
          entered, so seasons of different lengths compare fairly) and the constructors' championship. The car that wins more of them is
          the better car; a tie goes to the higher overall rating. Points per race and top speed are shown but not counted: the points
          system changed over the years (sprints, the fastest-lap point), and top speed says what kind of car it was, not how good.
        </p>
      </div>
    </>
  )
}

function verdictLine(cmp: ReturnType<typeof compareCars>, x: CarRow, y: CarRow): string {
  const level = cmp.score.counted - cmp.score.a - cmp.score.b
  const tail = level ? `, ${level} level` : ''
  if (cmp.decidedBy === 'overall rating') {
    return `Level on categories (${cmp.score.a}–${cmp.score.b}${tail}); the higher overall rating decides it: ${x.ratings.overall} against ${y.ratings.overall}.`
  }
  if (!cmp.winner) return `${cmp.score.a}–${cmp.score.b} on categories${tail}, and the same overall rating.`
  const [w, l, ws, ls] = cmp.winner === 'a' ? [x, y, cmp.score.a, cmp.score.b] : [y, x, cmp.score.b, cmp.score.a]
  return `${shortCar(w)} wins ${ws} of the ${cmp.score.counted} categories, the ${shortCar(l)} ${ls}${tail}.`
}

function CompareRowView({ r, x, y, colourOf }: { r: ReturnType<typeof compareCars>['rows'][number]; x: CarRow; y: CarRow
  colourOf: (c: CarRow) => string }) {
  return (
    <tr>
      <td className="cat">{r.label}</td>
      {(['a', 'b'] as const).map((side) => (
        <td key={side} className={r.winner === side ? 'win' : ''}
            style={r.winner === side ? { boxShadow: `inset 3px 0 0 ${colourOf(side === 'a' ? x : y)}` } : undefined}>
          {r.winner === side && <span className="tick" aria-label="better">✓ </span>}{side === 'a' ? r.a : r.b}
        </td>
      ))}
    </tr>
  )
}
