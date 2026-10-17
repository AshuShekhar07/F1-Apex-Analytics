import assert from 'node:assert/strict'
import { test } from 'node:test'

import { score, search, words, type SearchItem } from './search.ts'

const items: SearchItem[] = [
  { kind: 'page', title: 'Championship', words: 'standings title', to: '/championship' },
  { kind: 'race', title: '2024 Monaco', sub: 'Round 8 · Charles Leclerc', words: 'Monaco grand prix', to: '/race/1', rank: 0.06 },
  { kind: 'race', title: '2021 Monaco', sub: 'Round 5 · Max Verstappen', words: 'Monaco grand prix', to: '/race/2', rank: 0.03 },
  { kind: 'driver', title: 'Sergio Pérez', sub: 'Red Bull Racing', words: 'Mexican 11', to: '/drivers?driver=3' },
  { kind: 'track', title: 'Autódromo José Carlos Pace - Interlagos', sub: 'Sao Paulo', to: '/tracks?circuit=br-1940' },
]

test('words drop accents and punctuation', () => {
  assert.deepEqual(words('Autódromo José Carlos Pace - Interlagos'), ['autodromo', 'jose', 'carlos', 'pace', 'interlagos'])
})

test('every query word has to match', () => {
  assert.ok(score(items[1], 'monaco 2024') > 0)
  assert.equal(score(items[1], 'monaco 2019'), 0)
  assert.equal(score(items[0], ''), 0)
})

test('accents, prefixes and words inside', () => {
  assert.equal(search(items, 'perez')[0].title, 'Sergio Pérez')
  assert.equal(search(items, 'champ')[0].kind, 'page')
  assert.equal(search(items, 'lagos')[0].kind, 'track')          // inside a word, from three letters
  assert.deepEqual(search(items, 'la'), [])                       // two letters only start words
  assert.equal(search(items, 'standings')[0].to, '/championship')
})

test('title matches first, then the rank (recent seasons)', () => {
  assert.deepEqual(search(items, 'monaco').map((i) => i.to), ['/race/1', '/race/2'])
  assert.equal(search(items, 'leclerc')[0].to, '/race/1')
  assert.equal(search(items, 'monaco', 1).length, 1)
})
