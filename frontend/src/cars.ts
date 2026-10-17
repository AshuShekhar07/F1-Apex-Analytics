/** Wording for the car ratings: the measured numbers behind each 0-100 rating, in sentences. */

import type { CarRow } from './types'

/** "Elite" etc. for the overall rating (ranked against every car of 2018 onwards). */
export function overallBand(r: number | null): string {
  if (r == null) return 'Not rated'
  return r >= 90 ? 'Elite' : r >= 70 ? 'Front-runner' : r >= 40 ? 'Midfield' : 'Backmarker'
}

function pct(v: number): string {
  return `${Math.abs(v).toFixed(2)}%`
}

/** The numbers behind the ratings, one sentence each. */
export function metricLines(c: CarRow): string[] {
  const m = c.metrics
  const out: string[] = []
  if (m.quali_gap_pct != null) {
    out.push(`Qualifying: ${pct(m.quali_gap_pct)} ${m.quali_gap_pct <= 0 ? 'quicker' : 'slower'} than the median team ` +
      `(median of ${m.quali_races} races)${m.quickest_in_qualifying ? `; quickest car at ${m.quickest_in_qualifying}` : ''}.`)
  }
  if (c.season_best.quali != null) out.push(`The quickest car of ${c.season} over a lap, ${pct(c.season_best.quali)} clear of the next.`)
  if (m.race_gap_pct != null) {
    out.push(`Race pace: ${pct(m.race_gap_pct)} ${m.race_gap_pct <= 0 ? 'quicker' : 'slower'} than the median team ` +
      `(median of ${m.race_races} races)${m.quickest_in_race ? `; quickest car at ${m.quickest_in_race}` : ''}.`)
  }
  if (c.season_best.race != null) out.push(`The quickest race car of ${c.season}, ${pct(c.season_best.race)} clear of the next.`)
  if (m.top_speed_kmh != null) {
    const v = m.top_speed_kmh
    out.push(`Top speed: ${Math.abs(v).toFixed(1)} km/h ${v >= 0 ? 'above' : 'below'} the median team in qualifying (${m.speed_races} races).`)
  }
  if (m.finish_rate != null) {
    const causes = [m.mechanical ? `${m.mechanical} mechanical` : '', m.retired_unknown ? `${m.retired_unknown} with no cause given` : '',
      m.incidents ? `${m.incidents} crash${m.incidents === 1 ? '' : 'es'} (not counted)` : ''].filter(Boolean)
    out.push(`Reliability: finished ${Math.round(m.finish_rate * 100)}% of ${m.starts} starts${causes.length ? `; retirements: ${causes.join(', ')}` : ''}.`)
  }
  return out
}

/** Race drivers, the regulars first; anyone with under a third of the team's races in brackets. */
export function driverLine(c: CarRow): string {
  const surname = (n: string | null) => (n ?? '?').split(' ').slice(-1)[0]
  const most = Math.max(...c.drivers.map((d) => d.races), 1)
  const main = c.drivers.filter((d) => d.races >= most / 3).map((d) => surname(d.name))
  const extra = c.drivers.filter((d) => d.races < most / 3).map((d) => `${surname(d.name)} ${d.races}`)
  return main.join(', ') + (extra.length ? ` (also ${extra.join(', ')})` : '')
}

/** Every car ranked by its overall rating; unrated ones last. */
export function ranking(cars: CarRow[]): CarRow[] {
  return [...cars].sort((a, b) => (b.ratings.overall ?? -1) - (a.ratings.overall ?? -1) || b.season - a.season)
}

/** Colours for the drawing when there is no photo: the livery's palette, else the team colour. */
export function liveryColours(c: CarRow): [string, string, string] {
  const p = c.palette ?? []
  const team = c.color_hex && /^#[0-9a-f]{6}$/i.test(c.color_hex) ? c.color_hex : '#888888'
  return [p[0] ?? team, p[1] ?? '#1a1a1a', p[2] ?? '#ffffff']
}
