import { useEffect, useState } from 'react'

export interface AsyncState<T> {
  data: T | undefined
  error: Error | undefined
  loading: boolean
}

/** Runs `load` whenever `deps` change; ignores results from superseded runs. */
export function useAsync<T>(load: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({ data: undefined, error: undefined, loading: true })
  useEffect(() => {
    let current = true
    setState({ data: undefined, error: undefined, loading: true })
    load().then(
      (data) => current && setState({ data, error: undefined, loading: false }),
      (error: Error) => current && setState({ data: undefined, error, loading: false }),
    )
    return () => {
      current = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return state
}

export interface LiveState<T> extends AsyncState<T> {
  /** When the data last arrived (ms since epoch). */
  updatedAt: number | null
}

/**
 * Like useAsync, but fetches again every `everyMs` (and when the tab becomes visible again),
 * keeping the old data on screen while it refreshes: pages stay current through a race
 * weekend without a reload. A failed refresh keeps the last good data. A refresh is skipped
 * while the previous one is still waiting, so a slow API never gets a pile of repeat requests.
 */
export function useLive<T>(load: () => Promise<T>, deps: unknown[], everyMs = 60_000): LiveState<T> {
  const [state, setState] = useState<LiveState<T>>({ data: undefined, error: undefined, loading: true, updatedAt: null })
  useEffect(() => {
    let current = true
    let timer = 0
    let busy = false
    setState({ data: undefined, error: undefined, loading: true, updatedAt: null })
    const run = () => {
      if (busy) return
      busy = true
      load().then(
        (data) => current && setState({ data, error: undefined, loading: false, updatedAt: Date.now() }),
        (error: Error) => current && setState((s) => (s.data !== undefined ? { ...s, loading: false } : { data: undefined, error, loading: false, updatedAt: null })),
      ).finally(() => {
        busy = false
      })
    }
    run()
    timer = window.setInterval(() => document.visibilityState === 'visible' && run(), everyMs)
    const onVisible = () => document.visibilityState === 'visible' && run()
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      current = false
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return state
}
