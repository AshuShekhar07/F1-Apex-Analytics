import assert from 'node:assert/strict'
import { test } from 'node:test'

import { carState, crossingTimes, lapPath, neutralisedOn, pointAt, replayEnd, runningOrder } from './replay.ts'
import type { RaceTrace, RaceTraceDriver } from '../types.ts'

const driver = (id: number, trace: (number | null)[]): RaceTraceDriver => ({
  driver_id: id, driver_name: `D${id}`, team_name: null, color_hex: null, finish: id, grid: id, status: 'Finished',
  classified: true, pits: [], gap: trace.map(() => 0), trace,
})

test('crossing times from the trace: reference pace minus how far ahead of it the car was', () => {
  // reference lap 90 s; the car 2 s behind the reference at the end of lap 2, 1 s ahead at lap 3
  assert.deepEqual(crossingTimes(driver(1, [0, -2, 1]), 90), [0, 92, 179])
})

test('where a car is between two crossings, and before and after its timing', () => {
  const times = [0, 90, 180]
  assert.deepEqual(carState(times, 45, 3, 90), { laps: 1, frac: 0.5, progress: 1.5, finished: false })
  assert.deepEqual(carState(times, 200, 3, 90), { laps: 3, frac: 0, progress: 3, finished: true })    // took the flag
  assert.equal(carState([0, 90], 200, 3, 90), null)                                               // retired after lap 2
  const late = carState([30, 120], 0, 3, 90)!                                                     // 30 s behind the leader
  assert.equal(late.laps, 0)
  assert.ok(Math.abs(late.frac - 2 / 3) < 1e-9)
  const gap = carState([0, null, 180], 135, 3, 90)!                                               // a timing gap
  assert.equal(gap.laps, 2)
  assert.ok(Math.abs(gap.frac - 0.5) < 1e-9)
})

test('running order: furthest round first, retired cars gone', () => {
  const cars = [{ id: 1, times: [0, 90, 180] }, { id: 2, times: [3, 95, 186] }, { id: 3, times: [5, 99] }]
  assert.deepEqual(runningOrder(cars, 60, 3, 90).map((r) => r.id), [1, 2, 3])
  assert.deepEqual(runningOrder(cars, 150, 3, 90).map((r) => r.id), [1, 2])
})

test('the lap path spends more of the lap in the slow sector', () => {
  // two straight sectors of 100 m each; the second at half the speed takes 2/3 of the lap
  const path = lapPath([[[0, 0], [100, 0]], [[100, 0], [200, 0]]], [200, 100])
  assert.ok(Math.abs(path.sectors[1][2] - 1 / 3) < 1e-9)
  const near = (p: [number, number], q: [number, number]) => assert.ok(Math.hypot(p[0] - q[0], p[1] - q[1]) < 1e-6, `${p} vs ${q}`)
  near(pointAt(path, 1 / 3), [100, 0])
  near(pointAt(path, 2 / 3), [150, 0])
  near(pointAt(path, 0), [0, 0])
})

test('replay length and neutralised laps', () => {
  const t: RaceTrace = { race_id: 1, season: 2026, round: 1, track: 'X', total_laps: 3, reference_lap: 90,
    neutralised: [{ kind: 'SC', start_lap: 2, end_lap: 3 }], drivers: [driver(1, [0, 0, 0]), driver(2, [-1, -2, -4])] }
  assert.equal(replayEnd(t), 184)
  assert.equal(neutralisedOn(t, 2), 'SC')
  assert.equal(neutralisedOn(t, 1), null)
})
