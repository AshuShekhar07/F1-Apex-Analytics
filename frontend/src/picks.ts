/**
 * Fan predictions: the next race's winner and full finishing order, and a season's
 * constructors' champion. Saved in the visitor's browser only (no account); a pick locks when
 * the visitor locks it or when picks close at 00:00 UTC on race day, before any race starts.
 * Nothing here is read by the prediction models.
 */

export interface RacePick {
  v: 1
  raceId: number
  season: number
  round: number
  track: string
  raceDate: string
  winner: number | null
  order: number[] | null
  lockedAt: string | null
  savedAt: string
}

export interface ChampionPick {
  v: 1
  season: number
  teamId: number
  afterRound: number
  lockedAt: string | null
  savedAt: string
}

/** A saved race pick as written by this page (anything else in storage is ignored). */
export function validRacePick(x: unknown): x is RacePick {
  const p = x as RacePick
  return !!p && p.v === 1 && Number.isInteger(p.raceId) && typeof p.raceDate === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(p.raceDate)
    && (p.winner === null || Number.isInteger(p.winner))
    && (p.order === null || (Array.isArray(p.order) && p.order.every((id) => Number.isInteger(id))))
}

export function validChampionPick(x: unknown): x is ChampionPick {
  const p = x as ChampionPick
  return !!p && p.v === 1 && Number.isInteger(p.season) && Number.isInteger(p.teamId) && Number.isInteger(p.afterRound)
}

/** The saved order against today's line-up: drivers no longer entered drop out, new ones go to the back. */
export function fitOrder(order: number[], lineup: number[]): number[] {
  const kept = order.filter((id) => lineup.includes(id))
  return [...kept, ...lineup.filter((id) => !kept.includes(id))]
}

export const raceKey = (raceId: number) => `predict:race:${raceId}`
export const championKey = (season: number) => `predict:constructors:${season}`

/** Picks close at 00:00 UTC on race day: before lights out at every circuit on the calendar. */
export function closesAt(raceDate: string): Date {
  const [y, m, d] = raceDate.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d))
}

export function isClosed(raceDate: string, now: Date): boolean {
  return now.getTime() >= closesAt(raceDate).getTime()
}

export function isLocked(pick: { lockedAt: string | null; raceDate?: string } | null, now: Date): boolean {
  if (!pick) return false
  return pick.lockedAt != null || (pick.raceDate != null && isClosed(pick.raceDate, now))
}

/** Move one driver to a position (0 = P1); the others keep their order. */
export function moveTo(order: number[], id: number, index: number): number[] {
  const rest = order.filter((x) => x !== id)
  const at = Math.max(0, Math.min(index, rest.length))
  return [...rest.slice(0, at), id, ...rest.slice(at)]
}

export function moveBy(order: number[], id: number, delta: number): number[] {
  const from = order.indexOf(id)
  return from === -1 ? order : moveTo(order, id, from + delta)
}

/** Choosing a winner puts them at P1 of the finishing order, if there is one. */
export function withWinner(pick: RacePick, id: number): RacePick {
  return { ...pick, winner: id, order: pick.order ? moveTo(pick.order, id, 0) : null }
}

/** A new finishing order also sets the winner to its P1. */
export function withOrder(pick: RacePick, order: number[]): RacePick {
  return { ...pick, order, winner: order[0] ?? pick.winner }
}

/** The order a visitor starts from: the grid when it is known, else the championship order,
    and anyone missing at the back in the lineup's order. */
export function startingOrder(lineup: { driver_id: number; grid: number | null }[], standings: number[]): number[] {
  const ids = lineup.map((d) => d.driver_id)
  if (lineup.length && lineup.every((d) => d.grid != null)) {
    return [...lineup].sort((a, b) => (a.grid ?? 99) - (b.grid ?? 99)).map((d) => d.driver_id)
  }
  const known = standings.filter((id) => ids.includes(id))
  return [...known, ...ids.filter((id) => !known.includes(id))]
}

export interface Finisher {
  driverId: number
  position: number
  classified: boolean
}

export interface OrderRow {
  driverId: number
  predicted: number
  actual: number | null
  classified: boolean
  diff: number | null
}

export interface OrderScore {
  rows: OrderRow[]
  compared: number
  exact: number
  withinOne: number
  podium: number
  avgMiss: number | null
  winnerRight: boolean | null
}

/** A predicted order against the real one: positions right, within one place, podium drivers
    found, and the average number of places off. */
export function scoreOrder(order: number[], finishers: Finisher[], winner: number | null = order[0] ?? null): OrderScore {
  const actual = new Map(finishers.map((f) => [f.driverId, f]))
  const rows = order.map((driverId, i) => {
    const f = actual.get(driverId)
    return { driverId, predicted: i + 1, actual: f?.position ?? null, classified: f?.classified ?? false,
      diff: f ? f.position - (i + 1) : null }
  })
  const compared = rows.filter((r) => r.diff != null)
  const realPodium = new Set(finishers.filter((f) => f.position <= 3).map((f) => f.driverId))
  const realWinner = finishers.find((f) => f.position === 1)?.driverId ?? null
  return {
    rows,
    compared: compared.length,
    exact: compared.filter((r) => r.diff === 0).length,
    withinOne: compared.filter((r) => Math.abs(r.diff!) <= 1).length,
    podium: order.slice(0, 3).filter((id) => realPodium.has(id)).length,
    avgMiss: compared.length ? compared.reduce((s, r) => s + Math.abs(r.diff!), 0) / compared.length : null,
    winnerRight: winner == null || realWinner == null ? null : winner === realWinner,
  }
}

/** Points per driver from a finishing order (race points only, no sprint or fastest lap). */
export function orderPoints(order: number[], table: number[]): Map<number, number> {
  return new Map(order.map((id, i) => [id, table[i] ?? 0]))
}

export function formatClose(raceDate: string, locale?: string): string {
  return new Intl.DateTimeFormat(locale, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
    .format(closesAt(raceDate))
}
