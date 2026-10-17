/**
 * Riding out a server restart (a deployment, a container coming back): a GET that cannot reach
 * the API, or gets "bad gateway / unavailable / timeout" from it, is tried again with growing
 * pauses for about a minute instead of failing at once. Listeners hear how many requests are
 * waiting, so the page can say it is reconnecting rather than look stuck.
 */

/** Pauses between tries, in ms: about a minute in all. */
export const RETRY_DELAYS = [1000, 2000, 3000, 5000, 8000, 13000, 20000]

/** Statuses a restarting server or its proxy answers with. */
const RETRY_STATUSES = new Set([502, 503, 504])

export function shouldRetry(method: string, outcome: { status?: number; networkError?: boolean }): boolean {
  if (method.toUpperCase() !== 'GET') return false              // never repeat a POST
  return !!outcome.networkError || (outcome.status != null && RETRY_STATUSES.has(outcome.status))
}

let waiting = 0
const listeners = new Set<(n: number) => void>()

export function onReconnecting(fn: (n: number) => void): () => void {
  listeners.add(fn)
  fn(waiting)
  return () => {
    listeners.delete(fn)
  }
}

function setWaiting(delta: number) {
  waiting = Math.max(0, waiting + delta)
  listeners.forEach((fn) => fn(waiting))
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

/** fetch, tried again while the server is restarting. Returns the last response, or throws
    the last network error once the tries run out. */
export async function fetchWithRetry(url: string, init: RequestInit = {}, delays = RETRY_DELAYS): Promise<Response> {
  const method = init.method ?? 'GET'
  let counted = false
  try {
    for (let attempt = 0; ; attempt++) {
      let response: Response | null = null
      let failure: unknown = null
      try {
        response = await fetch(url, init)
      } catch (error) {
        failure = error
      }
      const retry = attempt < delays.length
        && shouldRetry(method, failure ? { networkError: true } : { status: response!.status })
      if (!retry) {
        if (failure) throw failure
        return response!
      }
      if (!counted) {
        counted = true
        setWaiting(+1)
      }
      await sleep(delays[attempt])
    }
  } finally {
    if (counted) setWaiting(-1)
  }
}

/** A different build id on the server means a new version has been deployed. */
export function isNewBuild(current: string, served: unknown): boolean {
  const id = (served as { build?: unknown } | null)?.build
  return typeof id === 'string' && id.length > 0 && id !== current
}
