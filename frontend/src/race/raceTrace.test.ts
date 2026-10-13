import assert from 'node:assert/strict'
import { test } from 'node:test'

import type { RaceTrace, RaceTraceDriver } from '../types.ts'
import { axis, dashedIds, formatTick, formatValue, neutralAt, orderAt, presetIds } from './raceTrace.ts'

function driver(id: number, team: string, gap: (number | null)[], trace: (number | null)[] = gap.map((g) => (g == null ? null : -g))): RaceTraceDriver {
  return { driver_id: id, driver_name: `Driver ${id}`, team_name: team, color_hex: null, finish: id, grid: id, status: 'Finished',
    classified: true, pits: [], gap, trace }
}

const field = [
  driver(1, 'Red', [0, 0, 0]),
  driver(2, 'Blue', [1.2, 0.8, 2.5]),
  driver(3, 'Red', [2.0, 4.0, 1.5]),
  driver(4, 'Blue', [3.1, null]),
]

test('presets take the first N finishers; a team\'s second car is dashed over the whole field', () => {
  assert.deepEqual(presetIds(field, 'top5'), [1, 2, 3, 4])
  assert.deepEqual(presetIds(field.concat(field.map((d) => ({ ...d, driver_id: d.driver_id + 10 }))), 'top5'), [1, 2, 3, 4, 11])
  assert.deepEqual([...dashedIds(field)], [3, 4])
})

test('axis: gap from the leader down, trace up to at least zero, round ticks', () => {
  assert.deepEqual(axis(field, 'gap'), { lo: 0, hi: 4, ticks: [0, 1, 2, 3, 4] })
  const t = axis(field, 'trace')
  assert.equal(t.hi, 0)
  assert.equal(t.lo, -4)
  assert.deepEqual(axis([], 'gap', 5).ticks.slice(0, 2), [0, 0.2])
})

test('running order on a lap: by gap, with the interval to the car ahead and pit stops', () => {
  const withPit = field.map((d) => (d.driver_id === 3 ? { ...d, pits: [{ lap: 2, compound: 'HARD' }] } : d))
  const lap2 = orderAt(withPit, 'gap', 2)
  assert.deepEqual(lap2.map((r) => r.driver.driver_id), [1, 2, 3])           // car 4 has no time on lap 2
  assert.deepEqual(lap2.map((r) => r.interval), [null, 0.8, 3.2])
  assert.deepEqual(lap2[2].pitted, { lap: 2, compound: 'HARD' })
  const lap3 = orderAt(withPit, 'trace', 3)                                   // trace values, gap order
  assert.deepEqual(lap3.map((r) => [r.driver.driver_id, r.value]), [[1, -0], [3, -1.5], [2, -2.5]])
})

test('Safety Car laps and labels', () => {
  const trace = { neutralised: [{ kind: 'VSC', start_lap: 2, end_lap: 3 }, { kind: 'SC', start_lap: 3, end_lap: 5 }] } as unknown as RaceTrace
  assert.equal(neutralAt(trace, 1), null)
  assert.equal(neutralAt(trace, 2), 'VSC')
  assert.equal(neutralAt(trace, 3), 'SC')
  assert.equal(formatValue(0, 'gap'), 'Leader')
  assert.equal(formatValue(1.23456, 'gap'), '+1.235')
  assert.equal(formatValue(-12.34, 'trace'), '−12.3s')
  assert.equal(formatTick(2.5, 'gap'), '+2.5s')
  assert.equal(formatTick(-20, 'trace'), '−20s')
})
