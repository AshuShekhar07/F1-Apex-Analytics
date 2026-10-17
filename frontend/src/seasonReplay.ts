/** The championship replayed round by round: who led after each round. Pure. */

import type { SeasonResults } from './types'

export interface ReplayRow {
  id: number
  name: string
  colour: string | null
  team: string | null
  points: number
  wins: number
}

/** Rounds that have been raced, in order. */
export function racedRounds(m: SeasonResults): number[] {
  return m.rounds.filter((r) => r.raced).map((r) => r.round).sort((a, b) => a - b)
}

/** Standings after `round` (race and sprint points), most points first, then most wins. */
export function standingsAfter(m: SeasonResults, kind: 'drivers' | 'constructors', round: number): ReplayRow[] {
  const rows = new Map<number, ReplayRow>()
  const teamOf = new Map(m.teams.map((t) => [t.id, t]))
  for (const d of m.drivers) {
    for (const cell of d.results) {
      if (cell.round > round) continue
      const pts = (cell.race?.points ?? 0) + (cell.sprint?.points ?? 0)
      const win = cell.race?.position === 1 && cell.race.classified ? 1 : 0
      const key = kind === 'drivers' ? d.id : cell.team_id
      if (key == null) continue
      const team = kind === 'drivers' ? d.team : teamOf.get(key) ?? null
      const row = rows.get(key) ?? {
        id: key, name: kind === 'drivers' ? d.name : team?.name ?? '?', colour: team?.color_hex ?? null,
        team: team?.name ?? null, points: 0, wins: 0,
      }
      row.points += Number.isFinite(pts) ? pts : 0
      row.wins += win
      rows.set(key, row)
    }
  }
  return [...rows.values()].sort((a, b) => b.points - a.points || b.wins - a.wins || a.name.localeCompare(b.name))
}
