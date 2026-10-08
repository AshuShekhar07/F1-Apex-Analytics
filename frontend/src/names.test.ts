import assert from 'node:assert/strict'
import { test } from 'node:test'

import { splitName } from './names.ts'

test('surname is the last word, first names keep the rest', () => {
  assert.deepEqual(splitName('Lando Norris'), ['Lando', 'Norris'])
  assert.deepEqual(splitName('Andrea Kimi Antonelli'), ['Andrea Kimi', 'Antonelli'])
})

test('a single name is all surname', () => {
  assert.deepEqual(splitName('Zhou'), ['', 'Zhou'])
  assert.deepEqual(splitName('  Max   Verstappen '), ['Max', 'Verstappen'])
})
