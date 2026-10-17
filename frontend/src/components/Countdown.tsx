import { useEffect, useState } from 'react'

import { countdownText, nextSession } from '../countdown'
import type { WeekendSession } from '../types'

/** A ticking countdown to the next session of the weekend, in the visitor's own time zone. */
export function Countdown({ sessions }: { sessions: WeekendSession[] }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [])
  const n = nextSession(sessions, now)
  if (!n) return null
  const when = new Date(n.start).toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' })
  return (
    <div className={`countdown ${n.live ? 'live' : ''}`} title={`${n.s.name}: ${when} your time`}>
      <span className="cd-label">{n.live ? 'Live now' : `Next · ${n.s.name}`}</span>
      <b className="num">{n.live ? n.s.name : countdownText(n.start - now)}</b>
      <span className="cd-when">{when} your time</span>
    </div>
  )
}
