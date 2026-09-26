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
