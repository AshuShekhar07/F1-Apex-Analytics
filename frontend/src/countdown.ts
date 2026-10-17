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
