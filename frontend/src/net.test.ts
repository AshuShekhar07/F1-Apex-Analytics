import assert from 'node:assert/strict'
import { test } from 'node:test'

import { fetchWithRetry, isNewBuild, onReconnecting, shouldRetry } from './net.ts'

test('only GETs are tried again, and only while the server is unreachable or restarting', () => {
  assert.equal(shouldRetry('GET', { networkError: true }), true)
  assert.equal(shouldRetry('GET', { status: 502 }), true)
  assert.equal(shouldRetry('get', { status: 503 }), true)
  assert.equal(shouldRetry('GET', { status: 504 }), true)
  assert.equal(shouldRetry('GET', { status: 404 }), false)
  assert.equal(shouldRetry('GET', { status: 500 }), false)
  assert.equal(shouldRetry('GET', { status: 429 }), false)
  assert.equal(shouldRetry('POST', { networkError: true }), false)
})

test('a restarting server: tried again until it answers, and the page hears it is reconnecting', async () => {
  const answers = [new TypeError('fetch failed'), new Response('', { status: 502 }), new Response('{"ok":1}', { status: 200 })]
  const real = globalThis.fetch
  globalThis.fetch = (async () => {
    const next = answers.shift()!
    if (next instanceof Error) throw next
    return next
  }) as typeof fetch
  const seen: number[] = []
  const off = onReconnecting((n) => seen.push(n))
  try {
    const r = await fetchWithRetry('/api/x', {}, [1, 1, 1])
    assert.equal(r.status, 200)
    assert.deepEqual(seen, [0, 1, 0])                    // idle, waiting, back
  } finally {
    off()
    globalThis.fetch = real
  }
})

test('the tries run out: the last answer is returned, a POST is never repeated', async () => {
  const real = globalThis.fetch
  let calls = 0
  globalThis.fetch = (async () => {
    calls++
    return new Response('', { status: 503 })
  }) as typeof fetch
  try {
    assert.equal((await fetchWithRetry('/api/x', {}, [1, 1])).status, 503)
    assert.equal(calls, 3)
    calls = 0
    assert.equal((await fetchWithRetry('/api/x', { method: 'POST' }, [1, 1])).status, 503)
    assert.equal(calls, 1)
  } finally {
    globalThis.fetch = real
  }
})

test('a new build is noticed', () => {
  assert.equal(isNewBuild('abc', { build: 'def' }), true)
  assert.equal(isNewBuild('abc', { build: 'abc' }), false)
  assert.equal(isNewBuild('abc', null), false)
  assert.equal(isNewBuild('abc', { build: '' }), false)
  assert.equal(isNewBuild('abc', '<!doctype html>'), false)
})
