import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  compoundLetter,
  evidenceLabel,
  parseLaps,
  pct,
  planStints,
  pctLabel,
  positionChange,
  roundLabel,
  shortName,
  stageLabel,
  topTitleSeries,
} from './format.ts'

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

test('topTitleSeries keeps the leaders of the latest round, rounds in order', () => {
  const series = [
    { id: 1, name: 'A', rounds: [{ after_round: 2, p_title: 0.1 }, { after_round: 1, p_title: 0.5 }] },
    { id: 2, name: 'B', rounds: [{ after_round: 1, p_title: 0.3 }, { after_round: 2, p_title: 0.8 }] },
    { id: 3, name: 'C', rounds: [{ after_round: 1, p_title: 0.2 }, { after_round: 2, p_title: 0.1 }] },
  ]
  const top = topTitleSeries(series, 2)
  assert.deepEqual(top.map((s) => s.id), [2, 1])
  assert.deepEqual(top[1].points, [{ x: 1, y: 0.5 }, { x: 2, y: 0.1 }])
})

test('stage and round labels', () => {
  assert.equal(stageLabel('pre_qualifying'), 'Before qualifying')
  assert.equal(stageLabel('post_qualifying'), 'After qualifying')
  assert.equal(roundLabel(0), 'Before the season')
  assert.equal(roundLabel(7), 'After round 7')
})

test('labels never round a title chance to a false certainty', () => {
  assert.equal(pctLabel(0.999), '>99%')
  assert.equal(pctLabel(1), '100%')
  assert.equal(pctLabel(0.003), '<1%')
  assert.equal(pctLabel(0), '0%')
  assert.equal(pctLabel(0.678), '68%')
})

test('short names for chart labels', () => {
  assert.equal(shortName('Kimi Antonelli', 'drivers'), 'Antonelli')
  assert.equal(shortName('Red Bull Racing', 'constructors'), 'Red Bull')
  assert.equal(shortName('Haas F1 Team', 'constructors'), 'Haas')
  assert.equal(shortName('Racing Bulls', 'constructors'), 'Racing Bulls')
  assert.equal(shortName('Mercedes', 'constructors'), 'Mercedes')
})
