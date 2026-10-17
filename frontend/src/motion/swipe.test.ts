import assert from 'node:assert/strict'
import { test } from 'node:test'

import { swipeDirection, swipeTarget } from './swipe.ts'

test('a swipe is quick, long enough and mostly sideways', () => {
  assert.equal(swipeDirection(-120, 10, 250), 'left')
  assert.equal(swipeDirection(90, -30, 400), 'right')
  assert.equal(swipeDirection(40, 0, 200), null)        // too short
  assert.equal(swipeDirection(100, 80, 200), null)      // a scroll down the page
  assert.equal(swipeDirection(150, 0, 1200), null)      // a slow drag
})

test('the tab a swipe moves to', () => {
  const tabs = ['drivers', 'constructors']
  assert.equal(swipeTarget(tabs, 'drivers', 'left'), 'constructors')
  assert.equal(swipeTarget(tabs, 'constructors', 'right'), 'drivers')
  assert.equal(swipeTarget(tabs, 'constructors', 'left'), null)
  assert.equal(swipeTarget(['a', 'b', 'c'], 'a', 'left', (x) => x !== 'b'), 'c')   // skips one that is not available
})
