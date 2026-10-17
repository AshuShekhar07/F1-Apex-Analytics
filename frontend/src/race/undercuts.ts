/** Undercut checker: the words for each duel and the shared gap scale. */

import type { UndercutDuel } from '../types.ts'

/** The verdict, from the first stopper (F) and the second (S), by their codes. */
export function verdict(d: UndercutDuel, f: string, s: string): { title: string; detail: string; good: boolean } {
  if (d.kind === 'undercut') {
    return d.worked
      ? { title: 'Undercut worked', detail: `${f} pitted first and came out ahead of ${s}`, good: true }
      : { title: 'Undercut failed', detail: `${f} pitted first but ${s} stayed ahead`, good: false }
  }
  return d.worked
    ? { title: 'Overcut worked', detail: `${s} stayed out longer and got ahead of ${f}`, good: true }
    : { title: 'Covered', detail: `${f} pitted first and kept ${s} behind`, good: false }
}

/** "1.8s behind" / "0.6s ahead" / "level", from F's point of view (gap + = behind). */
export function gapWords(gap: number): string {
  if (Math.abs(gap) < 0.05) return 'level'
  return `${Math.abs(gap).toFixed(1)}s ${gap > 0 ? 'behind' : 'ahead'}`
}

/** One scale for every duel: F's gap to S, ahead to the right, symmetric around level. */
export function duelScale(duels: UndercutDuel[]) {
  const max = Math.max(1, ...duels.flatMap((d) => [Math.abs(d.gap_before), Math.abs(d.gap_after)]))
  return { max, pct: (gap: number) => 50 - (gap / max) * 50 }       // behind (+) left of centre, ahead right
}

export function biggestGain(duels: UndercutDuel[]): UndercutDuel | null {
  return duels.reduce<UndercutDuel | null>((a, d) => (Math.abs(d.gain) > Math.abs(a?.gain ?? 0) ? d : a), null)
}
