/**
 * The site's one AudioContext (used by the background music). Browsers only allow audio
 * after the visitor's first click or key press, so it is created then.
 */
let ctx: AudioContext | null = null
const unlockListeners = new Set<() => void>()

export function audioContext(): AudioContext | null {
  return ctx
}

export function unlockAudio(): void {
  if (!ctx) {
    const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!AC) return
    ctx = new AC()
  }
  if (ctx.state === 'suspended') void ctx.resume()
  unlockListeners.forEach((fn) => fn())
}

/** Runs after every unlock (first click / key press): the music starts from here. */
export function onUnlock(fn: () => void): () => void {
  unlockListeners.add(fn)
  return () => unlockListeners.delete(fn)
}

/** Unlock audio on the first gesture anywhere on the page. */
export function installAudioUnlock(): () => void {
  const unlock = () => unlockAudio()
  document.addEventListener('pointerdown', unlock, { capture: true, passive: true })
  document.addEventListener('keydown', unlock, { capture: true })
  return () => {
    document.removeEventListener('pointerdown', unlock, { capture: true })
    document.removeEventListener('keydown', unlock, { capture: true })
  }
}
