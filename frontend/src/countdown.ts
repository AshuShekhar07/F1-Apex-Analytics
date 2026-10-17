/** Countdown arithmetic for the next session of a race weekend. Pure. */

import type { WeekendSession } from './types'

/** "1d 04:12:33" / "04:12:33". */
export function countdownText(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000))
  const d = Math.floor(s / 86400)
  const hh = String(Math.floor((s % 86400) / 3600)).padStart(2, '0')
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, '0')
  const ss = String(s % 60).padStart(2, '0')
  return `${d ? `${d}d ` : ''}${hh}:${mm}:${ss}`
}

/** The next session with a known start time, or one running now (within its first 2 hours). */
export function nextSession(sessions: WeekendSession[], now: number): { s: WeekendSession; start: number; live: boolean } | null {
  const timed = sessions.filter((x) => x.start_time_utc).map((x) => ({ s: x, start: Date.parse(`${x.start_time_utc}Z`) }))
    .filter((x) => Number.isFinite(x.start)).sort((a, b) => a.start - b.start)
  const live = timed.find((x) => x.start <= now && now < x.start + 2 * 3600_000 && !x.s.loaded)
  if (live) return { ...live, live: true }
  const next = timed.find((x) => x.start > now)
  return next ? { ...next, live: false } : null
}

export type SessionWaitState =
  | { state: 'upcoming' | 'running' | 'loading'; name: string; start: number }
  | { state: 'unknown' }

/** Why a session panel is empty: the first of these session types (weekend order) without data
    is still to come, running now (its first 2 hours), or has finished and its data is not in yet;
    'unknown' when there is no such session or its start time is not known. */
export function sessionWait(sessions: WeekendSession[] | null | undefined, types: string[], now: number): SessionWaitState {
  const s = sessions?.find((x) => types.includes(x.type) && !x.has_laps && !x.has_results)
  const start = s?.start_time_utc ? Date.parse(`${s.start_time_utc}Z`) : NaN
  if (!s || !Number.isFinite(start)) return { state: 'unknown' }
  const state = now < start ? 'upcoming' : now < start + 2 * 3600_000 ? 'running' : 'loading'
  return { state, name: s.name, start }
}
