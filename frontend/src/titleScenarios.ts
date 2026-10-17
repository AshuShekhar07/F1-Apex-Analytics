/**
 * Title scenarios: what the next round can decide. Pure points arithmetic on the real points
 * system -- no model, no probabilities.
 *
 * "Clinched" means no rival can reach the leader even by scoring the maximum everywhere after
 * the next round. A tie on points goes to the most wins; that is only counted as clinched when
 * the leader has more wins even if the rival wins every race (and sprint) left, so a sentence
 * here never claims a title that a countback could still take away.
 */

export type Kind = 'drivers' | 'constructors'

export interface Contender {
  id: number
  name: string
  points: number
  wins: number
}

export interface RoundLeft {
  round: number
  track: string
  /** a sprint still to be run this weekend */
  sprint: boolean
}

export const RACE_POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]

export function sprintPoints(season: number): number[] {
  return season === 2021 ? [3, 2, 1] : season >= 2022 ? [8, 7, 6, 5, 4, 3, 2, 1] : []
}

export function fastestLapPoint(season: number): boolean {
  return season >= 2019 && season <= 2024
}

/** Most points one driver (one race) or one team (two cars) can take from a race / a sprint. */
export function maxPerRound(season: number, kind: Kind): { race: number; sprint: number } {
  const fl = fastestLapPoint(season) ? 1 : 0
  const sp = sprintPoints(season)
  return kind === 'drivers'
    ? { race: RACE_POINTS[0] + fl, sprint: sp[0] ?? 0 }
    : { race: RACE_POINTS[0] + RACE_POINTS[1] + fl, sprint: (sp[0] ?? 0) + (sp[1] ?? 0) }
}

export function available(rounds: RoundLeft[], season: number, kind: Kind): number {
  const m = maxPerRound(season, kind)
  return rounds.reduce((sum, r) => sum + m.race + (r.sprint ? m.sprint : 0), 0)
}

/** Race points for a finishing position (0 = out of the points / did not finish). */
export function racePoints(position: number): number {
  return position >= 1 && position <= RACE_POINTS.length ? RACE_POINTS[position - 1] : 0
}

/** Is `leader` out of reach of `rival` with `left` points still available (and these rounds left)? */
export function outOfReach(leader: Contender, rival: Contender, left: number, racesLeft: number, sprintsLeft: number): boolean {
  const lead = leader.points - rival.points
  if (lead > left) return true
  // level on points at the end only if the rival takes every point left, winning everything
  return lead === left && leader.wins > rival.wins + racesLeft + sprintsLeft
}

export function decided(standings: Contender[], rounds: RoundLeft[], season: number, kind: Kind): boolean {
  if (standings.length < 2) return standings.length === 1 && rounds.length === 0
  const left = available(rounds, season, kind)
  const sprints = rounds.filter((r) => r.sprint).length
  return standings.slice(1).every((r) => outOfReach(standings[0], r, left, rounds.length, sprints))
}

/** Rivals who can still mathematically win the title. */
export function aliveRivals(standings: Contender[], rounds: RoundLeft[], season: number, kind: Kind): Contender[] {
  if (!standings.length) return []
  const left = available(rounds, season, kind)
  const sprints = rounds.filter((r) => r.sprint).length
  return standings.slice(1).filter((r) => !outOfReach(standings[0], r, left, rounds.length, sprints))
}

/** What a rival must do for the leader to clinch: 'any' result, finish `from` or lower, not
    score at all, or 'none' (the leader cannot clinch with this result). */
export type RivalNeed = { rival: Contender; need: 'any' | 'none' | 'no-points' | number }

export interface PositionRow {
  /** leader's finishing position; 0 = out of the points */
  position: number
  possible: boolean
  needs: RivalNeed[]
}

export type Scenario =
  | { mode: 'decided'; leader: Contender }
  | { mode: 'not-this-round'; leader: Contender; round: RoundLeft; earliest: RoundLeft | null }
  | { mode: 'positions'; leader: Contender; round: RoundLeft; rows: PositionRow[]; margins: { rival: Contender; needed: number }[] }
  | { mode: 'margin'; leader: Contender; round: RoundLeft; margins: { rival: Contender; needed: number }[]; maxMargin: number }

/** Points the leader must outscore each rival by at the next round to clinch there (strictly). */
function neededMargins(leader: Contender, rivals: Contender[], afterNext: number) {
  return rivals.map((rival) => ({ rival, needed: afterNext - (leader.points - rival.points) + 1 }))
}

/** The first round the leader could clinch at: maximum score every round, nobody else scoring. */
export function earliestClinch(standings: Contender[], rounds: RoundLeft[], season: number, kind: Kind): RoundLeft | null {
  const rivals = aliveRivals(standings, rounds, season, kind)
  if (!rivals.length) return null
  const m = maxPerRound(season, kind)
  const leader = { ...standings[0] }
  for (let k = 0; k < rounds.length; k++) {
    leader.points += m.race + (rounds[k].sprint ? m.sprint : 0)
    leader.wins += 1
    const rest = rounds.slice(k + 1)
    const left = available(rest, season, kind)
    const sprints = rest.filter((r) => r.sprint).length
    if (rivals.every((r) => outOfReach(leader, r, left, rest.length, sprints))) return rounds[k]
  }
  return null
}

/** What the next round can decide for the title. */
export function titleScenario(standings: Contender[], rounds: RoundLeft[], season: number, kind: Kind): Scenario | null {
  if (!standings.length || !rounds.length) return null
  const leader = standings[0]
  if (decided(standings, rounds, season, kind)) return { mode: 'decided', leader }
  const next = rounds[0]
  const after = rounds.slice(1)
  const afterNext = available(after, season, kind)
  const sprintsAfter = after.filter((r) => r.sprint).length
  const rivals = aliveRivals(standings, rounds, season, kind)
  const margins = neededMargins(leader, rivals, afterNext)
  const m = maxPerRound(season, kind)
  const maxMargin = m.race + (next.sprint ? m.sprint : 0)
  if (margins.some((x) => x.needed > maxMargin)) {
    return { mode: 'not-this-round', leader, round: next, earliest: earliestClinch(standings, rounds, season, kind) }
  }
  if (kind === 'constructors' || next.sprint) return { mode: 'margin', leader, round: next, margins, maxMargin }

  // a single race: every finishing position of the leader against every result of each rival
  const fl = fastestLapPoint(season)
  const rows: PositionRow[] = []
  for (const position of [...RACE_POINTS.map((_, i) => i + 1), 0]) {
    const leaderAfter: Contender = { ...leader, points: leader.points + racePoints(position), wins: leader.wins + (position === 1 ? 1 : 0) }
    const needs: RivalNeed[] = rivals.map((rival) => {
      const best = position === 1 ? 2 : 1
      // rival results from their best possible down to no points; the worst case for the leader
      // takes the fastest-lap point for the rival whenever they are in the top ten
      const clinches = (q: number) => {
        const pts = q === 0 ? 0 : racePoints(q) + (fl && q <= 10 ? 1 : 0)
        return outOfReach(leaderAfter, { ...rival, points: rival.points + pts, wins: rival.wins + (q === 1 ? 1 : 0) },
                          afterNext, after.length, sprintsAfter)
      }
      const order = [...RACE_POINTS.map((_, i) => i + 1).filter((q) => q !== position && q >= best), 0]
      const first = order.findIndex(clinches)
      if (first === -1) return { rival, need: 'none' }
      if (first === 0) return { rival, need: 'any' }
      const from = order[first]
      return { rival, need: from === 0 ? 'no-points' : from }
    })
    rows.push({ position, possible: needs.every((n) => n.need !== 'none'), needs })
  }
  return { mode: 'positions', leader, round: next, rows, margins }
}

/** Standings after one race: points added per entity, re-sorted (points, then wins). */
export function applyRound(standings: Contender[], points: Map<number, number>, winnerId: number | null): Contender[] {
  return standings
    .map((s) => ({ ...s, points: s.points + (points.get(s.id) ?? 0), wins: s.wins + (s.id === winnerId ? 1 : 0) }))
    .sort((a, b) => b.points - a.points || b.wins - a.wins)
}

/** Consecutive finishing positions of the leader that need the same rival results, merged:
    P3, P4, P5 and P6 with "Albon P2 or lower" become one line. Positions run 1..10 then 0. */
export interface RowGroup {
  from: number
  to: number
  needs: RivalNeed[]
}

export function groupRows(rows: PositionRow[]): RowGroup[] {
  const same = (a: RivalNeed[], b: RivalNeed[]) => a.length === b.length && a.every((n, i) => n.rival.id === b[i].rival.id && n.need === b[i].need)
  const out: RowGroup[] = []
  for (const r of rows.filter((x) => x.possible)) {
    const last = out[out.length - 1]
    const follows = last && (r.position === 0 ? last.to === RACE_POINTS.length : r.position === last.to + 1)
    if (last && follows && same(last.needs, r.needs)) last.to = r.position
    else out.push({ from: r.position, to: r.position, needs: r.needs })
  }
  return out
}

/** "P3 to P6", "wins", "does not score", "finishes P9 or lower" for a group of the leader's results. */
export function groupResult(g: RowGroup): { label: string; text: string } {
  if (g.from === 0) return { label: '–', text: 'does not score' }
  if (g.from === 1 && g.to === 1) return { label: 'P1', text: 'wins' }
  if (g.to === 0) return { label: `P${g.from}+`, text: `finishes P${g.from} or lower` }
  if (g.from === g.to) return { label: `P${g.from}`, text: `finishes P${g.from}` }
  if (g.from === 1) return { label: `P1–${g.to}`, text: g.to === 2 ? 'finishes P1 or P2' : `finishes in the top ${g.to}` }
  return { label: `P${g.from}–${g.to}`, text: g.to === g.from + 1 ? `finishes P${g.from} or P${g.to}` : `finishes P${g.from} to P${g.to}` }
}

/** A points margin in words: what the leader must do against one rival at the round. */
export function marginWords(leader: string, rival: string, needed: number): string {
  if (needed > 0) return `${leader} outscores ${rival} by ${needed} or more`
  if (needed === 0) return `${rival} does not outscore ${leader}`
  return `${rival} does not outscore ${leader} by more than ${-needed}`
}
