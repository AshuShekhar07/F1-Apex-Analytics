import assert from 'node:assert/strict'
import { test } from 'node:test'

import { F1_RED, glowColour, hexToRgb, inkOn, luminance, normaliseHex } from './color.ts'

test('normaliseHex accepts short and bare forms and falls back otherwise', () => {
  assert.equal(normaliseHex('FFF'), '#ffffff')
  assert.equal(normaliseHex('#3671C6'), '#3671c6')
  assert.equal(normaliseHex('3671c6'), '#3671c6')
  assert.equal(normaliseHex(null), F1_RED)
  assert.equal(normaliseHex('nope', '#000000'), '#000000')
})

test('ink and glow follow the background brightness', () => {
  assert.deepEqual(hexToRgb('#ff8000'), [255, 128, 0])
  assert.ok(luminance('#ffffff') > 0.99 && luminance('#000000') === 0)
  assert.equal(inkOn('#ffffff'), '#0b0b0f')
  assert.equal(inkOn('#e10600'), '#ffffff')
  assert.equal(glowColour('#000000'), '#595959')       // black is lifted so it shows
  assert.equal(glowColour('#ff8000'), '#ff8000')       // bright colours stay as they are
})
