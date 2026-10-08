import { useEffect, useRef } from 'react'

import { gsap } from '../motion/gsap'

const LEDS = 15

/** How many of the 15 shift lights are lit at this scroll position (0..1). */
function litLeds(progress: number): number {
  return Math.round(Math.max(0, Math.min(1, progress)) * LEDS)
}

/**
 * Scroll progress as a steering-wheel shift-light strip: 5 green, 5 red, 5 purple LEDs light
 * up as the page scrolls; at the bottom they flash like the rev limiter.
 */
export function RevLights() {
  const strip = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = strip.current
    if (!el) return
    const leds = Array.from(el.children) as HTMLElement[]
    let shown = -1
    const update = () => {
      const max = document.documentElement.scrollHeight - window.innerHeight
      const lit = max > 8 ? litLeds(window.scrollY / max) : 0
      if (lit === shown) return
      shown = lit
      leds.forEach((led, i) => led.classList.toggle('on', i < lit))
      el.classList.toggle('limiter', lit === LEDS)
    }
    gsap.ticker.add(update)
    return () => gsap.ticker.remove(update)
  }, [])

  return (
    <div className="rev-lights" ref={strip} aria-hidden="true">
      {Array.from({ length: LEDS }, (_, i) => (
        <i key={i} className={i < 5 ? 'g' : i < 10 ? 'r' : 'p'} />
      ))}
    </div>
  )
}
