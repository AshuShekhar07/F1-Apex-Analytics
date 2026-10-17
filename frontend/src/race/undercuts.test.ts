import assert from 'node:assert/strict'
import { test } from 'node:test'

import type { UndercutDuel } from '../types.ts'
import { biggestGain, duelScale, gapWords, verdict } from './undercuts.ts'

const duel = (kind: 'undercut' | 'overcut', worked: boolean, before: number, after: number): UndercutDuel => ({
  first: 1, second: 2, first_lap: 18, second_lap: 21, first_compound: 'HARD', second_compound: 'HARD',
  gap_before: before, gap_after: after, gain: before - after, kind, worked, for_position: 3,
})

test('verdicts name who gained', () => {
  assert.equal(verdict(duel('undercut', true, 1.2, -0.4), 'NOR', 'LEC').title, 'Undercut worked')
  assert.equal(verdict(duel('undercut', false, 1.2, 0.3), 'NOR', 'LEC').detail, 'NOR pitted first but LEC stayed ahead')
  assert.equal(verdict(duel('overcut', true, -0.8, 0.5), 'NOR', 'LEC').detail, 'LEC stayed out longer and got ahead of NOR')
  assert.equal(verdict(duel('overcut', false, -0.8, -1.5), 'NOR', 'LEC').title, 'Covered')
})

test('gap words and the shared scale', () => {
  assert.equal(gapWords(1.84), '1.8s behind')
  assert.equal(gapWords(-0.62), '0.6s ahead')
  assert.equal(gapWords(0.01), 'level')
  const s = duelScale([duel('undercut', true, 2, -1), duel('overcut', false, -0.5, -4)])
  assert.equal(s.max, 4)
  assert.equal(s.pct(0), 50)
  assert.equal(s.pct(4), 0)            // 4 s behind: far left
  assert.equal(s.pct(-4), 100)         // 4 s ahead: far right
  assert.equal(biggestGain([duel('undercut', true, 2, -1), duel('overcut', false, -0.5, -4)])?.gain, 3.5)
  assert.equal(biggestGain([]), null)
})
