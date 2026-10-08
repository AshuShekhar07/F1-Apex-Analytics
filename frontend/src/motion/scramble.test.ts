import assert from 'node:assert/strict'
import { test } from 'node:test'

import { scrambleFrame } from './scramble.ts'

test('fully resolved at the end, fully scrambled at the start (spaces kept)', () => {
  assert.equal(scrambleFrame('LIGHTS OUT', 1), 'LIGHTS OUT')
  const start = scrambleFrame('LIGHTS OUT', 0, () => 0)
  assert.equal(start.length, 'LIGHTS OUT'.length)
  assert.equal(start[6], ' ')
  assert.equal(start.replace(' ', ''), 'A'.repeat(9))
})

test('resolves left to right', () => {
  assert.equal(scrambleFrame('NORRIS', 0.5, () => 0), 'NORAAA')
  assert.equal(scrambleFrame('NORRIS', 2), 'NORRIS')
})
