import assert from 'node:assert/strict'
import { test } from 'node:test'

import { compareCars, headToHead, pickPair, shortCar } from './compareCars.ts'
import type { CarRow, SeasonResults } from './types.ts'

const base: CarRow = {
  key: 'x', season: 2020, team_id: 10, team_name: 'Mercedes', color_hex: '#27F4D2', car: 'Mercedes-AMG F1 W11 EQ Performance',
  pu: 'Mercedes', livery: null, notes: [], palette: null, wiki: null, drivers: [],
  results: { position: 1, points: 573, races: 17, season_races: 17, wins: 13, poles: 15, podiums: 25, one_twos: 5 },
  metrics: { quali_gap_pct: -1.35, race_gap_pct: -1.1, top_speed_kmh: 2.4, finish_rate: 0.94, starts: 34, finishes: 32, incidents: 0,
    mechanical: 2, retired_unknown: 0, quickest_in_qualifying: 15, quickest_in_race: 14, quali_races: 17, race_races: 17, speed_races: 17 },
  ratings: { overall: 98, quali: 100, race: 97, speed: 70, reliability: 80 },
  character: { kind: 'balanced', label: 'Balanced', text: '' }, season_best: { quali: 0.62 },
}
type Over = Omit<Partial<CarRow>, 'results' | 'metrics'> & { results?: Partial<CarRow['results']>; metrics?: Partial<CarRow['metrics']> }
const car = (over: Over): CarRow => ({
  ...base, ...over, results: { ...base.results, ...over.results }, metrics: { ...base.metrics, ...over.metrics },
})
const w11 = car({ key: '2020-mercedes' })
const rb19 = car({
  key: '2023-red-bull-racing', season: 2023, team_id: 2, team_name: 'Red Bull Racing', car: 'Red Bull Racing RB19',
  results: { position: 1, points: 860, races: 22, season_races: 22, wins: 21, poles: 14, podiums: 30, one_twos: 8 },
  metrics: { quali_gap_pct: -1.0, race_gap_pct: -1.6, finish_rate: 0.98, top_speed_kmh: 5.1 },
  ratings: { overall: 99, quali: 96, race: 100, speed: 92, reliability: 95 },
  character: { kind: 'low-drag', label: 'Low drag', text: '' }, season_best: { quali: 0.3, race: 0.9 },
})

test('short names for sentences', () => {
  assert.equal(shortCar(w11), 'Mercedes W11')
  assert.equal(shortCar(rb19), 'Red Bull RB19')
  assert.equal(shortCar(car({ team_name: 'Racing Bulls', car: 'Racing Bulls VCARB 02' })), 'Racing Bulls VCARB 02')
  assert.equal(shortCar(car({ team_name: 'Haas F1 Team', car: 'Haas VF-24' })), 'Haas VF-24')
  assert.equal(shortCar(car({ team_name: 'Ferrari', car: 'Ferrari F1-75' })), 'Ferrari F1-75')
  assert.equal(shortCar(car({ team_name: 'Team Alpha', car: null })), 'Team Alpha')
})

test('the verdict counts the categories each car wins', () => {
  const c = compareCars(w11, rb19)
  const won = Object.fromEntries(c.rows.map((r) => [r.key, r.winner]))
  // W11: quicker over a lap, more poles and podiums per race; RB19: race pace, wins, one-twos, reliability; titles level
  assert.equal(won.quali, 'a')
  assert.equal(won.poles, 'a')                  // 15/17 against 14/22
  assert.equal(won.podiums, 'a')                // 25/17 = 1.47 a race against 30/22 = 1.36
  assert.equal(won.race, 'b')
  assert.equal(won.wins, 'b')
  assert.equal(won.title, null)
  assert.equal(won.speed, null)                 // character, not quality
  assert.deepEqual(c.score, { a: 3, b: 4, counted: 8 })
  assert.equal(c.winner, 'b')
  assert.equal(c.decidedBy, 'categories')
  assert.equal(c.report[0], 'Red Bull RB19 (2023) is the better car: it wins 4 of the 8 categories against Mercedes W11 (2020)\'s 3.')
  assert.ok(c.report.includes("Both were constructors' champions."))
  assert.ok(c.report.some((s) => s.startsWith('They never raced each other')))
  assert.ok(c.report.some((s) => s === 'Over one lap Mercedes W11 (2020) was further ahead of its field: 1.35% quicker than the median team, against 1.00% quicker.'))
})

test('a draw on categories goes to the overall rating, else too close to call', () => {
  const twin = car({ key: 'twin', season: 2021 })
  const c = compareCars(w11, { ...twin, ratings: { ...twin.ratings, overall: 90 } })
  assert.deepEqual([c.score.a, c.score.b], [0, 0])
  assert.equal(c.winner, 'a')
  assert.equal(c.decidedBy, 'overall rating')
  const same = compareCars(w11, twin)
  assert.equal(same.winner, null)
  assert.ok(same.report[0].startsWith('Too close to call'))
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
