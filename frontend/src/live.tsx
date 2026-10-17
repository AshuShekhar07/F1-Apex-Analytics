import { createContext, useContext, type ReactNode } from 'react'

import { api } from './api'
import { type LiveState, useLive } from './hooks'
import type { Health, RaceSessions, WinnerPrediction } from './types'

interface LiveWeekend {
  /** The next race's winner prediction (null when the calendar has no race left). */
  prediction: LiveState<WinnerPrediction | null>
  /** That weekend's sessions: which have run and are in the database. */
  sessions: LiveState<RaceSessions | null>
  health: LiveState<Health>
}

const Ctx = createContext<LiveWeekend | null>(null)

/** One poller for the whole site: the header strip and the race page share the same data,
    refreshed every minute, so a session loaded by the pipeline shows up without a reload. */
export function LiveWeekendProvider({ children }: { children: ReactNode }) {
  const prediction = useLive(() => api.nextWinnerPrediction(), [], 60_000)
  const raceId = prediction.data?.race_id
  const sessions = useLive(() => (raceId ? api.raceSessions(raceId) : Promise.resolve(null)), [raceId], 60_000)
  const health = useLive(() => api.health(), [], 60_000)
  return <Ctx.Provider value={{ prediction, sessions, health }}>{children}</Ctx.Provider>
}

export function useLiveWeekend(): LiveWeekend {
  const v = useContext(Ctx)
  if (!v) throw new Error('useLiveWeekend outside LiveWeekendProvider')
  return v
}
