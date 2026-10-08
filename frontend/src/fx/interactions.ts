/**
 * Site-wide hover behaviour, installed once with event delegation (no per-component code):
 *   magnetic  .btn-sharp / [data-magnetic] lean toward the pointer and spring back
 *   decode    nav links, buttons, chips and [data-scramble] re-type their label on hover
 *   spotlight cards ([data-spot] and the listed classes) get --sx/--sy: a light that follows
 *             the pointer, painted by CSS
 * Mouse only; under reduced motion only the spotlight stays.
 */
import { finePointer, gsap, reducedMotion } from '../motion/gsap'
import { scrambleElement } from '../motion/scramble'

const MAGNETIC = '.btn-sharp, [data-magnetic]'
const DECODE = '.top-bar nav a, .btn-sharp, .chip, [data-scramble]'
const SPOT = '[data-spot], .pick-tile, .driver-card, .feature-panel, .track-list button, .stat-cell, .tower-row'

interface Magnet {
  x: (v: number) => void
  y: (v: number) => void
}

export function installInteractions(): () => void {
  if (!finePointer()) return () => {}
  const motion = !reducedMotion()
  const magnets = new WeakMap<Element, Magnet>()
  let magnet: HTMLElement | null = null
  let decoded: Element | null = null

  const release = (el: HTMLElement) => gsap.to(el, { x: 0, y: 0, duration: 0.8, ease: 'elastic.out(1, 0.35)', overwrite: 'auto' })

  const move = (e: PointerEvent) => {
    if (e.pointerType !== 'mouse') return
    const target = e.target as Element | null

    const spot = target?.closest?.(SPOT) as HTMLElement | null
    if (spot) {
      const r = spot.getBoundingClientRect()
      spot.style.setProperty('--sx', `${e.clientX - r.left}px`)
      spot.style.setProperty('--sy', `${e.clientY - r.top}px`)
    }
    if (!motion) return

    const m = target?.closest?.(MAGNETIC) as HTMLElement | null
    if (m !== magnet) {
      if (magnet) release(magnet)
      magnet = m
    }
    if (m && !(m as HTMLButtonElement).disabled) {
      let q = magnets.get(m)
      if (!q) {
        q = { x: gsap.quickTo(m, 'x', { duration: 0.4, ease: 'power3' }), y: gsap.quickTo(m, 'y', { duration: 0.4, ease: 'power3' }) }
        magnets.set(m, q)
      }
      const r = m.getBoundingClientRect()
      const dx = e.clientX - (r.left + r.width / 2)
      const dy = e.clientY - (r.top + r.height / 2)
      q.x(Math.max(-14, Math.min(14, dx * 0.22)))
      q.y(Math.max(-9, Math.min(9, dy * 0.3)))
    }

    const d = target?.closest?.(DECODE) ?? null
    if (d !== decoded) {
      decoded = d
      if (d && !(d as HTMLButtonElement).disabled) scrambleElement(d, 0.38)
    }
  }

  const leaveWindow = (e: PointerEvent) => {
    if (e.relatedTarget) return
    if (magnet) release(magnet)
    magnet = null
    decoded = null
  }

  document.addEventListener('pointermove', move, { passive: true })
  document.addEventListener('pointerout', leaveWindow, { passive: true })
  return () => {
    document.removeEventListener('pointermove', move)
    document.removeEventListener('pointerout', leaveWindow)
  }
}
