import assert from 'node:assert/strict'
import { test } from 'node:test'

import { centre, radius, ribbonArrays } from './ribbon.ts'

test('a straight centre line becomes a flat road of the given width and thickness', () => {
  const { positions, indices } = ribbonArrays([[0, 0], [10, 0], [20, 0]], 4, 1)
  assert.equal(positions.length, 3 * 4 * 3)
  assert.equal(indices.length, 2 * 4 * 6)           // 2 segments x 4 faces x 2 triangles
  // first point: top-left at z=+2, top-right at z=-2, both at y=thickness; bottom at y=0
  assert.deepEqual(Array.from(positions.slice(0, 12)), [0, 1, 2, 0, 1, -2, 0, 0, 2, 0, 0, -2])
  assert.ok(Math.max(...indices) < 12)
})

test('a turn keeps the road width across the bend', () => {
  const { positions } = ribbonArrays([[0, 0], [10, 0], [10, 10]], 2, 0.5)
  const corner = Array.from(positions.slice(12, 18))   // top-left, top-right of the middle point
  const width = Math.hypot(corner[0] - corner[3], corner[2] - corner[5])
  assert.ok(Math.abs(width - 2) < 1e-6)
})

test('degenerate input and framing helpers', () => {
  assert.equal(ribbonArrays([[1, 1]], 2, 1).positions.length, 0)
  assert.deepEqual(centre([[[0, 0], [10, 4]], [[-10, -4]]]), [0, 0])
  assert.ok(Math.abs(radius([[[0, 0], [3, 4]]]) - 2.5) < 1e-9)
})
