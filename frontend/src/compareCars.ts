/**
 * Two cars side by side: which was the better car, and why. Cars of different seasons never raced
 * each other, so every measure is against the car's own field (pace against the season's median
 * team, results per race entered), which keeps cars of different years comparable.
 */

import type { CarRow, SeasonResults } from './types'

/** "Red Bull Racing" -> "Red Bull", "Haas F1 Team" -> "Haas" (as format.shortName for teams). */
function teamShort(name: string): string {
  return name.replace(/\s+(Racing|F1 Team|Team)$/i, '').trim() || name
}

export type Side = 'a' | 'b'

export interface Category {
  key: string
  label: string
  /** counted in the verdict (false: shown for information) */
  verdict: boolean
  /** comparable value, higher = better; null when not measured */
  value: (c: CarRow) => number | null
  text: (c: CarRow) => string
}

const rate = (n: number, of: number) => (of > 0 ? n / of : null)
const pctText = (v: number, digits = 0) => `${(v * 100).toFixed(digits)}%`
const gapText = (gap: number | null) => (gap == null ? '–' : `${Math.abs(gap).toFixed(2)}% ${gap <= 0 ? 'quicker' : 'slower'}`)

export const CATEGORIES: Category[] = [
  { key: 'quali', label: 'Qualifying pace', verdict: true, value: (c) => (c.metrics.quali_gap_pct == null ? null : -c.metrics.quali_gap_pct),
    text: (c) => `${gapText(c.metrics.quali_gap_pct)} than the median team${c.ratings.quali != null ? ` · ${c.ratings.quali}/100` : ''}` },
  { key: 'race', label: 'Race pace', verdict: true, value: (c) => (c.metrics.race_gap_pct == null ? null : -c.metrics.race_gap_pct),
    text: (c) => `${gapText(c.metrics.race_gap_pct)} than the median team${c.ratings.race != null ? ` · ${c.ratings.race}/100` : ''}` },
  { key: 'wins', label: 'Win rate', verdict: true, value: (c) => rate(c.results.wins, c.results.races),
    text: (c) => `${c.results.wins} of ${c.results.races} races${c.results.races ? ` (${pctText(c.results.wins / c.results.races)})` : ''}` },
  { key: 'poles', label: 'Pole rate', verdict: true, value: (c) => rate(c.results.poles, c.results.races),
    text: (c) => `${c.results.poles} of ${c.results.races}${c.results.races ? ` (${pctText(c.results.poles / c.results.races)})` : ''}` },
  { key: 'podiums', label: 'Podiums per race', verdict: true, value: (c) => rate(c.results.podiums, c.results.races),
    text: (c) => `${c.results.podiums} podiums${c.results.races ? ` (${(c.results.podiums / c.results.races).toFixed(2)} a race)` : ''}` },
  { key: 'onetwos', label: 'One-twos', verdict: true, value: (c) => rate(c.results.one_twos, c.results.races),
    text: (c) => `${c.results.one_twos}${c.results.races ? ` (${pctText(c.results.one_twos / c.results.races)} of races)` : ''}` },
  { key: 'reliability', label: 'Reliability', verdict: true, value: (c) => c.metrics.finish_rate,
    text: (c) => (c.metrics.finish_rate == null ? '–' : `finished ${pctText(c.metrics.finish_rate)} of ${c.metrics.starts} starts`) },
  { key: 'title', label: "Constructors' championship", verdict: true, value: (c) => -c.results.position,
    text: (c) => (c.results.position === 1 ? 'Champions' : `P${c.results.position}`) },
  // shown, not counted
  { key: 'overall', label: 'Overall rating', verdict: false, value: (c) => c.ratings.overall, text: (c) => `${c.ratings.overall ?? '–'}/100` },
  { key: 'margin', label: 'Margin over the next car', verdict: false,
    value: (c) => c.season_best.quali ?? c.season_best.race ?? null,
    text: (c) => (c.season_best.quali != null || c.season_best.race != null
      ? [c.season_best.quali != null ? `${c.season_best.quali.toFixed(2)}% in qualifying` : '', c.season_best.race != null
        ? `${c.season_best.race.toFixed(2)}% in races` : ''].filter(Boolean).join(', ')
      : 'not the quickest of its season') },
  { key: 'speed', label: 'Top speed', verdict: false, value: (c) => c.metrics.top_speed_kmh,
    text: (c) => (c.metrics.top_speed_kmh == null ? '–' : `${c.metrics.top_speed_kmh >= 0 ? '+' : '−'}${Math.abs(c.metrics.top_speed_kmh).toFixed(1)} km/h on the median team`) },
  { key: 'points', label: 'Points per race', verdict: false, value: (c) => rate(c.results.points, c.results.races),
    text: (c) => (c.results.races ? `${(c.results.points / c.results.races).toFixed(1)} (${Math.round(c.results.points)} in total)` : '–') },
]

export interface CompareRow {
  key: string
  label: string
  verdict: boolean
  a: string
  b: string
  winner: Side | null
}

export interface Comparison {
  rows: CompareRow[]
  score: { a: number; b: number; counted: number }
  winner: Side | null
  /** how the verdict was reached */
  decidedBy: 'categories' | 'overall rating' | null
  report: string[]
  sameSeason: boolean
}

/** "Mercedes W11", "Red Bull RB19", "Racing Bulls VCARB 02": team and model, for sentences. */
export function shortCar(c: CarRow): string {
  const team = teamShort(c.team_name ?? '?')
  if (!c.car) return team
  const tokens = c.car.split(/\s+/)
  const i = tokens.findIndex((t) => /\d/.test(t) && t !== 'F1')
  if (i === -1) return c.car
  const code = /^\d+$/.test(tokens[i]) && i > 0 ? `${tokens[i - 1]} ${tokens[i]}` : tokens[i]
  return code.toLowerCase().startsWith(team.toLowerCase()) ? code : `${team} ${code}`
}

export const label = (c: CarRow) => `${shortCar(c)} (${c.season})`

function better(x: number | null, y: number | null): Side | null {
  if (x == null || y == null || Math.abs(x - y) < 1e-9) return null
  return x > y ? 'a' : 'b'
}

export function compareCars(a: CarRow, b: CarRow): Comparison {
  const rows = CATEGORIES.map((cat) => ({
    key: cat.key, label: cat.label, verdict: cat.verdict, a: cat.text(a), b: cat.text(b),
    winner: cat.key === 'speed' || cat.key === 'margin' ? null : better(cat.value(a), cat.value(b)),
  }))
  const counted = rows.filter((r) => r.verdict && CATEGORIES.find((c) => c.key === r.key)!.value(a) != null
    && CATEGORIES.find((c) => c.key === r.key)!.value(b) != null)
  const score = { a: counted.filter((r) => r.winner === 'a').length, b: counted.filter((r) => r.winner === 'b').length, counted: counted.length }
  let winner: Side | null = score.a > score.b ? 'a' : score.b > score.a ? 'b' : null
  let decidedBy: Comparison['decidedBy'] = winner ? 'categories' : null
  if (!winner) {
    winner = better(a.ratings.overall, b.ratings.overall)
    decidedBy = winner ? 'overall rating' : null
  }
  return { rows, score, winner, decidedBy, report: report(a, b, rows, score, winner, decidedBy), sameSeason: a.season === b.season }
}

function report(a: CarRow, b: CarRow, rows: CompareRow[], score: Comparison['score'], winner: Side | null,
                decidedBy: Comparison['decidedBy']): string[] {
  const A = label(a), B = label(b)
  const name = (s: Side) => (s === 'a' ? A : B)
  const won = (key: string) => rows.find((r) => r.key === key)?.winner ?? null
  const out: string[] = []

  if (winner && decidedBy === 'categories') {
    const loser = winner === 'a' ? B : A
    out.push(`${name(winner)} is the better car: it wins ${Math.max(score.a, score.b)} of the ${score.counted} categories against ${loser}'s ${Math.min(score.a, score.b)}.`)
  } else if (winner) {
    out.push(`Level at ${score.a}–${score.b} on categories; ${name(winner)} edges it on the overall rating (${(winner === 'a' ? a : b).ratings.overall} against ${(winner === 'a' ? b : a).ratings.overall}).`)
  } else {
    out.push(`Too close to call: ${score.a}–${score.b} on categories and the same overall rating.`)
  }

  // pace
  const q = won('quali'), r = won('race')
  const gaps = (c: CarRow, k: 'quali_gap_pct' | 'race_gap_pct') => gapText(c.metrics[k])
  if (q && q === r) {
    out.push(`${name(q)} was the faster car against its field, over one lap (${gaps(q === 'a' ? a : b, 'quali_gap_pct')} than the median team, `
      + `against ${gaps(q === 'a' ? b : a, 'quali_gap_pct')}) and in races (${gaps(q === 'a' ? a : b, 'race_gap_pct')} against ${gaps(q === 'a' ? b : a, 'race_gap_pct')}).`)
  } else {
    if (q) out.push(`Over one lap ${name(q)} was further ahead of its field: ${gaps(q === 'a' ? a : b, 'quali_gap_pct')} than the median team, against ${gaps(q === 'a' ? b : a, 'quali_gap_pct')}.`)
    if (r) out.push(`In races ${name(r)} was further ahead: ${gaps(r === 'a' ? a : b, 'race_gap_pct')} than the median team, against ${gaps(r === 'a' ? b : a, 'race_gap_pct')}.`)
  }
  for (const c of [a, b]) {
    if (c.season_best.quali != null) out.push(`${label(c)} was the quickest car of ${c.season} over a lap, ${c.season_best.quali.toFixed(2)}% clear of the next.`)
  }

  // results
  const w = won('wins')
  out.push(w
    ? `${name(w)} won more often: ${(w === 'a' ? a : b).results.wins} of ${(w === 'a' ? a : b).results.races} races, against ${(w === 'a' ? b : a).results.wins} of ${(w === 'a' ? b : a).results.races}.`
    : `Both won the same share of their races (${a.results.wins} of ${a.results.races}; ${b.results.wins} of ${b.results.races}).`)
  const p = won('poles')
  if (p) out.push(`${name(p)} took pole more often: ${(p === 'a' ? a : b).results.poles} against ${(p === 'a' ? b : a).results.poles}.`)
  const champs = [a, b].filter((c) => c.results.position === 1)
  out.push(champs.length === 2 ? "Both were constructors' champions."
    : champs.length === 1 ? `Only ${label(champs[0])} won the constructors' title; ${label(champs[0] === a ? b : a)} finished P${(champs[0] === a ? b : a).results.position}.`
      : `Neither won the constructors' title (P${a.results.position} and P${b.results.position}).`)

  // reliability and character
  const rel = won('reliability')
  if (rel && a.metrics.finish_rate != null && b.metrics.finish_rate != null) {
    const x = rel === 'a' ? a : b, y = rel === 'a' ? b : a
    out.push(`${name(rel)} was the more reliable: it finished ${pctText(x.metrics.finish_rate!)} of its starts, against ${pctText(y.metrics.finish_rate!)}.`)
  }
  if (a.character && b.character) {
    out.push(a.character.kind === b.character.kind
      ? `Both were ${a.character.label.toLowerCase()} cars.`
      : `Character: ${A} ${a.character.label.toLowerCase()}, ${B} ${b.character.label.toLowerCase()}.`)
  }
  if (a.season !== b.season) {
    out.push(`They never raced each other: each is measured against its own season's field, which is what makes cars of different years comparable.`)
  }
  return out
}

export interface HeadToHead {
  a: number
  b: number
  races: number
}

/** Same season only: in how many Grands Prix each team's best car finished ahead of the other's.
    A race where neither team had a classified finisher is not counted. */
export function headToHead(matrix: SeasonResults, teamA: number, teamB: number): HeadToHead {
  const best = new Map<string, number>()
  for (const d of matrix.drivers) {
    for (const cell of d.results) {
      if (cell.team_id !== teamA && cell.team_id !== teamB) continue
      if (!cell.race) continue
      const pos = cell.race.classified && cell.race.position != null ? cell.race.position : 999
      const k = `${cell.round}:${cell.team_id}`
      best.set(k, Math.min(best.get(k) ?? 1000, pos))
    }
  }
  let a = 0, b = 0, races = 0
  for (const r of matrix.rounds.filter((x) => x.raced)) {
    const x = best.get(`${r.round}:${teamA}`), y = best.get(`${r.round}:${teamB}`)
    if (x == null || y == null || (x === 999 && y === 999)) continue
    races++
    if (x < y) a++
    else if (y < x) b++
  }
  return { a, b, races }
}

/** The two cars to show: from the link, else the two best of all time. */
export function pickPair(cars: CarRow[], ranked: CarRow[], a: string | null, b: string | null): [CarRow | null, CarRow | null] {
  const byKey = new Map(cars.map((c) => [c.key, c]))
  const first = (a && byKey.get(a)) || ranked[0] || null
  const second = (b && b !== first?.key && byKey.get(b)) || ranked.find((c) => c.key !== first?.key) || null
  return [first, second]
}
