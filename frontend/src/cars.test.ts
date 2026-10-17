import assert from 'node:assert/strict'
import { test } from 'node:test'

import { driverLine, liveryColours, metricLines, overallBand, ranking } from './cars.ts'
import type { CarRow } from './types.ts'

const car = (over: Partial<CarRow> = {}): CarRow => ({
  key: '2020-mercedes', season: 2020, team_id: 10, team_name: 'Mercedes', color_hex: '#27F4D2', car: 'Mercedes-AMG F1 W11 EQ Performance',
  pu: 'Mercedes', livery: 'Black', notes: [], palette: ['#151515', '#c4c8cc', '#00d2be'], wiki: null,
  drivers: [{ id: 1, name: 'Lewis Hamilton', races: 16 }, { id: 2, name: 'Valtteri Bottas', races: 17 }, { id: 3, name: 'George Russell', races: 1 }],
  results: { position: 1, points: 573, races: 17, season_races: 17, wins: 13, poles: 15, podiums: 25, one_twos: 5 },
  metrics: { quali_gap_pct: -1.35, race_gap_pct: -1.1, top_speed_kmh: 2.4, finish_rate: 0.94, starts: 34, finishes: 32, incidents: 0,
    mechanical: 2, retired_unknown: 0, quickest_in_qualifying: 15, quickest_in_race: 14, quali_races: 17, race_races: 17, speed_races: 17 },
  ratings: { overall: 98, quali: 99, race: 100, speed: 80, reliability: 90 },
  character: { kind: 'balanced', label: 'Balanced', text: '' },
  season_best: { quali: 0.62, race: 0.4 },
  ...over,
})

test('the numbers behind the ratings read as sentences', () => {
  const lines = metricLines(car())
  assert.equal(lines[0], 'Qualifying: 1.35% quicker than the median team (median of 17 races); quickest car at 15.')
  assert.equal(lines[1], 'The quickest car of 2020 over a lap, 0.62% clear of the next.')
  assert.ok(lines.some((l) => l === 'Top speed: 2.4 km/h above the median team in qualifying (17 races).'))
  assert.ok(lines.some((l) => l === 'Reliability: finished 94% of 34 starts; retirements: 2 mechanical.'))
  const slow = metricLines(car({ season_best: {}, metrics: { ...car().metrics, quali_gap_pct: 0.8, quickest_in_qualifying: 0, top_speed_kmh: null } }))
  assert.equal(slow[0], 'Qualifying: 0.80% slower than the median team (median of 17 races).')
  assert.ok(!slow.some((l) => l.startsWith('Top speed')))
})

test('drivers, bands, ranking and colours', () => {
  assert.equal(driverLine(car()), 'Hamilton, Bottas (also Russell 1)')
  assert.deepEqual([overallBand(95), overallBand(70), overallBand(40), overallBand(10), overallBand(null)],
    ['Elite', 'Front-runner', 'Midfield', 'Backmarker', 'Not rated'])
  const r = ranking([car({ key: 'a', ratings: { ...car().ratings, overall: 50 } }), car({ key: 'b' }), car({ key: 'c', ratings: { ...car().ratings, overall: null } })])
  assert.deepEqual(r.map((c) => c.key), ['b', 'a', 'c'])
  assert.deepEqual(liveryColours(car()), ['#151515', '#c4c8cc', '#00d2be'])
  assert.deepEqual(liveryColours(car({ palette: null, color_hex: 'bad' })), ['#888888', '#1a1a1a', '#ffffff'])
})
