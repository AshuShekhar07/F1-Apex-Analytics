/** Ideal lap arithmetic for the chart: the shared scale of the rows and the headline numbers. */

import type { IdealLap, IdealLapDriver } from '../types.ts'

export const SESSION_LABEL: Record<string, string> = {
  FP1: 'Practice 1', FP2: 'Practice 2', FP3: 'Practice 3', SQ: 'Sprint quali', Q: 'Qualifying',
}

/** One horizontal scale for every row: seconds from the pole lap, from the quickest ideal lap
    (which can beat pole) to the slowest real lap. pct maps a gap to a 0-100 position. */
export function gapScale(drivers: IdealLapDriver[], pole: number | null | undefined) {
  const gaps = drivers.flatMap((d) => [d.best_lap, d.ideal_lap]).filter((v): v is number => v != null && pole != null)
    .map((v) => v - (pole as number))
  const lo = Math.min(0, ...gaps)
  const hi = Math.max(0.1, ...gaps)
  const span = hi - lo
  return { lo, hi, pct: (gap: number) => ((gap - lo) / span) * 100 }
}

/** Who left the most time on the table, and whose ideal lap would have been fastest of all. */
export function headlines(data: IdealLap): { mostLeft: IdealLapDriver | null; idealBest: IdealLapDriver | null } {
  const timed = data.drivers.filter((d) => d.left_on_table != null)
  const mostLeft = timed.length ? timed.reduce((a, b) => ((b.left_on_table ?? 0) > (a.left_on_table ?? 0) ? b : a)) : null
  const idealBest = data.drivers.find((d) => d.ideal_rank === 1) ?? null
  return { mostLeft: mostLeft && (mostLeft.left_on_table ?? 0) > 0 ? mostLeft : null, idealBest }
}

/** A driver whose ideal lap ranks higher than their real best lap: the places it was worth. */
export function placesInHand(d: IdealLapDriver): number {
  return d.ideal_rank != null && d.best_rank != null ? Math.max(0, d.best_rank - d.ideal_rank) : 0
}
