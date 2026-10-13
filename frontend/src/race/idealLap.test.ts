import assert from 'node:assert/strict'
import { test } from 'node:test'

import type { IdealLap, IdealLapDriver } from '../types.ts'
import { gapScale, headlines, placesInHand } from './idealLap.ts'

function d(id: number, best: number | null, ideal: number | null, bestRank: number | null, idealRank: number | null): IdealLapDriver {
  return { driver_id: id, driver_name: `D${id}`, team_name: 'T', color_hex: null, position: bestRank, best_lap: best, ideal_lap: ideal,
    left_on_table: best != null && ideal != null ? Math.round((best - ideal) * 1000) / 1000 : null,
    best_rank: bestRank, ideal_rank: idealRank, sectors: [] }
}

const rows = [d(1, 90.0, 89.9, 1, 2), d(2, 90.2, 89.8, 2, 1), d(3, 91.0, 91.0, 3, 3), d(4, null, null, null, null)]

test('one scale for every row: from the quickest ideal lap (it can beat pole) to the slowest lap', () => {
  const s = gapScale(rows, 90.0)
  assert.equal(Math.round(s.lo * 1000) / 1000, -0.2)
  assert.equal(s.hi, 1)
  assert.equal(Math.round(s.pct(0)), 17)          // pole sits a sixth of the way in
  assert.equal(s.pct(1), 100)
  const empty = gapScale([], null)
  assert.deepEqual([empty.lo, empty.hi], [0, 0.1])
})

test('headlines: the most time left on the table, and the fastest ideal lap', () => {
  const h = headlines({ drivers: rows } as IdealLap)
  assert.equal(h.mostLeft?.driver_id, 2)
  assert.equal(h.idealBest?.driver_id, 2)
  assert.equal(headlines({ drivers: [d(3, 91, 91, 1, 1)] } as IdealLap).mostLeft, null)   // nothing left anywhere
})

test('places in hand: how much higher the ideal lap ranks', () => {
  assert.equal(placesInHand(rows[1]), 1)
  assert.equal(placesInHand(rows[0]), 0)
  assert.equal(placesInHand(rows[3]), 0)
})
