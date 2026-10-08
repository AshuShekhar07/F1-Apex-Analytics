import { useEffect, useRef } from 'react'

import { gsap, reducedMotion } from '../motion/gsap'
import { scrollVelocity } from '../motion/SmoothScroll'

/**
 * An endless ticker between kerb stripes. It runs faster the faster the page scrolls and
 * turns around when the scroll direction does.
 */
export function Marquee({ items }: { items: string[] }) {
  const row = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = row.current
    if (!el || reducedMotion()) return
    let x = 0
    let dir = 1
    const tick = (_t: number, deltaMs: number) => {
      const half = el.scrollWidth / 2
      if (!half) return
      const v = scrollVelocity()
      if (Math.abs(v) > 0.5) dir = v > 0 ? 1 : -1
      x -= dir * (1.1 + Math.min(Math.abs(v), 50) * 0.35) * (deltaMs / 16.7)
      if (x <= -half) x += half
      if (x > 0) x -= half
      el.style.transform = `translate3d(${x}px, 0, 0)`
    }
    gsap.ticker.add(tick)
    return () => gsap.ticker.remove(tick)
  }, [])

  const content = items.map((t, i) => (
    <span className="mq-item" key={i}>
      {t}
      <i aria-hidden="true" />
    </span>
  ))
  return (
    <div className="marquee" aria-label={items.join('. ')}>
      <div className="kerb" aria-hidden="true" />
      <div className="mq-window" aria-hidden="true">
        <div className="mq-row" ref={row}>
          {content}
          {content}
        </div>
      </div>
      <div className="kerb" aria-hidden="true" />
    </div>
  )
}
