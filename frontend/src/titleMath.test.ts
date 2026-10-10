import assert from 'node:assert/strict'
import { test } from 'node:test'

import { pointsSystem, titleMath } from './titleMath.ts'

test('points systems by season', () => {
  assert.deepEqual(pointsSystem(2026, 'drivers'), { race: 25, sprint: 8 })
  assert.deepEqual(pointsSystem(2024, 'drivers'), { race: 26, sprint: 8 })      // fastest-lap point
  assert.deepEqual(pointsSystem(2026, 'constructors'), { race: 43, sprint: 15 })
  assert.deepEqual(pointsSystem(2021, 'drivers'), { race: 26, sprint: 3 })
})

test('who can still win, and whether it is decided', () => {
  const s = [{ id: 1, name: 'A', points: 334 }, { id: 2, name: 'B', points: 305 }, { id: 3, name: 'C', points: 140 }]
  // 7 races and 1 sprint left in 2026: 7*25 + 8 = 183 available
  const m = titleMath(s, 7, 1, pointsSystem(2026, 'drivers'), true)
  assert.equal(m.available, 183)
  assert.deepEqual(m.rows.map((r) => r.alive), [true, true, false])   // C: 140 + 183 = 323 < 334
  assert.equal(m.decided, false)
  assert.equal(m.canClinchNext, false)
  // one race left, lead of 30 > 25 available: decided
  const d = titleMath([{ id: 1, name: 'A', points: 400 }, { id: 2, name: 'B', points: 370 }], 1, 0, pointsSystem(2026, 'drivers'), false)
  assert.equal(d.decided, true)
  // two races left, lead 26: 26 + 25 > 50 - 25 -> can clinch next time out
  const c = titleMath([{ id: 1, name: 'A', points: 300 }, { id: 2, name: 'B', points: 274 }], 2, 0, pointsSystem(2026, 'drivers'), false)
  assert.equal(c.canClinchNext, true)
})
