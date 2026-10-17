import assert from 'node:assert/strict'
import { test } from 'node:test'

import { countdownText, nextSession } from './countdown.ts'
import type { WeekendSession } from './types.ts'

const s = (type: string, start: string | null, loaded = false): WeekendSession =>
  ({ type, name: type, loaded, start_time_utc: start, has_laps: loaded, has_results: loaded })

test('countdown wording', () => {
  assert.equal(countdownText(((26 * 60 + 12) * 60 + 33) * 1000), '1d 02:12:33')
  assert.equal(countdownText(59_000), '00:00:59')
  assert.equal(countdownText(-5), '00:00:00')
})

test('the next session, or the one running now', () => {
  const sessions = [s('FP1', '2026-10-23T17:30:00', true), s('Q', '2026-10-24T21:00:00'), s('R', '2026-10-25T19:00:00'), s('FP2', null)]
  const at = (iso: string) => Date.parse(`${iso}Z`)
  assert.equal(nextSession(sessions, at('2026-10-24T08:00:00'))?.s.type, 'Q')
  assert.deepEqual([nextSession(sessions, at('2026-10-24T21:30:00'))?.s.type, nextSession(sessions, at('2026-10-24T21:30:00'))?.live], ['Q', true])
  assert.equal(nextSession(sessions, at('2026-10-24T23:30:00'))?.s.type, 'R')
  assert.equal(nextSession(sessions, at('2026-10-26T00:00:00')), null)
})
