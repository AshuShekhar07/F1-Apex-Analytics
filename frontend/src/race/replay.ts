/**
 * Race replay arithmetic: where every car is on the circuit at any moment, from the stored lap
 * timing (the race trace: each car's time against the winner's average pace at the end of every
 * lap). Pure, so it can be tested.
 *
 * Clock: seconds since the leader completed lap 1. A car's crossing time at the end of lap n is
 * (n - 1) x reference lap - trace[n - 1]. Between two crossings a car moves round the lap, faster
 * through the circuit's quicker sectors (the sector average speeds weigh how long each part of
 * the lap takes). The first lap itself has no timing, so the replay starts as lap 1 ends.
 */

import type { Pt } from '../circuitTypes'
import type { RaceTrace, RaceTraceDriver } from '../types'

/** End-of-lap times (index = lap - 1) on the replay clock; null where the timing has a gap. */
export function crossingTimes(d: RaceTraceDriver, referenceLap: number): (number | null)[] {
  return d.trace.map((t, i) => (t == null ? null : i * referenceLap - t))
}

export interface CarState {
  /** laps completed */
  laps: number
  /** share of the current lap covered, 0-1 */
  frac: number
  /** laps + frac, for the running order */
  progress: number
  finished: boolean
}

/** Where a car is at time T, or null once it has retired. Before its first known crossing it
    runs in on its first timed lap (one reference lap behind the line at most). */
export function carState(times: (number | null)[], T: number, totalLaps: number, referenceLap: number): CarState | null {
  let last = -1
  for (let i = 0; i < times.length; i++) {
    const t = times[i]
    if (t == null) continue
    if (t <= T) {
      last = i
      continue
    }
    if (last < 0) {                                   // still on the lap before its first timed one
      const frac = Math.max(0, 1 - (t - T) / referenceLap)
      return { laps: i, frac, progress: i + frac, finished: false }
    }
    const t0 = times[last]!
    const per = (t - t0) / (i - last)                 // a timing gap spans several laps evenly
    const x = (T - t0) / per
    const laps = last + 1 + Math.floor(x)
    const frac = x % 1
    return { laps, frac, progress: laps + frac, finished: false }
  }
  if (last < 0) return null
  const lapsDone = last + 1
  // after its last crossing: finished if it ran the full distance, else it has retired
  return lapsDone >= totalLaps ? { laps: lapsDone, frac: 0, progress: lapsDone, finished: true } : null
}

export interface LapPath {
  points: Pt[]
  /** cumulative distance along points (same units) */
  cum: number[]
  length: number
  /** for each sector: [start distance, end distance, share of lap time at the start, at the end] */
  sectors: [number, number, number, number][]
}

/** The lap as one path with time weights per sector (length / average speed). Points are in
    any units; sectorPoints are the three sectors in racing order, starting at the line. */
export function lapPath(sectorPoints: Pt[][], speeds: (number | null)[]): LapPath {
  const points: Pt[] = []
  const breaks: number[] = [0]
  for (const s of sectorPoints) {
    for (const p of s) {
      const last = points[points.length - 1]
      if (!last || last[0] !== p[0] || last[1] !== p[1]) points.push(p)
    }
    breaks.push(points.length - 1)
  }
  const cum = [0]
  for (let i = 1; i < points.length; i++) cum.push(cum[i - 1] + Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]))
  const length = cum[cum.length - 1] || 1
  const lens = sectorPoints.map((_, k) => cum[breaks[k + 1]] - cum[breaks[k]])
  const known = speeds.filter((v): v is number => v != null && v > 0)
  const fallback = known.length ? known.reduce((a, b) => a + b, 0) / known.length : 1
  const times = lens.map((l, k) => l / ((speeds[k] && speeds[k]! > 0 ? speeds[k]! : fallback)))
  const total = times.reduce((a, b) => a + b, 0) || 1
  const sectors: LapPath['sectors'] = []
  let d = 0, w = 0
  for (let k = 0; k < lens.length; k++) {
    sectors.push([d, d + lens[k], w, w + times[k] / total])
    d += lens[k]
    w += times[k] / total
  }
  return { points, cum, length, sectors }
}

/** The point at a share of the lap's time (0-1). */
export function pointAt(path: LapPath, frac: number): Pt {
  const f = ((frac % 1) + 1) % 1
  const s = path.sectors.find((x) => f >= x[2] && f <= x[3]) ?? path.sectors[path.sectors.length - 1]
  const share = s[3] > s[2] ? (f - s[2]) / (s[3] - s[2]) : 0
  const dist = s[0] + share * (s[1] - s[0])
  let i = 1
  while (i < path.cum.length - 1 && path.cum[i] < dist) i++
  const a = path.points[i - 1], b = path.points[i]
  const seg = path.cum[i] - path.cum[i - 1] || 1
  const t = Math.min(1, Math.max(0, (dist - path.cum[i - 1]) / seg))
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]
}

/** Map circuit metres into a 1000 x 1000 box, north up (as the circuit outlines). */
export function projector(points: Pt[]): (p: Pt) => Pt {
  const xs = points.map((p) => p[0])
  const ys = points.map((p) => -p[1])
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys)
  const scale = 900 / Math.max(maxX - minX, maxY - minY || 1)
  const ox = (1000 - (maxX - minX) * scale) / 2, oy = (1000 - (maxY - minY) * scale) / 2
  return ([x, y]) => [ox + (x - minX) * scale, oy + (-y - minY) * scale]
}

export interface Running {
  id: number
  state: CarState
}

/** Running order at T: furthest round first. */
export function runningOrder(cars: { id: number; times: (number | null)[] }[], T: number, totalLaps: number,
                             referenceLap: number): Running[] {
  return cars
    .map((c) => ({ id: c.id, state: carState(c.times, T, totalLaps, referenceLap) }))
    .filter((r): r is Running => r.state != null)
    .sort((a, b) => b.state.progress - a.state.progress)
}

/** The replay's length: the last crossing of any car. */
export function replayEnd(trace: RaceTrace): number {
  if (!trace.reference_lap) return 0
  let end = 0
  for (const d of trace.drivers) for (const t of crossingTimes(d, trace.reference_lap)) if (t != null && t > end) end = t
  return end
}

/** Is the race neutralised (SC / VSC / red flag) on this lap? */
export function neutralisedOn(trace: RaceTrace, lap: number): 'SC' | 'VSC' | 'RED_FLAG' | null {
  return trace.neutralised.find((w) => lap >= w.start_lap && lap <= w.end_lap)?.kind ?? null
}
