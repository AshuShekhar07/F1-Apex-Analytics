/** Championship arithmetic: how many points are still available and who can still win. */

export interface Standing {
  id: number
  name: string
  points: number
}

export interface PointsSystem {
  race: number
  sprint: number
}

/** Most points one driver (or one team, two cars) can score in a race / sprint that season. */
export function pointsSystem(season: number, kind: 'drivers' | 'constructors'): PointsSystem {
  const fastestLap = season >= 2019 && season <= 2024 ? 1 : 0
  const sprintWin = season === 2021 ? 3 : season >= 2022 ? 8 : 0
  const sprintSecond = season === 2021 ? 2 : season >= 2022 ? 7 : 0
  return kind === 'drivers'
    ? { race: 25 + fastestLap, sprint: sprintWin }
    : { race: 25 + 18 + fastestLap, sprint: sprintWin + sprintSecond }
}

export interface TitleRow extends Standing {
  gap: number
  maxFinal: number
  alive: boolean
}

export interface TitleMath {
  available: number
  races: number
  sprints: number
  rows: TitleRow[]
  /** the leader is champion already (no one can reach them) */
  decided: boolean
  /** the leader can clinch at the next round with a maximum score there and nothing for anyone else */
  canClinchNext: boolean
}

export function titleMath(standings: Standing[], races: number, sprints: number, system: PointsSystem,
                          nextIsSprint: boolean): TitleMath {
  const available = races * system.race + sprints * system.sprint
  const leader = standings[0]?.points ?? 0
  const rows = standings.map((s) => ({ ...s, gap: leader - s.points, maxFinal: s.points + available, alive: s.points + available >= leader }))
  const lead = standings.length > 1 ? leader - standings[1].points : Infinity
  const next = races > 0 ? system.race + (nextIsSprint ? system.sprint : 0) : 0
  return {
    available, races, sprints, rows,
    decided: lead > available,
    canClinchNext: !(lead > available) && races > 0 && lead + next > available - next,
  }
}
