/**
 * Background music, on by default with its own mute switch (saved in the browser).
 *
 * If an audio file is served at /audio/theme.mp3 (or VITE_MUSIC_URL) it loops that file.
 * Only use a track you have the rights to publish: the official F1 theme and film scores
 * are copyrighted. Without a file it plays "Lights Out", an original score generated live in
 * the browser: a ticking clock, a pulsing bass, low brass hits on the chord changes, a pad
 * and an arpeggio, D minor, in a 16-bar loop.
 *
 * Browsers only allow audio after the first click or key press, so it starts then.
 */
import { readPick, writePick } from '../storage'
import { audioContext, onUnlock } from './sound'

export type MusicState = 'off' | 'waiting' | 'playing'

const TRACK_URL: string = import.meta.env?.VITE_MUSIC_URL ?? '/audio/theme.mp3'
const VOLUME = 0.3
const BPM = 116

let enabled = readPick<boolean>('music') ?? true
let state: MusicState = enabled ? 'waiting' : 'off'
const listeners = new Set<(s: MusicState) => void>()

let bus: GainNode | null = null
let reverb: ConvolverNode | null = null
let echo: DelayNode | null = null
let mode: 'file' | 'generated' | null = null
let fileAudio: HTMLAudioElement | null = null
let timer = 0
let nextTime = 0
let step = 0

function setState(s: MusicState) {
  state = s
  listeners.forEach((fn) => fn(s))
}

export function musicState(): MusicState {
  return state
}

export function onMusicChange(fn: (s: MusicState) => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

export function setMusicEnabled(on: boolean): void {
  enabled = on
  writePick('music', on)
  if (on) {
    setState('waiting')
    void start()
  } else {
    stop()
    setState('off')
  }
}

/** Wire the music to the first click/key press, and pause the generated score in hidden tabs. */
export function installMusic(): () => void {
  const offUnlock = onUnlock(() => void start())
  const visibility = () => {
    if (mode !== 'generated' || !enabled) return
    if (document.hidden) pauseScore()
    else resumeScore()
  }
  document.addEventListener('visibilitychange', visibility)
  return () => {
    offUnlock()
    document.removeEventListener('visibilitychange', visibility)
    stop()
  }
}

async function start() {
  const ctx = audioContext()
  if (!enabled || !ctx || musicState() === 'playing') return
  if (ctx.state !== 'running') await ctx.resume().catch(() => {})
  // re-checked after the await: a click may have changed things meanwhile
  if (ctx.state !== 'running' || !enabled || musicState() === 'playing') return
  setState('playing')
  if (!bus) buildBus(ctx)
  if (mode === null) mode = (await hasTrackFile()) ? 'file' : 'generated'
  if (!enabled) return
  bus!.gain.cancelScheduledValues(ctx.currentTime)
  bus!.gain.setValueAtTime(bus!.gain.value, ctx.currentTime)
  bus!.gain.linearRampToValueAtTime(VOLUME, ctx.currentTime + 2.5)
  if (mode === 'file') {
    if (!fileAudio) {
      fileAudio = new Audio(TRACK_URL)
      fileAudio.loop = true
      ctx.createMediaElementSource(fileAudio).connect(bus!)
    }
    await fileAudio.play().catch(() => setState('waiting'))
  } else {
    resumeScore()
  }
}

function stop() {
  const ctx = audioContext()
  if (!ctx || !bus) return
  bus.gain.cancelScheduledValues(ctx.currentTime)
  bus.gain.setValueAtTime(bus.gain.value, ctx.currentTime)
  bus.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.6)
  window.setTimeout(() => {
    if (enabled) return
    fileAudio?.pause()
    pauseScore()
  }, 650)
}

async function hasTrackFile(): Promise<boolean> {
  try {
    const r = await fetch(TRACK_URL, { method: 'HEAD' })
    // the dev server answers unknown paths with the web page, so check it is really audio
    return r.ok && (r.headers.get('content-type') ?? '').startsWith('audio/')
  } catch {
    return false
  }
}

function buildBus(ctx: AudioContext) {
  bus = ctx.createGain()
  bus.gain.value = 0
  bus.connect(ctx.destination)
  // a dark hall: a decaying noise impulse
  reverb = ctx.createConvolver()
  const len = Math.round(ctx.sampleRate * 2.8)
  const ir = ctx.createBuffer(2, len, ctx.sampleRate)
  for (let c = 0; c < 2; c++) {
    const d = ir.getChannelData(c)
    for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 3.2)
  }
  reverb.buffer = ir
  const wet = ctx.createGain()
  wet.gain.value = 0.32
  reverb.connect(wet).connect(bus)
  // a dotted-eighth echo for the arpeggio
  echo = ctx.createDelay(1)
  echo.delayTime.value = (60 / BPM) * 0.75
  const feedback = ctx.createGain()
  feedback.gain.value = 0.32
  echo.connect(feedback).connect(echo)
  echo.connect(bus)
}

// --- the generated score ---------------------------------------------------------------------

const SIXTEENTH = 60 / BPM / 4
// D minor: i - VI - III - VII, two bars each (MIDI note numbers)
const CHORDS = [
  { root: 38, pad: [50, 53, 57, 62], arp: [62, 65, 69, 74] },
  { root: 34, pad: [46, 53, 58, 62], arp: [58, 62, 65, 70] },
  { root: 41, pad: [45, 53, 57, 60], arp: [65, 69, 72, 77] },
  { root: 36, pad: [48, 52, 55, 60], arp: [60, 64, 67, 72] },
]
const hz = (midi: number) => 440 * Math.pow(2, (midi - 69) / 12)

function resumeScore() {
  const ctx = audioContext()
  if (!ctx || timer) return
  nextTime = ctx.currentTime + 0.1
  timer = window.setInterval(schedule, 30)
}

function pauseScore() {
  window.clearInterval(timer)
  timer = 0
}

function schedule() {
  const ctx = audioContext()
  if (!ctx) return
  while (nextTime < ctx.currentTime + 0.18) {
    playStep(ctx, step, nextTime)
    nextTime += SIXTEENTH
    step = (step + 1) % (16 * 16)
  }
}

function voice(ctx: AudioContext, at: number, peak: number, attack: number, hold: number, release: number, to: AudioNode) {
  const g = ctx.createGain()
  g.gain.setValueAtTime(0.0001, at)
  g.gain.linearRampToValueAtTime(peak, at + attack)
  g.gain.setValueAtTime(peak, at + attack + hold)
  g.gain.exponentialRampToValueAtTime(0.0001, at + attack + hold + release)
  g.connect(to)
  return g
}

function playStep(ctx: AudioContext, s: number, t: number) {
  if (!bus || !reverb || !echo) return
  const bar = Math.floor(s / 16)
  const inBar = s % 16
  const chord = CHORDS[Math.floor(bar / 2) % CHORDS.length]
  const section = bar % 16         // 0-7 verse, 8-15 adds the arpeggio
  const end = t + 8 * SIXTEENTH * 4

  // the clock: an eighth-note tick, accented on the beat
  if (inBar % 2 === 0) {
    const hp = ctx.createBiquadFilter()
    hp.type = 'highpass'
    hp.frequency.value = 6500
    hp.connect(voice(ctx, t, inBar % 4 === 0 ? 0.09 : 0.045, 0.001, 0.004, 0.03, bus))
    noiseBurst(ctx, t, 0.05, hp)
  }

  // pulsing bass, its filter breathing over 8 bars
  if (inBar % 2 === 0) {
    const lp = ctx.createBiquadFilter()
    lp.type = 'lowpass'
    lp.frequency.value = 380 + 520 * (0.5 - 0.5 * Math.cos((2 * Math.PI * (s % 128)) / 128))
    lp.Q.value = 6
    lp.connect(voice(ctx, t, 0.16, 0.005, 0.06, 0.12, bus))
    osc(ctx, 'sawtooth', hz(chord.root), t, 0.25, lp)
  }

  // chord change: low brass hit + boom + pad
  if (inBar === 0 && bar % 2 === 0) {
    const brass = ctx.createBiquadFilter()
    brass.type = 'lowpass'
    brass.frequency.setValueAtTime(180, t)
    brass.frequency.exponentialRampToValueAtTime(section === 0 ? 1800 : 1100, t + 0.35)
    brass.frequency.exponentialRampToValueAtTime(300, t + 2.2)
    const g = voice(ctx, t, section === 0 ? 0.2 : 0.11, 0.04, 0.5, 1.8, bus)
    g.connect(reverb)
    brass.connect(g)
    for (const n of [chord.root - 12, chord.root, chord.root + 7]) {
      osc(ctx, 'sawtooth', hz(n), t, 2.5, brass, -6)
      osc(ctx, 'sawtooth', hz(n), t, 2.5, brass, 6)
    }
    const boom = voice(ctx, t, 0.3, 0.005, 0.05, 1.1, bus)
    const o = ctx.createOscillator()
    o.frequency.setValueAtTime(62, t)
    o.frequency.exponentialRampToValueAtTime(34, t + 1)
    o.connect(boom)
    o.start(t)
    o.stop(t + 1.3)

    const pad = ctx.createBiquadFilter()
    pad.type = 'lowpass'
    pad.frequency.value = 900
    const pg = voice(ctx, t, 0.022, 1.2, end - t - 2.2, 1.6, bus)
    pg.connect(reverb)
    pad.connect(pg)
    for (const n of chord.pad) {
      osc(ctx, 'sawtooth', hz(n), t, end - t + 0.5, pad, -8)
      osc(ctx, 'sawtooth', hz(n), t, end - t + 0.5, pad, 8)
    }
  }

  // the arpeggio, second half of the loop
  if (section >= 8) {
    const lp = ctx.createBiquadFilter()
    lp.type = 'lowpass'
    lp.frequency.value = 2400
    const g = voice(ctx, t, 0.03, 0.004, 0.03, 0.12, bus)
    g.connect(echo)
    lp.connect(g)
    osc(ctx, 'square', hz(chord.arp[[0, 1, 2, 3, 2, 1, 2, 3][inBar % 8]]), t, 0.2, lp)
  }

  // a riser into the top of the loop
  if (section === 15 && inBar === 0) {
    const bp = ctx.createBiquadFilter()
    bp.type = 'bandpass'
    bp.Q.value = 2
    bp.frequency.setValueAtTime(200, t)
    bp.frequency.exponentialRampToValueAtTime(7000, t + 16 * SIXTEENTH)
    const g = ctx.createGain()
    g.gain.setValueAtTime(0.0001, t)
    g.gain.exponentialRampToValueAtTime(0.12, t + 16 * SIXTEENTH)
    g.gain.exponentialRampToValueAtTime(0.0001, t + 16 * SIXTEENTH + 0.05)
    g.connect(bus)
    bp.connect(g)
    noiseBurst(ctx, t, 16 * SIXTEENTH + 0.1, bp)
  }
}

function osc(ctx: AudioContext, type: OscillatorType, freq: number, at: number, length: number, to: AudioNode, detune = 0) {
  const o = ctx.createOscillator()
  o.type = type
  o.frequency.value = freq
  o.detune.value = detune
  o.connect(to)
  o.start(at)
  o.stop(at + length)
}

let noise: AudioBuffer | null = null
function noiseBurst(ctx: AudioContext, at: number, length: number, to: AudioNode) {
  if (!noise) {
    noise = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate)
    const d = noise.getChannelData(0)
    for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1
  }
  const src = ctx.createBufferSource()
  src.buffer = noise
  src.connect(to)
  src.start(at)
  src.stop(at + length)
}
