import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  capabilityVerdict, compareCars, curve, driverFactor, fairRanking, headToHead, marginText, pickPair, sameCircuits, shortCar,
} from './compareCars.ts'
import type { CarFair, CarRace, CarRow, SeasonResults } from './types.ts'

const fair = (over: Partial<CarFair> = {}): CarFair => ({
  era: { code: 'era1_13inch', label: '2017-2021 rules', rules: '13-inch wheels' },
  pace_rank: [1, 10],
  dominance: { car_only: { z: 1.8, se: 0.1, races: 17, spread: 0.4 }, with_drivers: { z: 1.9, se: 0.1, races: 17, spread: 0.4 } },
  margin: { quali_pct: 0.62, race_pct: 0.5, quali_pct_with_drivers: 0.7, race_pct_with_drivers: 0.55 },
  model: { win_share: 0.54, rank: 1, teams: 10 },
  fingerprint: { slow: 1.2, medium: 1.8, fast: 2.1, straights: 0.9, tyre_wear_s_per_lap: -0.01, race_vs_quali: 0.1,
    consistency_spread: 0.4, mechanical_per_start: 0.06 },
  achievements: { points_share_of_max: 0.75, points_share_of_field: 0.33, win_rate: 0.76, pole_rate: 0.88, podiums_per_race: 1.47,
    one_two_rate: 0.29, front_row_lockouts: 9, points_ceiling: 765, sprints: 0 },
  drivers: [
    { id: 1, name: 'Lewis Hamilton', races: 16, points: 347, quali_h2h: [10, 6], race_h2h: [11, 4], skill_rank: [1, 20] },
    { id: 2, name: 'Valtteri Bottas', races: 17, points: 223, quali_h2h: [6, 10], race_h2h: [4, 11], skill_rank: [6, 20] },
  ],
  ...over,
})

const base: CarRow = {
  key: 'x', season: 2020, team_id: 10, team_name: 'Mercedes', color_hex: '#27F4D2', car: 'Mercedes-AMG F1 W11 EQ Performance',
  pu: 'Mercedes', livery: null, notes: [], palette: null, wiki: null, drivers: [],
  results: { position: 1, points: 573, races: 17, season_races: 17, wins: 13, poles: 15, podiums: 25, one_twos: 5 },
  metrics: { quali_gap_pct: -1.35, race_gap_pct: -1.1, top_speed_kmh: 2.4, finish_rate: 0.94, starts: 34, finishes: 32, incidents: 0,
    mechanical: 2, retired_unknown: 0, quickest_in_qualifying: 15, quickest_in_race: 14, quali_races: 17, race_races: 17, speed_races: 17 },
  ratings: { overall: 98, quali: 100, race: 97, speed: 70, reliability: 80 },
  character: { kind: 'balanced', label: 'Balanced', text: '' }, season_best: { quali: 0.62 },
  fair: fair(),
}
type Over = Omit<Partial<CarRow>, 'results'> & { results?: Partial<CarRow['results']> }
const car = (over: Over): CarRow => ({ ...base, ...over, results: { ...base.results, ...over.results } })
const w11 = car({ key: '2020-mercedes' })
const rb19 = car({
  key: '2023-red-bull-racing', season: 2023, team_id: 2, team_name: 'Red Bull Racing', car: 'Red Bull Racing RB19',
  results: { position: 1, points: 860, races: 22, season_races: 22, wins: 21, poles: 14, podiums: 30, one_twos: 8 },
  fair: fair({
    era: { code: 'era2_18inch_groundeffect', label: '2022-2025 rules', rules: 'ground-effect floors' },
    dominance: { car_only: { z: 1.2, se: 0.15, races: 22, spread: 0.7 }, with_drivers: { z: 2.1, se: 0.15, races: 22, spread: 0.7 } },
    model: { win_share: 0.48, rank: 1, teams: 10 },
    fingerprint: { slow: 0.8, medium: 1.3, fast: 1.5, straights: 1.6, tyre_wear_s_per_lap: -0.03, race_vs_quali: 0.6,
      consistency_spread: 0.7, mechanical_per_start: 0.02 },
    achievements: { points_share_of_max: 0.86, points_share_of_field: 0.38, win_rate: 0.95, pole_rate: 0.64, podiums_per_race: 1.36,
      one_two_rate: 0.36, front_row_lockouts: 5, points_ceiling: 1004, sprints: 6 },
    drivers: [
      { id: 3, name: 'Max Verstappen', races: 22, points: 575, quali_h2h: [20, 2], race_h2h: [19, 2], skill_rank: [1, 22] },
      { id: 4, name: 'Sergio Perez', races: 22, points: 285, quali_h2h: [2, 20], race_h2h: [2, 19], skill_rank: [12, 22] },
    ],
  }),
})

test('short names for sentences', () => {
  assert.equal(shortCar(w11), 'Mercedes W11')
  assert.equal(shortCar(rb19), 'Red Bull RB19')
  assert.equal(shortCar(car({ team_name: 'Racing Bulls', car: 'Racing Bulls VCARB 02' })), 'Racing Bulls VCARB 02')
  assert.equal(shortCar(car({ team_name: 'Haas F1 Team', car: 'Haas VF-24' })), 'Haas VF-24')
  assert.equal(shortCar(car({ team_name: 'Team Alpha', car: null })), 'Team Alpha')
})

test('capability: dominance against each own field, judged against the race-to-race noise', () => {
  // car only: 1.8 vs 1.2, noise sqrt(0.1² + 0.15²) = 0.18 -> 3.3x: clear, the W11
  const car = capabilityVerdict(w11, rb19, 'car')
  assert.equal(car.winner, 'a')
  assert.equal(car.confidence, 'clear')
  assert.equal(car.headline, 'Mercedes W11 (2020) was more dominant in its era')
  // car + drivers (the quicker car): 1.9 vs 2.1 -> 1.1x the noise: narrow, the RB19 with Verstappen
  const drivers = capabilityVerdict(w11, rb19, 'drivers')
  assert.equal(drivers.winner, 'b')
  assert.equal(drivers.confidence, 'narrow')
  // a difference inside the noise is too close to call
  const close = capabilityVerdict(w11, car_({ dominance: { car_only: { z: 1.75, se: 0.2, races: 10, spread: 1 }, with_drivers: null } }), 'car')
  assert.deepEqual([close.winner, close.confidence], [null, 'too close'])
  // missing data
  assert.equal(capabilityVerdict(w11, car_({ dominance: { car_only: null, with_drivers: null } }), 'car').confidence, 'too close')
})

function car_(f: Partial<CarFair>): CarRow {
  return car({ key: 'other', season: 2021, fair: fair(f) })
}

test('achievement: records normalised for the points systems, counted once each', () => {
  const c = compareCars(w11, rb19)
  const won = Object.fromEntries(c.achievement.rows.map((r) => [r.key, r.winner]))
  assert.deepEqual(won, { points: 'b', wins: 'b', poles: 'a', podiums: 'a', onetwos: 'b', title: null })
  assert.deepEqual(c.achievement.score, { a: 2, b: 3, counted: 6 })
  assert.equal(c.achievement.verdict.winner, 'b')
  assert.equal(c.achievement.verdict.confidence, 'narrow')
  // capability goes to the W11, the record to the RB19: the report says so
  assert.ok(c.report.some((s) => s.startsWith('The more dominant car is not the one with the stronger record: Red Bull RB19 (2023)')))
})

test('era banner and the fingerprint', () => {
  const c = compareCars(w11, rb19)
  assert.equal(c.sameEra, false)
  assert.ok(c.eraNote.startsWith('Different eras: 2020 under the 2017-2021 rules'))
  assert.ok(compareCars(w11, car({ key: 'w12', season: 2021 })).eraNote.startsWith('Same rules (2017-2021 rules'))
  assert.ok(compareCars(w11, car({ key: 'rb16', team_id: 3 })).eraNote.startsWith('Same season'))
  const fp = Object.fromEntries(c.fingerprint.map((r) => [r.key, r.winner]))
  assert.equal(fp.tyres, 'b')              // less wear than its field
  assert.equal(fp.raceday, null)           // character, not judged
  assert.equal(fp.straights, 'b')
  assert.equal(fp.consistency, 'a')        // varied less
})

test('wording', () => {
  assert.equal(marginText(0.62), '0.62% clear of the next car')
  assert.equal(marginText(-0.35), '0.35% behind the quickest')
  assert.equal(marginText(null), '–')
  assert.equal(driverFactor(rb19), "Verstappen led the team: 67% of the team's 860 points.")
  assert.equal(driverFactor(w11), "Hamilton led the team: 61% of the team's 570 points.")
})

const race = (round: number, track_id: number, z: number): CarRace =>
  ({ race_id: round, round, track_id, track: `T${track_id}`, country: null, kind: null, z, quali_gap_pct: null, race_gap_pct: null,
    quali_margin_pct: null, race_margin_pct: null })

test('same circuits, each car against its own field; a circuit raced twice is averaged', () => {
  const a = [race(1, 1, 2.0), race(2, 2, 1.0), race(3, 2, 0.6), race(4, 9, 1.5)]
  const b = [race(1, 2, 1.0), race(2, 1, 1.5), race(3, 3, 0.2)]
  const s = sameCircuits(a, b)
  assert.deepEqual(s.map((x) => [x.track, x.winner]), [['T1', 'a'], ['T2', 'b']])   // T2: (1.0 + 0.6) / 2 = 0.8 against 1.0
  const t2 = s.find((x) => x.track_id === 2)!
  assert.ok(Math.abs(t2.a - 0.8) < 1e-9)
  assert.equal(t2.winner, 'b')
  assert.equal(s.length, 2)
})

test('development curve over the season and the era-fair ranking', () => {
  const c = curve([race(3, 1, 0.5), race(5, 1, 1.0), race(7, 1, 1.5)])
  assert.deepEqual(c.map((p) => p.x), [0, 0.5, 1])
  const none = car({ key: 'n', fair: fair({ dominance: { car_only: null, with_drivers: null } }) })
  assert.deepEqual(fairRanking([rb19, none, w11]).map((x) => x.key), ['2020-mercedes', '2023-red-bull-racing', 'n'])
})

test('head to head in the same season: best classified car of each team', () => {
  const cell = (round: number, team: number, position: number | null, classified = true) =>
    ({ round, team_id: team, sprint: null, race: { position, grid: null, points: 0, status: null, classified, dnf: !classified } })
  const m = {
    season: 2024, latest_round: 3, note: '', teams: [],
    rounds: [1, 2, 3, 4].map((r) => ({ race_id: r, round: r, race_date: '', track_name: '', country: null, weekend_format: null, sprint: false, raced: r <= 3 })),
    drivers: [
      { id: 1, name: 'A1', number: null, team: null, points: 0, wins: 0, podiums: 0, results: [cell(1, 1, 1), cell(2, 1, 5), cell(3, 1, 18, false)] },
      { id: 2, name: 'A2', number: null, team: null, points: 0, wins: 0, podiums: 0, results: [cell(1, 1, 4), cell(2, 1, 7), cell(3, 1, 19, false)] },
      { id: 3, name: 'B1', number: null, team: null, points: 0, wins: 0, podiums: 0, results: [cell(1, 2, 2), cell(2, 2, 3), cell(3, 2, 20, false)] },
    ],
  } as unknown as SeasonResults
  assert.deepEqual(headToHead(m, 1, 2), { a: 1, b: 1, races: 2 })      // round 3: neither finished
})

test('the pair to show', () => {
  const ranked = [rb19, w11]
  assert.deepEqual(pickPair([w11, rb19], ranked, null, null).map((c) => c?.key), ['2023-red-bull-racing', '2020-mercedes'])
  assert.deepEqual(pickPair([w11, rb19], ranked, '2020-mercedes', null).map((c) => c?.key), ['2020-mercedes', '2023-red-bull-racing'])
  assert.deepEqual(pickPair([w11, rb19], ranked, '2020-mercedes', '2020-mercedes').map((c) => c?.key), ['2020-mercedes', '2023-red-bull-racing'])
  assert.deepEqual(pickPair([w11, rb19], ranked, 'nope', 'nope2').map((c) => c?.key), ['2023-red-bull-racing', '2020-mercedes'])
})
