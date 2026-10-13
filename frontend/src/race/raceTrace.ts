/** Race trace arithmetic: which cars to draw, the axis range and the running order on a lap. */

import type { RaceTrace, RaceTraceDriver } from '../types.ts'

export type TraceMode = 'gap' | 'trace'
export type Preset = 'top5' | 'top10' | 'all'

/** Drivers come in finishing order; a preset is the first N of them. */
export function presetIds(drivers: RaceTraceDriver[], preset: Preset): number[] {
  const n = preset === 'top5' ? 5 : preset === 'top10' ? 10 : drivers.length
  return drivers.slice(0, n).map((d) => d.driver_id)
}

/** A team's second car (in finishing order) is drawn dashed. Decided over the whole field so a
    filter never restyles a line. */
export function dashedIds(drivers: RaceTraceDriver[]): Set<number> {
  const seen = new Set<string>()
  const out = new Set<number>()
  for (const d of drivers) {
    const team = d.team_name ?? `#${d.driver_id}`
    if (seen.has(team)) out.add(d.driver_id)
    seen.add(team)
  }
  return out
}

export function valueAt(d: RaceTraceDriver, mode: TraceMode, lap: number): number | null {
  return (mode === 'gap' ? d.gap : d.trace)[lap - 1] ?? null
}

/** Round axis bounds and ticks: gap runs 0 (the leader) down to the largest gap; trace runs
    from the lowest value to at least 0 (the winner's average pace). */
export function axis(drivers: RaceTraceDriver[], mode: TraceMode, count = 5): { lo: number; hi: number; ticks: number[] } {
  const values = drivers.flatMap((d) => (mode === 'gap' ? d.gap : d.trace)).filter((v): v is number => v != null)
  let lo = mode === 'gap' ? 0 : Math.min(0, ...values)
  let hi = Math.max(mode === 'gap' ? 1 : 0, ...values)
  const step = niceStep((hi - lo) / count)
  lo = Math.floor(lo / step) * step
  hi = Math.ceil(hi / step) * step
  if (hi === lo) hi = lo + step
  const ticks: number[] = []
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Math.round(v * 1000) / 1000)
  return { lo, hi, ticks }
}

function niceStep(raw: number): number {
  if (!(raw > 0)) return 1
  const p = 10 ** Math.floor(Math.log10(raw))
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((m) => m >= raw) ?? raw
}

export interface OrderRow {
  driver: RaceTraceDriver
  value: number
  /** seconds to the car ahead on this lap (gap mode), null for the first row */
  interval: number | null
  pitted: { lap: number; compound: string | null } | undefined
}

/** The running order at the end of a lap among the given drivers, front first. */
export function orderAt(drivers: RaceTraceDriver[], mode: TraceMode, lap: number): OrderRow[] {
  const rows = drivers
    .map((driver) => ({ driver, value: valueAt(driver, mode, lap), gap: valueAt(driver, 'gap', lap) }))
    .filter((r): r is { driver: RaceTraceDriver; value: number; gap: number } => r.value != null && r.gap != null)
    .sort((a, b) => a.gap - b.gap)
  return rows.map((r, i) => ({
    driver: r.driver, value: r.value,
    interval: i === 0 ? null : Math.round((r.gap - rows[i - 1].gap) * 1000) / 1000,
    pitted: r.driver.pits.find((p) => p.lap === lap),
  }))
}

/** The Safety Car / VSC / red flag on this lap, if any (red flag first). */
export function neutralAt(trace: RaceTrace, lap: number): 'SC' | 'VSC' | 'RED_FLAG' | null {
  const on = trace.neutralised.filter((e) => e.start_lap <= lap && lap <= e.end_lap).map((e) => e.kind)
  return on.includes('RED_FLAG') ? 'RED_FLAG' : on.includes('SC') ? 'SC' : on.includes('VSC') ? 'VSC' : null
}

export function formatValue(v: number, mode: TraceMode): string {
  if (mode === 'gap') return v === 0 ? 'Leader' : `+${v.toFixed(3)}`
  return `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(1)}s`
}

export function formatTick(v: number, mode: TraceMode): string {
  if (mode === 'gap') return v === 0 ? '0' : `+${v % 1 ? v.toFixed(1) : v}s`
  return v === 0 ? '0' : `${v > 0 ? '+' : '−'}${Math.abs(v) % 1 ? Math.abs(v).toFixed(1) : Math.abs(v)}s`
}
