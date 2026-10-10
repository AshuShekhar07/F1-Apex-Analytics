import assert from 'node:assert/strict'
import { test } from 'node:test'

import { ago, sessionDay, sessionStates } from './format.ts'

test('session days follow the weekend format', () => {
  // 2026 sprint weekend, race on Sunday 11 Oct: SQ Friday, sprint and qualifying Saturday
  assert.equal(sessionDay('2026-10-11', 'sprint_shootout', 2026, 'SQ'), '2026-10-09')
  assert.equal(sessionDay('2026-10-11', 'sprint_shootout', 2026, 'S'), '2026-10-10')
  assert.equal(sessionDay('2026-10-11', 'sprint_shootout', 2026, 'Q'), '2026-10-10')
  // 2023 ran qualifying on Friday
  assert.equal(sessionDay('2023-07-30', 'sprint_shootout', 2023, 'Q'), '2023-07-28')
  assert.equal(sessionDay('2026-10-11', 'conventional', 2026, 'FP3'), '2026-10-10')
  assert.equal(sessionDay('2026-10-11', 'conventional', 2026, 'XX'), null)
})

test('a session whose day has passed without data is waiting, not upcoming', () => {
  const s = (type: string, data = false) => ({ type, has_laps: data, has_results: data })
  const sessions = [s('FP1', true), s('SQ', true), s('S'), s('Q'), s('R')]
  // Saturday evening: sprint and qualifying ran today but are not loaded yet
  assert.deepEqual(sessionStates(sessions, '2026-10-11', 'sprint_shootout', 2026, '2026-10-10'),
    ['done', 'done', 'today', 'today', 'upcoming'])
  // Sunday: they are now overdue
  assert.deepEqual(sessionStates(sessions, '2026-10-11', 'sprint_shootout', 2026, '2026-10-11'),
    ['done', 'done', 'waiting', 'waiting', 'today'])
  // a later session already in means an earlier one without data is waiting
  const gap = [s('FP1'), s('SQ', true), s('S'), s('Q'), s('R')]
  assert.equal(sessionStates(gap, '2026-10-11', 'sprint_shootout', 2026, '2026-10-09')[0], 'waiting')
})

test('ago reads naturally', () => {
  assert.equal(ago(0.4), 'just now')
  assert.equal(ago(14), '14 min ago')
  assert.equal(ago(180), '3 h ago')
  assert.equal(ago(4320), '3 d ago')
})
