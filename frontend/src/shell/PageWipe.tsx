import { useEffect, useRef } from 'react'
import { useLocation } from 'react-router-dom'

import { gsap, reducedMotion } from '../motion/gsap'

/**
 * Between pages, a band in the accent colour sweeps across the screen with a kerb on its
 * trailing edge, like a car flashing past the camera. It never blocks clicks; off under reduced
 * motion.
 */
export function PageWipe() {
  const band = useRef<HTMLDivElement>(null)
  const { pathname } = useLocation()
  const first = useRef(true)
  useEffect(() => {
    if (first.current) {
      first.current = false
      return
    }
    const el = band.current
    if (!el || reducedMotion()) return
    const tl = gsap.timeline()
    tl.set(el, { xPercent: -130, opacity: 1 })
      .to(el, { xPercent: 330, duration: 0.62, ease: 'power2.inOut' })
      .set(el, { opacity: 0 })
    return () => {
      tl.kill()
    }
  }, [pathname])
  return <div ref={band} className="page-wipe" aria-hidden="true"><i /></div>
}
