/**
 * Technical UI sounds, synthesised with the Web Audio API (no audio files): a relay tick on
 * hover, a two-tone blip on click, a whoosh between pages, a thump when a pick is locked in,
 * start-light beeps and an engine rev at lights out.
 *
 * Browsers only allow audio after the visitor's first click or key press, so the audio
 * context is created then; until that, play() does nothing. The sound-effects on/off choice
 * is saved in the browser (the background music has its own switch, see music.ts).
 */
import { readPick, writePick } from '../storage'

export type Sound = 'tick' | 'select' | 'whoosh' | 'lock' | 'beep' | 'go' | 'toggle'

let ctx: AudioContext | null = null
let out: GainNode | null = null
let noiseBuffer: AudioBuffer | null = null
let enabled = readPick<boolean>('sound') ?? true
const listeners = new Set<(on: boolean) => void>()
const unlockListeners = new Set<() => void>()

export function soundEnabled(): boolean {
  return enabled
}

export function setSoundEnabled(on: boolean): void {
  enabled = on
  writePick('sound', on)
  listeners.forEach((fn) => fn(on))
  if (on) {
    unlockAudio()
    play('toggle')
  }
}

export function onSoundChange(fn: (on: boolean) => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

/** The site's one AudioContext (also used by the music), created on the first gesture. */
export function audioContext(): AudioContext | null {
  return ctx
}

/** Called on the first click / key press: the only moment browsers let audio start. */
export function unlockAudio(): void {
  if (!ctx) {
    const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!AC) return
    ctx = new AC()
    out = ctx.createGain()
    out.gain.value = 0.5
    const limiter = ctx.createDynamicsCompressor()
    limiter.threshold.value = -12
    out.connect(limiter).connect(ctx.destination)
    noiseBuffer = ctx.createBuffer(1, ctx.sampleRate, ctx.sampleRate)
    const data = noiseBuffer.getChannelData(0)
    for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1
  }
  if (ctx.state === 'suspended') void ctx.resume()
  unlockListeners.forEach((fn) => fn())
}


/** Runs after every unlock (first click / key press): the music starts from here. */
export function onUnlock(fn: () => void): () => void {
  unlockListeners.add(fn)
  return () => unlockListeners.delete(fn)
}

function ready(): AudioContext | null {
  return enabled && ctx && out && ctx.state === 'running' ? ctx : null
}

/** A gain envelope: quick attack, exponential release. */
function envelope(c: AudioContext, at: number, peak: number, attack: number, release: number): GainNode {
  const g = c.createGain()
  g.gain.setValueAtTime(0.0001, at)
  g.gain.exponentialRampToValueAtTime(peak, at + attack)
  g.gain.exponentialRampToValueAtTime(0.0001, at + attack + release)
  g.connect(out!)
  return g
}

function tone(c: AudioContext, type: OscillatorType, from: number, to: number, at: number, length: number, gain: GainNode) {
  const o = c.createOscillator()
  o.type = type
  o.frequency.setValueAtTime(from, at)
  if (to !== from) o.frequency.exponentialRampToValueAtTime(to, at + length)
  o.connect(gain)
  o.start(at)
  o.stop(at + length + 0.05)
}

function noise(c: AudioContext, at: number, length: number, filter: BiquadFilterNode) {
  const s = c.createBufferSource()
  s.buffer = noiseBuffer
  s.connect(filter)
  s.start(at)
  s.stop(at + length)
}

export function play(sound: Sound): void {
  const c = ready()
  if (!c) return
  const t = c.currentTime + 0.005
  switch (sound) {
    case 'tick': {
      // a crisp relay click, slightly different every time
      const g = envelope(c, t, 0.09, 0.002, 0.035)
      const bp = c.createBiquadFilter()
      bp.type = 'bandpass'
      bp.frequency.value = 3200
      bp.Q.value = 6
      bp.connect(g)
      const o = c.createOscillator()
      o.type = 'square'
      o.frequency.value = 2900 + Math.random() * 600
      o.connect(bp)
      o.start(t)
      o.stop(t + 0.05)
      break
    }
    case 'select': {
      tone(c, 'triangle', 1400, 1400, t, 0.04, envelope(c, t, 0.16, 0.003, 0.04))
      tone(c, 'triangle', 2100, 2100, t + 0.05, 0.05, envelope(c, t + 0.05, 0.14, 0.003, 0.05))
      break
    }
    case 'toggle': {
      tone(c, 'sine', 900, 1800, t, 0.12, envelope(c, t, 0.14, 0.005, 0.12))
      break
    }
    case 'whoosh': {
      const g = envelope(c, t, 0.22, 0.14, 0.38)
      const bp = c.createBiquadFilter()
      bp.type = 'bandpass'
      bp.Q.value = 1.4
      bp.frequency.setValueAtTime(350, t)
      bp.frequency.exponentialRampToValueAtTime(4200, t + 0.22)
      bp.frequency.exponentialRampToValueAtTime(700, t + 0.52)
      const pan = c.createStereoPanner()
      pan.pan.setValueAtTime(-0.8, t)
      pan.pan.linearRampToValueAtTime(0.8, t + 0.5)
      bp.connect(pan).connect(g)
      noise(c, t, 0.6, bp)
      break
    }
    case 'lock': {
      tone(c, 'sine', 150, 50, t, 0.28, envelope(c, t, 0.45, 0.004, 0.3))
      const hp = c.createBiquadFilter()
      hp.type = 'highpass'
      hp.frequency.value = 2500
      hp.connect(envelope(c, t, 0.18, 0.001, 0.03))
      noise(c, t, 0.05, hp)
      tone(c, 'square', 1200, 1200, t + 0.06, 0.06, envelope(c, t + 0.06, 0.05, 0.002, 0.06))
      break
    }
    case 'beep': {
      const lp = c.createBiquadFilter()
      lp.type = 'lowpass'
      lp.frequency.value = 2600
      lp.connect(envelope(c, t, 0.12, 0.004, 0.11))
      const o = c.createOscillator()
      o.type = 'square'
      o.frequency.value = 1046
      o.connect(lp)
      o.start(t)
      o.stop(t + 0.14)
      break
    }
    case 'go': {
      // lights out: an engine rev (two detuned saws through a sweeping low-pass) plus air
      const g = envelope(c, t, 0.3, 0.05, 0.75)
      const lp = c.createBiquadFilter()
      lp.type = 'lowpass'
      lp.Q.value = 4
      lp.frequency.setValueAtTime(500, t)
      lp.frequency.exponentialRampToValueAtTime(2600, t + 0.5)
      lp.connect(g)
      for (const detune of [-9, 9]) {
        const o = c.createOscillator()
        o.type = 'sawtooth'
        o.detune.value = detune
        o.frequency.setValueAtTime(70, t)
        o.frequency.exponentialRampToValueAtTime(390, t + 0.55)
        o.frequency.exponentialRampToValueAtTime(300, t + 0.8)
        o.connect(lp)
        o.start(t)
        o.stop(t + 0.85)
      }
      const bp = c.createBiquadFilter()
      bp.type = 'bandpass'
      bp.frequency.setValueAtTime(800, t)
      bp.frequency.exponentialRampToValueAtTime(5000, t + 0.6)
      bp.connect(envelope(c, t, 0.1, 0.2, 0.5))
      noise(c, t, 0.75, bp)
      break
    }
  }
}

const INTERACTIVE = 'a[href], button, [role="button"], select, summary, [data-sound]'

/** One listener for the whole site: tick on hovering anything clickable, blip on clicking it.
    data-sound="off" silences an element; data-sound-click="lock" picks its click sound. */
export function installSoundDelegation(): () => void {
  let hovered: Element | null = null
  let lastTick = 0
  const over = (e: PointerEvent) => {
    if (e.pointerType !== 'mouse') return
    const el = (e.target as Element | null)?.closest?.(INTERACTIVE) ?? null
    if (el === hovered) return
    hovered = el
    if (!el || el.getAttribute('data-sound') === 'off' || (el as HTMLButtonElement).disabled) return
    const now = performance.now()
    if (now - lastTick < 35) return
    lastTick = now
    play('tick')
  }
  const click = (e: MouseEvent) => {
    const el = (e.target as Element | null)?.closest?.(INTERACTIVE)
    if (!el || el.getAttribute('data-sound') === 'off' || (el as HTMLButtonElement).disabled) return
    play((el.getAttribute('data-sound-click') as Sound | null) ?? 'select')
  }
  const unlock = () => unlockAudio()
  document.addEventListener('pointerover', over, { passive: true })
  document.addEventListener('pointerdown', unlock, { capture: true, passive: true })
  document.addEventListener('keydown', unlock, { capture: true })
  document.addEventListener('click', click, { capture: true })
  return () => {
    document.removeEventListener('pointerover', over)
    document.removeEventListener('pointerdown', unlock, { capture: true })
    document.removeEventListener('keydown', unlock, { capture: true })
    document.removeEventListener('click', click, { capture: true })
  }
}
