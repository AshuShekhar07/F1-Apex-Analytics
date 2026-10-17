import assert from 'node:assert/strict'
import { test } from 'node:test'

import { bestMatch, matchTrack, tokens } from './trackMatch.ts'

const DB = [
  { id: 1, name: 'Autodromo Nazionale di Monza', country: 'Italy', last_season: 2026 },
  { id: 2, name: 'Marina Bay Street Circuit', country: 'Singapore', last_season: 2026 },
  { id: 3, name: 'Circuit de Spa-Francorchamps', country: 'Belgium', last_season: 2026 },
  { id: 4, name: 'Autodromo Enzo e Dino Ferrari', country: 'Italy', last_season: 2025 },
  { id: 5, name: 'Yas Marina Circuit', country: 'UAE', last_season: 2026 },
]

test('tokens drop accents, punctuation and generic words', () => {
  assert.deepEqual([...tokens('Autódromo José Carlos Pace - Interlagos')], ['jose', 'carlos', 'pace', 'interlagos'])
})

test('circuits match on the distinctive words of their name or location', () => {
  assert.equal(matchTrack('Autodromo Nazionale Monza', 'Monza', DB)?.id, 1)
  assert.equal(matchTrack('Marina Bay Street Circuit', 'Singapore', DB)?.id, 2)
  assert.equal(matchTrack('Circuit de Spa-Francorchamps', 'Spa Francorchamps', DB)?.id, 3)
  assert.equal(matchTrack('Autodromo Enzo e Dino Ferrari', 'Imola', DB)?.id, 4)
  assert.equal(matchTrack('Yas Marina Circuit', 'Yas Marina', DB)?.id, 5)
  assert.equal(matchTrack('Hungaroring', 'Budapest', DB), null)
})

test('bestMatch picks the circuit sharing the most words with a database track name', () => {
  const circuits = [
    { id: 'ae-2009', name: 'Yas Marina Circuit', location: 'Yas Marina' },
    { id: 'sg-2008', name: 'Marina Bay Street Circuit', location: 'Singapore' },
    { id: 'us-2023', name: 'Las Vegas Street Circuit', location: 'Las Vegas' },
  ]
  assert.equal(bestMatch(circuits, 'Marina Bay')?.id, 'sg-2008')     // not Yas Marina, though it comes first
  assert.equal(bestMatch(circuits, 'Yas Island')?.id, 'ae-2009')
  assert.equal(bestMatch(circuits, 'Las Vegas')?.id, 'us-2023')
  assert.equal(bestMatch(circuits, 'Suzuka'), null)
  assert.equal(bestMatch(circuits, null), null)
})
