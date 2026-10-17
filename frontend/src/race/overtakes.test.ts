import assert from 'node:assert/strict'
import { test } from 'node:test'

import type { Overtakes, OvertakesDriver } from '../types.ts'
import { byPasses, headlines, signed } from './overtakes.ts'

const d = (id: number, made: number, lost: number, start: number | null, finish: number): OvertakesDriver => ({
  driver_id: id, driver_name: `D${id}`, team_name: 'T', color_hex: null, grid: id, finish, classified: true, made, lost, start_gain: start,
})

const data = {
  race_id: 1, total_overtakes: 5, per_lap: [0, 2, 0, 3], overtakes: [],
  battles: [{ attacker: 2, defender: 1, from_lap: 2, to_lap: 9, laps: 8, closest: 0.3, changes: 0, passed_on_lap: null }],
  drivers: [d(1, 1, 2, -1, 1), d(2, 3, 0, 4, 2), d(3, 3, 1, 0, 3), d(4, 0, 2, null, 4)],
} as Overtakes

test('headlines: most passes, best start, longest battle, busiest lap', () => {
  const h = headlines(data)
  assert.equal(h.most?.driver_id, 2)
  assert.equal(h.bestStart?.driver_id, 2)
  assert.equal(h.longest?.laps, 8)
  assert.deepEqual(h.busiest, { lap: 4, n: 3 })
  const quiet = headlines({ ...data, per_lap: [0, 0], battles: [], drivers: [d(1, 0, 0, 0, 1)] })
  assert.equal(quiet.most, null)
  assert.equal(quiet.bestStart, null)
  assert.equal(quiet.busiest, null)
})

test('driver order: most passes, then fewest lost', () => {
  assert.deepEqual(byPasses(data.drivers).map((x) => x.driver_id), [2, 3, 1, 4])
  assert.equal(signed(3), '+3')
  assert.equal(signed(-2), '−2')
  assert.equal(signed(0), '0')
  assert.equal(signed(null), '–')
})
