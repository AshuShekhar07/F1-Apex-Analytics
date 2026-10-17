import assert from 'node:assert/strict'
import { test } from 'node:test'

import { racedRounds, standingsAfter } from './seasonReplay.ts'
import type { SeasonResults } from './types.ts'

const res = (position: number, points: number) => ({ position, grid: null, points, status: null, classified: true, dnf: false })
const m = {
  season: 2021, latest_round: 2, note: '',
  rounds: [1, 2, 3].map((r) => ({ race_id: r, round: r, race_date: '', track_name: '', country: null, weekend_format: null, sprint: r === 2, raced: r <= 2 })),
  teams: [{ id: 10, name: 'Red Bull', color_hex: '#3671C6', logo_url: null, points: 0, wins: 0, by_round: [] },
    { id: 20, name: 'Mercedes', color_hex: '#27F4D2', logo_url: null, points: 0, wins: 0, by_round: [] }],
  drivers: [
    { id: 1, name: 'Max', number: 33, team: { id: 10, name: 'Red Bull', color_hex: '#3671C6', logo_url: null }, points: 0, wins: 0, podiums: 0,
      results: [{ round: 1, team_id: 10, race: res(2, 18), sprint: null }, { round: 2, team_id: 10, race: res(1, 25), sprint: res(2, 2) }] },
    { id: 2, name: 'Lewis', number: 44, team: { id: 20, name: 'Mercedes', color_hex: '#27F4D2', logo_url: null }, points: 0, wins: 0, podiums: 0,
      results: [{ round: 1, team_id: 20, race: res(1, 25), sprint: null }, { round: 2, team_id: 20, race: res(2, 18), sprint: res(1, 3) }] },
    { id: 3, name: 'Checo', number: 11, team: { id: 10, name: 'Red Bull', color_hex: '#3671C6', logo_url: null }, points: 0, wins: 0, podiums: 0,
      results: [{ round: 1, team_id: 10, race: res(3, 15), sprint: null }, { round: 2, team_id: 10, race: res(5, 10), sprint: null }] },
  ],
} as unknown as SeasonResults

test('standings after each round, sprints included', () => {
  assert.deepEqual(racedRounds(m), [1, 2])
  assert.deepEqual(standingsAfter(m, 'drivers', 1).map((r) => [r.name, r.points]), [['Lewis', 25], ['Max', 18], ['Checo', 15]])
  assert.deepEqual(standingsAfter(m, 'drivers', 2).map((r) => [r.name, r.points]), [['Max', 45], ['Lewis', 46], ['Checo', 25]].sort((a, b) => (b[1] as number) - (a[1] as number)))
  assert.deepEqual(standingsAfter(m, 'constructors', 2).map((r) => [r.name, r.points]), [['Red Bull', 70], ['Mercedes', 46]])
})
