/** localStorage that never throws (private windows, blocked storage): a game pick is a convenience. */
export function readPick<T>(key: string): T | null {
  try {
    const raw = window.localStorage.getItem(`apex21:${key}`)
    return raw ? (JSON.parse(raw) as T) : null
  } catch {
    return null
  }
}

export function writePick<T>(key: string, value: T | null): void {
  try {
    if (value === null) window.localStorage.removeItem(`apex21:${key}`)
    else window.localStorage.setItem(`apex21:${key}`, JSON.stringify(value))
  } catch {
    /* storage unavailable: the pick just isn't remembered */
  }
}

export function sessionFlag(key: string): boolean {
  try {
    return window.sessionStorage.getItem(`apex21:${key}`) === '1'
  } catch {
    return false
  }
}

export function setSessionFlag(key: string): void {
  try {
    window.sessionStorage.setItem(`apex21:${key}`, '1')
  } catch {
    /* ignore */
  }
}

/** API-relative asset paths (e.g. /static/team_logos/3.png) go through the API base. */
export function assetUrl(url: string | null | undefined): string | null {
  if (!url) return null
  if (/^https?:\/\//.test(url) || url.startsWith('data:')) return url
  const base = import.meta.env?.VITE_API_BASE ?? '/api'
  return `${base}${url.startsWith('/') ? '' : '/'}${url}`
}
