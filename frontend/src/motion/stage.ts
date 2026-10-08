/**
 * Timing shared by the page animations: while the intro (or a page-change wipe) covers the
 * screen, entrance animations wait, so the visitor actually sees them play.
 */
let introActive = false
const waiting: (() => void)[] = []

/** Seconds a page's entrance waits after it mounts: the page-change wipe still covers it. */
export const PAGE_ENTER_DELAY = 0.32

export function setIntroActive(active: boolean): void {
  introActive = active
  if (!active) waiting.splice(0).forEach((fn) => fn())
}

/** Run now, or as soon as the intro has gone. Returns a cancel function. */
export function afterIntro(fn: () => void): () => void {
  if (!introActive) {
    fn()
    return () => {}
  }
  waiting.push(fn)
  return () => {
    const i = waiting.indexOf(fn)
    if (i >= 0) waiting.splice(i, 1)
  }
}
