import assert from 'node:assert/strict'
import { test } from 'node:test'

import { compoundLetter, evidenceLabel, parseLaps, pct, planStints, positionChange } from './format.ts'

test('parseLaps expands ranges, dedupes and sorts', () => {
  assert.deepEqual(parseLaps('30, 10-12,11', 52), [10, 11, 12, 30])
  assert.deepEqual(parseLaps('  ', 52), [])
  assert.throws(() => parseLaps('60', 52), /between 1 and 52/)
  assert.throws(() => parseLaps('12-10', 52))
  assert.throws(() => parseLaps('abc', 52))
})

test('planStints splits the race at the stop laps', () => {
  assert.deepEqual(planStints(['MEDIUM', 'HARD'], [17], 52), [
    { compound: 'MEDIUM', start_lap: 1, end_lap: 17 },
    { compound: 'HARD', start_lap: 18, end_lap: 52 },
  ])
})

test('formatting helpers', () => {
  assert.equal(pct(0.6333), '63%')
  assert.equal(pct(0.001), '<1%')
  assert.equal(pct(0), '0%')
  assert.equal(compoundLetter('intermediate'), 'I')
  assert.equal(positionChange(-1.24).tone, 'up')
  assert.equal(positionChange(0.8).label, '▼ 0.8')
  assert.equal(positionChange(0.01).tone, 'flat')
})

test('evidenceLabel explains where the strategy came from', () => {
  assert.equal(evidenceLabel('era(n=703)'), '703 earlier cars in this regulation era')
  assert.equal(evidenceLabel('era_band(n=173), start=MEDIUM'),
    '173 earlier cars from similar grid slots in this regulation era that started on medium')
  assert.equal(evidenceLabel('weird'), 'weird')
})
