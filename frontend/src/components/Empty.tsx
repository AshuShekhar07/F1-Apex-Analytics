import type { ReactNode } from 'react'

import { sessionWait } from '../countdown'
import type { WeekendSession } from '../types'

type Icon = 'lights' | 'live' | 'flag' | 'tyre' | 'track'

/** A friendly empty state: a small racing glyph, a headline and a line on what will appear here. */
export function Empty({ icon = 'flag', title, children, panel = false }: {
  icon?: Icon
  title: string
  children?: ReactNode
  panel?: boolean
}) {
  return (
    <div className={`empty-state ${panel ? 'panel' : ''}`}>
      <span className={`es-icon es-${icon}`} aria-hidden="true">
        {(icon === 'lights' || icon === 'live') && <><i /><i /><i /><i /><i /></>}
      </span>
      <b className="es-title">{title}</b>
      {children && <p className="es-text">{children}</p>}
    </div>
  )
}

/** Carbon-fibre placeholder rows while a panel's data loads. */
export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="skeleton" role="status" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => <i key={i} style={{ width: `${94 - ((i * 23) % 38)}%` }} />)}
    </div>
  )
}

const when = (ms: number) => new Date(ms).toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' })

/** An empty session panel: when the session runs, that it is running, or that its data is on its
    way; `none` when nothing about the session is known. */
export function SessionWait({ sessions, types, what, none }: {
  sessions: WeekendSession[] | null | undefined
  types: string[]
  what: string
  none: string
}) {
  const w = sessionWait(sessions, types, Date.now())
  if (w.state === 'upcoming') {
    return <Empty icon="lights" title={`${w.name} hasn't run yet`}>Starts {when(w.start)} your time. {what} will show here once its data is in.</Empty>
  }
  if (w.state === 'running') {
    return <Empty icon="live" title={`${w.name} is on now`}>{what} will show here once its data is in.</Empty>
  }
  if (w.state === 'loading') {
    return <Empty icon="flag" title={`${w.name} has finished`}>{what} will show here as soon as its data is loaded.</Empty>
  }
  return <Empty icon="tyre" title={none} />
}
