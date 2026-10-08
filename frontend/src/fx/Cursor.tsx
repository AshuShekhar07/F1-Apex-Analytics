import { useEffect, useRef } from 'react'

import { finePointer, gsap } from '../motion/gsap'

const HOT = 'a[href], button, [role="button"], select, summary, [data-cursor]'

/**
 * A rhombus reticle that trails the pointer (mouse only). It opens up over anything
 * clickable and shows that element's data-cursor label ("Pick", "Drag", ...). It also
 * publishes the pointer position as --mx / --my for the backdrop's pointer glow.
 */
export function Cursor() {
  const ring = useRef<HTMLDivElement>(null)
  const dot = useRef<HTMLDivElement>(null)
  const label = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    if (!finePointer() || !ring.current || !dot.current || !label.current) return
    const root = document.documentElement
    root.classList.add('has-cursor')
    const r = ring.current
    const d = dot.current
    const l = label.current
    const ringX = gsap.quickTo(r, 'x', { duration: 0.38, ease: 'power3' })
    const ringY = gsap.quickTo(r, 'y', { duration: 0.38, ease: 'power3' })
    const dotX = gsap.quickTo(d, 'x', { duration: 0.06, ease: 'power3' })
    const dotY = gsap.quickTo(d, 'y', { duration: 0.06, ease: 'power3' })
    let hot: Element | null = null
    let frame = 0
    let px = 0
    let py = 0

    const move = (e: PointerEvent) => {
      if (e.pointerType !== 'mouse') return
      ringX(e.clientX)
      ringY(e.clientY)
      dotX(e.clientX)
      dotY(e.clientY)
      px = e.clientX
      py = e.clientY
      if (!frame) {
        frame = requestAnimationFrame(() => {
          frame = 0
          root.style.setProperty('--mx', `${px}px`)
          root.style.setProperty('--my', `${py}px`)
        })
      }
      root.classList.remove('cursor-away')
      const el = (e.target as Element | null)?.closest?.(HOT) ?? null
      if (el !== hot) {
        hot = el
        r.classList.toggle('hot', !!el)
        l.textContent = el?.getAttribute('data-cursor') ?? ''
        r.classList.toggle('labelled', !!l.textContent)
      }
    }
    const down = () => gsap.to(r, { scale: 0.7, duration: 0.12, overwrite: 'auto' })
    const up = () => gsap.to(r, { scale: 1, duration: 0.5, ease: 'elastic.out(1, 0.4)', overwrite: 'auto' })
    const out = (e: PointerEvent) => {
      if (!e.relatedTarget) root.classList.add('cursor-away')
    }
    document.addEventListener('pointermove', move, { passive: true })
    document.addEventListener('pointerdown', down, { passive: true })
    document.addEventListener('pointerup', up, { passive: true })
    document.addEventListener('pointerout', out, { passive: true })
    return () => {
      root.classList.remove('has-cursor', 'cursor-away')
      cancelAnimationFrame(frame)
      document.removeEventListener('pointermove', move)
      document.removeEventListener('pointerdown', down)
      document.removeEventListener('pointerup', up)
      document.removeEventListener('pointerout', out)
    }
  }, [])

  return (
    <div className="cursor" aria-hidden="true">
      <div className="cursor-ring" ref={ring}>
        <i />
        <span ref={label} />
      </div>
      <div className="cursor-dot" ref={dot} />
    </div>
  )
}
