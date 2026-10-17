import assert from 'node:assert/strict'
import { test } from 'node:test'

import { driverCode, splitName } from './names.ts'

test('surname is the last word, first names keep the rest', () => {
  assert.deepEqual(splitName('Lando Norris'), ['Lando', 'Norris'])
  assert.deepEqual(splitName('Andrea Kimi Antonelli'), ['Andrea Kimi', 'Antonelli'])
})

test('a single name is all surname', () => {
  assert.deepEqual(splitName('Zhou'), ['', 'Zhou'])
  assert.deepEqual(splitName('  Max   Verstappen '), ['Max', 'Verstappen'])
})

test('timing-screen codes: first three letters of the surname, accents dropped', () => {
  assert.equal(driverCode('Lando Norris'), 'NOR')
  assert.equal(driverCode('Nico Hülkenberg'), 'HUL')
  assert.equal(driverCode('Sergio Pérez'), 'PER')
  assert.equal(driverCode('Andrea Kimi Antonelli'), 'ANT')
})
