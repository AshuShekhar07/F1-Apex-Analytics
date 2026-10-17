/**
 * Two cars side by side, fairly. Cars of different seasons never raced each other and their
 * rules, tyres and fuel differed, so raw lap times are never compared: every measure is against
 * the car's OWN season's field (car_compare_v1 on the server). Two separate verdicts:
 *
 *   capability   how dominant the car was in its era: its dominance score (how far it stood out
 *                from its own field, in units of that field's spread), with a confidence from the
 *                race-to-race noise: clear (difference at least 2x the noise), narrow (1-2x) or
 *                too close to call
 *   achievement  what the team made of it, normalised for the points systems: share of the most
 *                points it could score, win / pole / podium / one-two rates, the championship
 *
 * "Car only" averages both cars (a star driver weighs half); "car + drivers" uses the team's
 * quicker car, so the better driver's pace counts in full.
 */

import type { CarDominance, CarRace, CarRow, SeasonResults } from './types'

/** "Red Bull Racing" -> "Red Bull", "Haas F1 Team" -> "Haas" (as format.shortName for teams). */
function teamShort(name: string): string {
  return name.replace(/\s+(Racing|F1 Team|Team)$/i, '').trim() || name
}

export type Side = 'a' | 'b'
export type Mode = 'car' | 'drivers'
export type Confidence = 'clear' | 'narrow' | 'too close'

export interface Measure {
  key: string
  label: string
  /** why the measure is fair across seasons, in one sentence */
  why: string
  a: string
  b: string
  /** comparable values, higher = better (null: not measured) */
  va: number | null
  vb: number | null
  winner: Side | null
}

export interface Verdict {
  winner: Side | null
  confidence: Confidence
  headline: string
  detail: string
}

export interface Comparison {
  sameSeason: boolean
  sameEra: boolean
  eraNote: string
  capability: { rows: Measure[]; verdict: Verdict }
  achievement: { rows: Measure[]; info: Measure[]; verdict: Verdict; score: { a: number; b: number; counted: number } }
  fingerprint: Measure[]
  report: string[]
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

function better(x: number | null, y: number | null, tolerance = 1e-9): Side | null {
  if (x == null || y == null || Math.abs(x - y) <= tolerance) return null
  return x > y ? 'a' : 'b'
}

const pct = (v: number, digits = 0) => `${(v * 100).toFixed(digits)}%`
const ordinal = (n: number) => `${n}${n % 100 >= 11 && n % 100 <= 13 ? 'th' : ['th', 'st', 'nd', 'rd'][n % 10] ?? 'th'}`
const signed = (v: number, digits = 2) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(digits)}`

/** "+1.80, stood out by 1.8x its field's spread" in short form for a cell. */
export function dominanceText(d: CarDominance | null | undefined): string {
  if (!d) return 'not enough races'
  return `${signed(d.z)} ${d.z >= 0 ? 'ahead of' : 'behind'} its field · ${d.races} races`
}

/** "0.62% clear of the next car" / "0.35% behind the quickest". */
export function marginText(v: number | null | undefined): string {
  if (v == null) return '–'
  if (Math.abs(v) < 0.005) return 'level with the best'
  return v > 0 ? `${v.toFixed(2)}% clear of the next car` : `${Math.abs(v).toFixed(2)}% behind the quickest`
}

function dominance(c: CarRow, mode: Mode): CarDominance | null {
  return (mode === 'car' ? c.fair?.dominance.car_only : c.fair?.dominance.with_drivers) ?? null
}

/** Clear, narrow or too close: the difference in dominance against the race-to-race noise. */
export function capabilityVerdict(a: CarRow, b: CarRow, mode: Mode): Verdict {
  const da = dominance(a, mode), db = dominance(b, mode)
  if (!da || !db) {
    return { winner: null, confidence: 'too close', headline: 'Not enough timing data to judge capability',
      detail: 'A car needs at least three races with qualifying and race pace against its field.' }
  }
  const diff = da.z - db.z
  const noise = Math.sqrt(da.se ** 2 + db.se ** 2) || 1e-9
  const t = Math.abs(diff) / noise
  const confidence: Confidence = t >= 2 ? 'clear' : t >= 1 ? 'narrow' : 'too close'
  const winner: Side | null = confidence === 'too close' ? null : diff > 0 ? 'a' : 'b'
  const w = winner === 'a' ? a : b
  const scores = `Dominance score ${signed(da.z)} for the ${shortCar(a)}, ${signed(db.z)} for the ${shortCar(b)}`
  if (!winner) {
    return { winner, confidence, headline: 'Too close to call on capability',
      detail: `${scores}: the difference is smaller than the race-to-race noise.` }
  }
  return {
    winner, confidence,
    headline: `${label(w)} was ${confidence === 'clear' ? 'more' : 'slightly more'} dominant in its era`,
    detail: `${scores}. The gap is ${t.toFixed(1)}× the race-to-race noise, so the call is ${confidence}.`,
  }
}

export function capabilityRows(a: CarRow, b: CarRow, mode: Mode): Measure[] {
  const m = (c: CarRow, k: 'quali' | 'race') => (mode === 'car'
    ? c.fair?.margin[k === 'quali' ? 'quali_pct' : 'race_pct']
    : c.fair?.margin[k === 'quali' ? 'quali_pct_with_drivers' : 'race_pct_with_drivers']) ?? null
  const da = dominance(a, mode), db = dominance(b, mode)
  const share = (c: CarRow) => c.fair?.model?.win_share ?? null
  const rank = (c: CarRow) => c.fair?.pace_rank ?? null
  const rows: Measure[] = [
    { key: 'dominance', label: 'Dominance score', why: 'How far it stood out from its own season\'s field, measured in units of how spread out that field was, so a lead in a close era counts as much as a bigger lead in a spread-out one.',
      a: dominanceText(da), b: dominanceText(db), va: da?.z ?? null, vb: db?.z ?? null, winner: null },
    { key: 'quali', label: 'One lap: margin to the best rival', why: 'Qualifying pace against the best other car of the same season, % of a lap: never two seasons\' lap times against each other.',
      a: marginText(m(a, 'quali')), b: marginText(m(b, 'quali')), va: m(a, 'quali'), vb: m(b, 'quali'), winner: null },
    { key: 'race', label: 'Race pace: margin to the best rival', why: 'Median clean race lap against the best other car of the same race.',
      a: marginText(m(a, 'race')), b: marginText(m(b, 'race')), va: m(a, 'race'), vb: m(b, 'race'), winner: null },
    { key: 'model', label: 'With an average driver', why: 'The race model splits results into car and driver; with the same average driver in every car of its season, the share of races this car would win.',
      a: share(a) == null ? '–' : `wins ${pct(share(a)!)} of races · ${ordinal(a.fair!.model!.rank)} of ${a.fair!.model!.teams}`,
      b: share(b) == null ? '–' : `wins ${pct(share(b)!)} of races · ${ordinal(b.fair!.model!.rank)} of ${b.fair!.model!.teams}`,
      va: share(a), vb: share(b), winner: null },
    { key: 'rank', label: 'Pace rank in its season', why: 'Where it ranked among the cars it raced against.',
      a: rank(a) ? (rank(a)![0] === 1 ? `fastest of ${rank(a)![1]}` : `${ordinal(rank(a)![0])} of ${rank(a)![1]}`) : '–',
      b: rank(b) ? (rank(b)![0] === 1 ? `fastest of ${rank(b)![1]}` : `${ordinal(rank(b)![0])} of ${rank(b)![1]}`) : '–',
      va: rank(a) ? -rank(a)![0] : null, vb: rank(b) ? -rank(b)![0] : null, winner: null },
  ]
  return rows.map((r) => ({ ...r, winner: better(r.va, r.vb) }))
}

export function achievementRows(a: CarRow, b: CarRow): { rows: Measure[]; info: Measure[] } {
  const ach = (c: CarRow) => c.fair?.achievements
  const rate = (k: 'win_rate' | 'pole_rate' | 'one_two_rate') => (c: CarRow) => ach(c)?.[k] ?? null
  const rows: Measure[] = [
    { key: 'points', label: 'Share of the maximum points', why: 'Points against the most a team could have scored that season (first and second every race and sprint, plus the fastest-lap point where it existed), so the changing points systems do not matter.',
      a: ach(a)?.points_share_of_max == null ? '–' : `${pct(ach(a)!.points_share_of_max!)} of ${ach(a)!.points_ceiling}`,
      b: ach(b)?.points_share_of_max == null ? '–' : `${pct(ach(b)!.points_share_of_max!)} of ${ach(b)!.points_ceiling}`,
      va: ach(a)?.points_share_of_max ?? null, vb: ach(b)?.points_share_of_max ?? null, winner: null },
    { key: 'wins', label: 'Win rate', why: 'Per race entered, so seasons of different lengths compare fairly.',
      a: `${a.results.wins} of ${a.results.races} (${pct(rate('win_rate')(a) ?? 0)})`, b: `${b.results.wins} of ${b.results.races} (${pct(rate('win_rate')(b) ?? 0)})`,
      va: rate('win_rate')(a), vb: rate('win_rate')(b), winner: null },
    { key: 'poles', label: 'Pole rate', why: 'Per qualifying session.',
      a: `${a.results.poles} (${pct(rate('pole_rate')(a) ?? 0)})`, b: `${b.results.poles} (${pct(rate('pole_rate')(b) ?? 0)})`,
      va: rate('pole_rate')(a), vb: rate('pole_rate')(b), winner: null },
    { key: 'podiums', label: 'Podiums per race', why: 'Two cars can both reach the podium, so up to 2 a race.',
      a: `${(ach(a)?.podiums_per_race ?? 0).toFixed(2)} a race`, b: `${(ach(b)?.podiums_per_race ?? 0).toFixed(2)} a race`,
      va: ach(a)?.podiums_per_race ?? null, vb: ach(b)?.podiums_per_race ?? null, winner: null },
    { key: 'onetwos', label: 'One-twos', why: 'Per race entered.',
      a: `${a.results.one_twos} (${pct(rate('one_two_rate')(a) ?? 0)})`, b: `${b.results.one_twos} (${pct(rate('one_two_rate')(b) ?? 0)})`,
      va: rate('one_two_rate')(a), vb: rate('one_two_rate')(b), winner: null },
    { key: 'title', label: "Constructors' championship", why: 'Where the team finished its own season.',
      a: a.results.position === 1 ? 'Champions' : `P${a.results.position}`, b: b.results.position === 1 ? 'Champions' : `P${b.results.position}`,
      va: -a.results.position, vb: -b.results.position, winner: null },
  ].map((r) => ({ ...r, winner: better(r.va, r.vb) }))
  const info: Measure[] = [
    { key: 'field', label: 'Share of all points scored', why: 'Its slice of every point the whole field scored that season.',
      a: ach(a)?.points_share_of_field == null ? '–' : pct(ach(a)!.points_share_of_field!, 1),
      b: ach(b)?.points_share_of_field == null ? '–' : pct(ach(b)!.points_share_of_field!, 1),
      va: ach(a)?.points_share_of_field ?? null, vb: ach(b)?.points_share_of_field ?? null, winner: null },
    { key: 'lockouts', label: 'Front-row lockouts', why: 'Both cars on the front row of the grid.',
      a: String(ach(a)?.front_row_lockouts ?? 0), b: String(ach(b)?.front_row_lockouts ?? 0),
      va: ach(a)?.front_row_lockouts ?? null, vb: ach(b)?.front_row_lockouts ?? null, winner: null },
  ].map((r) => ({ ...r, winner: better(r.va, r.vb) }))
  return { rows, info }
}

export function achievementVerdict(a: CarRow, b: CarRow, rows: Measure[]): { verdict: Verdict; score: Comparison['achievement']['score'] } {
  const counted = rows.filter((r) => r.va != null && r.vb != null)
  const score = { a: counted.filter((r) => r.winner === 'a').length, b: counted.filter((r) => r.winner === 'b').length, counted: counted.length }
  const diff = Math.abs(score.a - score.b)
  let winner: Side | null = score.a > score.b ? 'a' : score.b > score.a ? 'b' : null
  let confidence: Confidence = diff >= 3 ? 'clear' : diff >= 1 ? 'narrow' : 'too close'
  const shares = rows.find((r) => r.key === 'points')!
  if (!winner) {
    winner = better(shares.va, shares.vb, 0.03)                 // level on categories: the points share, if clearly apart
    confidence = winner ? 'narrow' : 'too close'
  }
  const w = winner === 'a' ? a : b
  return {
    score,
    verdict: {
      winner, confidence,
      headline: winner ? `${label(w)} had the ${confidence === 'clear' ? 'stronger' : 'slightly stronger'} season record` : 'Level on what they achieved',
      detail: `${score.a}–${score.b} across ${score.counted} records${score.a === score.b && winner ? `; the share of the maximum points decides it (${shares[winner === 'a' ? 'a' : 'b']})` : ''}.`,
    },
  }
}

export function fingerprintRows(a: CarRow, b: CarRow): Measure[] {
  const f = (c: CarRow) => c.fair?.fingerprint
  const z = (v: number | null | undefined) => (v == null ? '–' : `${signed(v)} on its field`)
  const kinds: [keyof NonNullable<CarRow['fair']>['fingerprint'], string, string][] = [
    ['slow', 'Slow circuits', 'Dominance score at the slowest third of circuits (by average qualifying speed): street tracks and tight layouts.'],
    ['medium', 'Medium-speed circuits', 'Dominance score at the middle third of circuits.'],
    ['fast', 'Fast circuits', 'Dominance score at the fastest third of circuits: long straights and fast corners.'],
    ['straights', 'Straight-line speed', 'Speed-trap readings in qualifying against the same race\'s field, in units of that field\'s spread.'],
  ]
  const rows: Measure[] = kinds.map(([k, lbl, why]) => ({
    key: k, label: lbl, why, a: z(f(a)?.[k]), b: z(f(b)?.[k]), va: f(a)?.[k] ?? null, vb: f(b)?.[k] ?? null, winner: null,
  }))
  const wear = (c: CarRow) => f(c)?.tyre_wear_s_per_lap ?? null
  const wearText = (v: number | null) => (v == null ? '–' : Math.abs(v) < 0.0005 ? 'same as the median team'
    : `${Math.abs(v).toFixed(3)} s/lap ${v < 0 ? 'less' : 'more'} wear than the median team`)
  rows.push({ key: 'tyres', label: 'Tyre wear', why: 'How much slower each lap got as the tyres aged, against the median team of the same race (fuel burn affects every car alike, so it cancels out).',
    a: wearText(wear(a)), b: wearText(wear(b)), va: wear(a) == null ? null : -wear(a)!, vb: wear(b) == null ? null : -wear(b)!, winner: null })
  const rq = (c: CarRow) => f(c)?.race_vs_quali ?? null
  const rqText = (v: number | null) => (v == null ? '–' : Math.abs(v) < 0.15 ? 'as strong in races as in qualifying'
    : v > 0 ? `stronger in races (${signed(v)})` : `stronger in qualifying (${signed(v)})`)
  rows.push({ key: 'raceday', label: 'Race day vs qualifying', why: 'Its race-pace dominance minus its qualifying dominance: character, not quality, so it is not judged.',
    a: rqText(rq(a)), b: rqText(rq(b)), va: rq(a), vb: rq(b), winner: null })
  const spread = (c: CarRow) => f(c)?.consistency_spread ?? null
  rows.push({ key: 'consistency', label: 'Consistency', why: 'How much its dominance varied from race to race: lower is steadier.',
    a: spread(a) == null ? '–' : `varied by ${spread(a)!.toFixed(2)}`, b: spread(b) == null ? '–' : `varied by ${spread(b)!.toFixed(2)}`,
    va: spread(a) == null ? null : -spread(a)!, vb: spread(b) == null ? null : -spread(b)!, winner: null })
  const mech = (c: CarRow) => f(c)?.mechanical_per_start ?? null
  rows.push({ key: 'mechanical', label: 'Mechanical failures', why: 'Retirements with a mechanical cause per start; crashes are not held against the car.',
    a: mech(a) == null ? '–' : `${a.metrics.mechanical} in ${a.metrics.starts} starts`, b: mech(b) == null ? '–' : `${b.metrics.mechanical} in ${b.metrics.starts} starts`,
    va: mech(a) == null ? null : -mech(a)!, vb: mech(b) == null ? null : -mech(b)!, winner: null })
  return rows.map((r) => ({ ...r, winner: r.key === 'raceday' ? null : better(r.va, r.vb, r.key === 'tyres' ? 0.0005 : 0.05) }))
}

/** How much the drivers shaped the result: the points split and the teammate gap. */
export function driverFactor(c: CarRow): string {
  const ds = (c.fair?.drivers ?? []).filter((d) => d.races > 0).sort((x, y) => y.points - x.points)
  if (ds.length < 2) return 'One driver all season.'
  const total = ds.reduce((s, d) => s + d.points, 0)
  const surname = (n: string | null) => (n ?? '?').split(' ').slice(-1)[0]
  if (!total) return `${ds.map((d) => surname(d.name)).join(' and ')} scored no points.`
  const top = ds[0]
  const share = top.points / total
  const lead = share >= 0.7 ? 'carried the team' : share >= 0.58 ? 'led the team' : 'shared the load evenly'
  return `${surname(top.name)} ${lead}: ${pct(share)} of the team's ${Math.round(total)} points.`
}

export function compareCars(a: CarRow, b: CarRow, mode: Mode = 'car'): Comparison {
  const sameSeason = a.season === b.season
  const ea = a.fair?.era, eb = b.fair?.era
  const sameEra = !!ea && !!eb && ea.code === eb.code
  const eraNote = sameSeason
    ? `Same season: these two raced each other, so their head-to-head counts too.`
    : sameEra
      ? `Same rules (${ea!.label}: ${ea!.rules}), different seasons: still measured against each car's own field, as the field moved on.`
      : `Different eras: ${a.season} under the ${ea?.label ?? 'its'} (${ea?.rules ?? '–'}), ${b.season} under the ${eb?.label ?? 'its'} (${eb?.rules ?? '–'}). Raw lap times cannot be compared, so every measure is against each car's own season.`
  const capRows = capabilityRows(a, b, mode)
  const capVerdict = capabilityVerdict(a, b, mode)
  const { rows: achRows, info } = achievementRows(a, b)
  const { verdict: achVerdict, score } = achievementVerdict(a, b, achRows)
  const fingerprint = fingerprintRows(a, b)
  return {
    sameSeason, sameEra, eraNote,
    capability: { rows: capRows, verdict: capVerdict },
    achievement: { rows: achRows, info, verdict: achVerdict, score },
    fingerprint,
    report: report(a, b, capVerdict, achVerdict, fingerprint),
  }
}

function report(a: CarRow, b: CarRow, cap: Verdict, ach: Verdict, fingerprint: Measure[]): string[] {
  const out: string[] = [cap.detail]
  const pick = (s: Side) => (s === 'a' ? a : b)
  // converting pace into results
  for (const c of [a, b]) {
    const r = c.fair?.pace_rank
    if (!r) continue
    const pos = c.results.position
    if (r[0] === 1 && pos === 1) out.push(`${label(c)}: the fastest car of its season, and champions.`)
    else if (pos < r[0]) out.push(`${label(c)} finished P${pos} with only the ${ordinal(r[0])}-fastest car: more out of it than its pace.`)
    else if (pos > r[0]) out.push(`${label(c)} had the ${r[0] === 1 ? 'fastest' : `${ordinal(r[0])}-fastest`} car but finished P${pos}: points left on the table.`)
  }
  if (ach.winner && cap.winner && ach.winner !== cap.winner) {
    out.push(`The more dominant car is not the one with the stronger record: ${label(pick(ach.winner))} made more of what it had.`)
  }
  // character
  const strong = (side: Side) => fingerprint.filter((r) => ['slow', 'medium', 'fast'].includes(r.key))
    .map((r) => ({ label: r.label.toLowerCase(), v: side === 'a' ? r.va : r.vb })).filter((x) => x.v != null)
    .sort((x, y) => (y.v as number) - (x.v as number))[0]
  for (const side of ['a', 'b'] as const) {
    const s = strong(side)
    if (s) out.push(`${shortCar(pick(side))} was at its best on ${s.label}.`)
  }
  for (const c of [a, b]) out.push(`${shortCar(c)}: ${driverFactor(c)}`)
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

export interface SameCircuit {
  track_id: number
  track: string
  a: number
  b: number
  winner: Side | null
}

/** Circuits both cars raced at, each car against its own field there (a circuit raced twice in a
    season counts as the mean of its visits). */
export function sameCircuits(ra: CarRace[], rb: CarRace[]): SameCircuit[] {
  const mean = (rs: CarRace[]) => {
    const m = new Map<number, { track: string; v: number[] }>()
    for (const r of rs) {
      if (r.track_id == null) continue
      const e = m.get(r.track_id) ?? { track: r.track ?? '?', v: [] }
      e.v.push(r.z)
      m.set(r.track_id, e)
    }
    return new Map([...m].map(([k, e]) => [k, { track: e.track, z: e.v.reduce((s, x) => s + x, 0) / e.v.length }]))
  }
  const A = mean(ra), B = mean(rb)
  return [...A].filter(([k]) => B.has(k)).map(([k, x]) => {
    const y = B.get(k)!
    return { track_id: k, track: x.track, a: x.z, b: y.z, winner: better(x.z, y.z, 0.1) }
  }).sort((p, q) => p.track.localeCompare(q.track))
}

/** The development curve: dominance by race over the season's progress (0 = first race, 1 = last). */
export function curve(races: CarRace[]): { x: number; y: number; round: number; track: string | null }[] {
  if (!races.length) return []
  const rounds = races.map((r) => r.round)
  const lo = Math.min(...rounds), hi = Math.max(...rounds)
  return races.map((r) => ({ x: hi === lo ? 0 : (r.round - lo) / (hi - lo), y: r.z, round: r.round, track: r.track }))
}

/** Every car by dominance in its era (car only), unmeasured ones last. */
export function fairRanking(cars: CarRow[]): CarRow[] {
  const z = (c: CarRow) => c.fair?.dominance.car_only?.z ?? -Infinity
  return [...cars].sort((a, b) => z(b) - z(a) || b.season - a.season)
}

/** The two cars to show: from the link, else the two most dominant of all. */
export function pickPair(cars: CarRow[], ranked: CarRow[], a: string | null, b: string | null): [CarRow | null, CarRow | null] {
  const byKey = new Map(cars.map((c) => [c.key, c]))
  const first = (a && byKey.get(a)) || ranked[0] || null
  const second = (b && b !== first?.key && byKey.get(b)) || ranked.find((c) => c.key !== first?.key) || null
  return [first, second]
}
