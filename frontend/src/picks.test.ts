import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  closesAt, fitOrder, isClosed, isLocked, moveBy, moveTo, orderPoints, scoreOrder, startingOrder, validChampionPick, validRacePick,
  withOrder, withWinner, type RacePick,
} from './picks.ts'

const pick: RacePick = { v: 1, raceId: 191, season: 2026, round: 18, track: 'Austin', raceDate: '2026-10-25',
  winner: null, order: null, lockedAt: null, savedAt: '2026-10-20T10:00:00Z' }

test('picks close at 00:00 UTC on race day', () => {
  assert.equal(closesAt('2026-10-25').toISOString(), '2026-10-25T00:00:00.000Z')
  assert.equal(isClosed('2026-10-25', new Date('2026-10-24T23:59:59Z')), false)
  assert.equal(isClosed('2026-10-25', new Date('2026-10-25T00:00:00Z')), true)
  assert.equal(isLocked(pick, new Date('2026-10-24T12:00:00Z')), false)
  assert.equal(isLocked({ ...pick, lockedAt: '2026-10-24T12:00:00Z' }, new Date('2026-10-24T12:00:01Z')), true)
  assert.equal(isLocked(pick, new Date('2026-10-25T13:00:00Z')), true)        // closed: whatever was saved counts
  assert.equal(isLocked(null, new Date()), false)
})

test('reordering a finishing order', () => {
  assert.deepEqual(moveTo([1, 2, 3, 4], 4, 0), [4, 1, 2, 3])
  assert.deepEqual(moveTo([1, 2, 3, 4], 1, 2), [2, 3, 1, 4])
  assert.deepEqual(moveTo([1, 2, 3, 4], 2, 99), [1, 3, 4, 2])
  assert.deepEqual(moveBy([1, 2, 3, 4], 3, -1), [1, 3, 2, 4])
  assert.deepEqual(moveBy([1, 2, 3, 4], 1, -1), [1, 2, 3, 4])                    // already P1
  assert.deepEqual(moveBy([1, 2, 3, 4], 9, 1), [1, 2, 3, 4])
})

test('the winner and the order stay in step', () => {
  const ordered = withOrder(pick, [5, 1, 2])
  assert.equal(ordered.winner, 5)
  const changed = withWinner(ordered, 2)
  assert.deepEqual(changed.order, [2, 5, 1])
  assert.equal(withWinner(pick, 7).order, null)
})

test('the starting order: grid first, else the championship', () => {
  const lineup = [{ driver_id: 1, grid: 3 }, { driver_id: 2, grid: 1 }, { driver_id: 3, grid: 2 }]
  assert.deepEqual(startingOrder(lineup, [1, 2, 3]), [2, 3, 1])
  const noGrid = lineup.map((d) => ({ ...d, grid: null }))
  assert.deepEqual(startingOrder(noGrid, [3, 9, 1]), [3, 1, 2])                  // 9 is not racing; 2 has no points yet
})

test('scoring an order against the result', () => {
  const finishers = [
    { driverId: 2, position: 1, classified: true }, { driverId: 1, position: 2, classified: true },
    { driverId: 3, position: 3, classified: true }, { driverId: 4, position: 4, classified: false },
  ]
  const s = scoreOrder([1, 2, 3, 5], finishers)
  assert.equal(s.exact, 1)                     // 3 in P3
  assert.equal(s.withinOne, 3)
  assert.equal(s.podium, 3)                    // all three podium finishers in the top 3, other order
  assert.equal(s.compared, 3)                  // driver 5 did not race
  assert.equal(s.avgMiss, 2 / 3)
  assert.equal(s.winnerRight, false)
  assert.deepEqual(s.rows.map((r) => r.diff), [1, -1, 0, null])
  assert.equal(scoreOrder([2], finishers).winnerRight, true)
  assert.equal(scoreOrder([], []).avgMiss, null)
})

test('points from an order', () => {
  const pts = orderPoints([7, 8, 9], [25, 18])
  assert.deepEqual([...pts.entries()], [[7, 25], [8, 18], [9, 0]])
})

test('saved picks are checked before use, and an order follows line-up changes', () => {
  assert.equal(validRacePick(pick), true)
  assert.equal(validRacePick({ ...pick, order: 'x' }), false)
  assert.equal(validRacePick({ ...pick, raceDate: 'soon' }), false)
  assert.equal(validRacePick(null), false)
  assert.equal(validChampionPick({ v: 1, season: 2026, teamId: 3, afterRound: 17, lockedAt: null, savedAt: '' }), true)
  assert.equal(validChampionPick({ v: 1, season: 2026 }), false)
  assert.deepEqual(fitOrder([1, 2, 3], [3, 1, 4]), [1, 3, 4])      // 2 replaced by 4
})
