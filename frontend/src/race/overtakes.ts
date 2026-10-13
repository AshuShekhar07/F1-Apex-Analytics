/** Overtakes and battles: the headline numbers and the driver table order. */

import type { Overtakes, OvertakesDriver } from '../types.ts'

export function headlines(data: Overtakes) {
  const most = data.drivers.reduce<OvertakesDriver | null>((a, d) => (d.made > (a?.made ?? 0) ? d : a), null)
  const bestStart = data.drivers.reduce<OvertakesDriver | null>(
    (a, d) => ((d.start_gain ?? 0) > (a?.start_gain ?? 0) ? d : a), null)
  const busiest = data.per_lap.reduce((best, n, i) => (n > best.n ? { lap: i + 1, n } : best), { lap: 0, n: 0 })
  return { most, bestStart, longest: data.battles[0] ?? null, busiest: busiest.n ? busiest : null }
}

/** Most passes made first, then fewest lost, then finishing order. */
export function byPasses(drivers: OvertakesDriver[]): OvertakesDriver[] {
  return [...drivers].sort((a, b) => b.made - a.made || a.lost - b.lost || (a.finish ?? 99) - (b.finish ?? 99))
}

export function signed(n: number | null | undefined): string {
  if (n == null) return '–'
  return n > 0 ? `+${n}` : n < 0 ? `−${Math.abs(n)}` : '0'
}
