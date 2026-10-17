/**
 * After a deployment: an open page notices the new version and switches to it, and a page that
 * asks for a file of the old version (gone after the deploy) reloads once instead of hanging.
 */

import { isNewBuild } from './net'

const CHECK_EVERY = 5 * 60_000
let ready = false
const listeners = new Set<() => void>()

/** True once a newer version has been deployed than the one this page runs. */
export function updateReady(): boolean {
  return ready
}

export function onUpdateReady(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

async function check() {
  if (ready) return
  try {
    const r = await fetch('/version.json', { cache: 'no-store' })
    if (!r.ok) return
    if (isNewBuild(__BUILD_ID__, await r.json())) {
      ready = true
      // nobody is looking at a background tab: switch now; otherwise at the next page change
      if (document.hidden) window.location.reload()
      else listeners.forEach((fn) => fn())
    }
  } catch {
    /* offline, or the server is restarting: the next check will tell */
  }
}

/** Reload once (not in a loop) when a file of an older version cannot be loaded. */
function reloadOnStaleFiles() {
  window.addEventListener('vite:preloadError', (event) => {
    let last = 0
    try {
      last = Number(window.sessionStorage.getItem('apex21:stale-reload') ?? 0)
      window.sessionStorage.setItem('apex21:stale-reload', String(Date.now()))
    } catch {
      /* storage blocked: still reload once */
    }
    if (Date.now() - last > 30_000) {
      event.preventDefault()
      window.location.reload()
    }
  })
}

let started = false

export function startUpdateWatch(): void {
  if (import.meta.env.DEV || started) return
  started = true
  reloadOnStaleFiles()
  window.setInterval(check, CHECK_EVERY)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') void check()
    else if (ready) window.location.reload()
  })
  window.addEventListener('focus', () => void check())
}
