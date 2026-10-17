import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  aliveRivals, applyRound, available, decided, earliestClinch, maxPerRound, outOfReach, titleScenario,
  type Contender, type RoundLeft,
} from './titleScenarios.ts'

const rounds = (n: number, sprintAt: number[] = []): RoundLeft[] =>
  Array.from({ length: n }, (_, i) => ({ round: 18 + i, track: `T${18 + i}`, sprint: sprintAt.includes(i) }))

const K: Contender = { id: 1, name: 'Kimi', points: 400, wins: 10 }
const R: Contender = { id: 2, name: 'George', points: 290, wins: 4 }
const H: Contender = { id: 3, name: 'Lewis', points: 250, wins: 2 }

test('points still available follow the season rules', () => {
  assert.deepEqual(maxPerRound(2026, 'drivers'), { race: 25, sprint: 8 })
  assert.deepEqual(maxPerRound(2024, 'drivers'), { race: 26, sprint: 8 })        // fastest-lap point
  assert.deepEqual(maxPerRound(2026, 'constructors'), { race: 43, sprint: 15 })
  assert.equal(available(rounds(6), 2026, 'drivers'), 150)
  assert.equal(available(rounds(3, [1]), 2026, 'drivers'), 83)
})

test('a tie on points counts as out of reach only when the leader wins the countback for sure', () => {
  // level at the end only if the rival wins the 2 races left: 4 + 2 = 6 wins against the leader's 7
  assert.equal(outOfReach({ ...K, points: 350, wins: 7 }, { ...R, points: 300, wins: 4 }, 50, 2, 0), true)
  assert.equal(outOfReach({ ...K, points: 350, wins: 6 }, { ...R, points: 300, wins: 4 }, 50, 2, 0), false)
  assert.equal(outOfReach(K, R, 109, 5, 0), true)
  assert.equal(outOfReach(K, R, 111, 5, 0), false)
})

test('who is still in it, and a decided title', () => {
  // Lewis can only tie (250 + 150 = 400) and would lose on wins (2 + 6 < 10)
  assert.deepEqual(aliveRivals([K, R, H], rounds(6), 2026, 'drivers').map((r) => r.name), ['George'])
  assert.equal(decided([K, R, H], rounds(6), 2026, 'drivers'), false)
  assert.equal(decided([K, { ...R, points: 240 }], rounds(6), 2026, 'drivers'), true)
  assert.equal(titleScenario([K, { ...R, points: 240 }], rounds(6), 2026, 'drivers')?.mode, 'decided')
})

test('a 110-point lead with six races left: what Austin decides', () => {
  const s = titleScenario([K, R, H], rounds(6), 2026, 'drivers')
  assert.ok(s && s.mode === 'positions')
  const need = (pos: number) => s.rows.find((r) => r.position === pos)!.needs.map((n) => n.need)
  // 125 points left after Austin. Kimi wins: George P5 (10 pts) leaves them level on 125, and
  // Kimi has 11 wins to George's at most 4 + 5 -> champion on countback; P4 or better is not enough
  assert.deepEqual(need(1), [5])
  assert.deepEqual(need(2), [9])                 // 418 vs 290 + 2 = 126 ahead
  assert.deepEqual(need(3), ['no-points'])       // level on points, ahead on wins
  assert.deepEqual(need(4), ['none'])
  assert.deepEqual(s.rows.filter((r) => r.possible).map((r) => r.position), [1, 2, 3])
  assert.deepEqual(s.margins.map((m) => m.needed), [16])
})

test('when a win settles it whatever the rival does', () => {
  const s = titleScenario([{ ...K, points: 420 }, R], rounds(6), 2026, 'drivers')
  assert.ok(s && s.mode === 'positions')
  assert.deepEqual(s.rows[0].needs.map((n) => n.need), ['any'])
})

test('too far from settling it: the earliest round it could be decided', () => {
  // lead 66 with 8 races left (200 points): after round k the lead can be 66 + 25k against 200 - 25k
  const s = titleScenario([{ ...K, points: 302, wins: 8 }, { ...R, points: 236 }], rounds(8), 2026, 'drivers')
  assert.ok(s && s.mode === 'not-this-round')
  assert.equal(s.earliest?.round, 20)            // after the 3rd race: 66 + 75 = 141 > 125 left
  assert.equal(earliestClinch([{ ...K, points: 302, wins: 8 }, { ...R, points: 236 }], rounds(8), 2026, 'drivers')?.track, 'T20')
})

test('sprint weekends and constructors are given as a points margin', () => {
  const sprint = titleScenario([K, R], rounds(6, [0]), 2026, 'drivers')
  assert.ok(sprint && sprint.mode === 'margin')
  assert.equal(sprint.maxMargin, 33)
  assert.deepEqual(sprint.margins.map((m) => m.needed), [16])
  const teams = titleScenario([{ id: 9, name: 'Mercedes', points: 700, wins: 14 }, { id: 8, name: 'Ferrari', points: 500, wins: 2 }],
    rounds(6), 2026, 'constructors')
  assert.ok(teams && teams.mode === 'margin')
  assert.deepEqual(teams.margins.map((m) => m.needed), [16])   // 5 x 43 = 215 left after it, lead 200
})

test('standings after a race', () => {
  const after = applyRound([K, R], new Map([[1, 0], [2, 25]]), 2)
  assert.deepEqual(after.map((c) => [c.name, c.points, c.wins]), [['Kimi', 400, 10], ['George', 315, 5]])
})

test('lines with the same rival results are merged, and margins read as words', async () => {
  const { groupRows, groupResult, marginWords } = await import('./titleScenarios.ts')
  // the e2e case: a 35-point lead with 2 races left
  const s = titleScenario([{ id: 1, name: 'Franco Colapinto', points: 101, wins: 3 }, { id: 2, name: 'Alexander Albon', points: 66, wins: 1 }],
    rounds(2), 2026, 'drivers')
  assert.ok(s && s.mode === 'positions')
  const groups = groupRows(s.rows).map((g) => [groupResult(g).label, groupResult(g).text, g.needs.map((n) => n.need).join()])
  assert.deepEqual(groups, [
    ['P1–2', 'finishes P1 or P2', 'any'],
    ['P3–6', 'finishes P3 to P6', '2'],
    ['P7', 'finishes P7', '3'],
    ['P8–9', 'finishes P8 or P9', '4'],
    ['P10+', 'finishes P10 or lower', '5'],
  ])
  assert.equal(marginWords('Colapinto', 'Albon', -9), 'Albon does not outscore Colapinto by more than 9')
  assert.equal(marginWords('Mercedes', 'Ferrari', 16), 'Mercedes outscores Ferrari by 16 or more')
  assert.equal(marginWords('A', 'B', 0), 'B does not outscore A')
})
