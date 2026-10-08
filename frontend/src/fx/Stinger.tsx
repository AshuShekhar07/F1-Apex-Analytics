import { useEffect, useRef } from 'react'
import { useLocation } from 'react-router-dom'

import { gsap, reducedMotion } from '../motion/gsap'
import { play } from './sound'

/** Seconds until the wipe fully covers the screen; the old page is removed just after. */
export const STINGER_COVER = 0.34

/**
 * The page change, like a TV replay transition: three slanted panels (accent, carbon, white
 * edge) sweep across, the Apex21 mark flashes, and they sweep off to reveal the new page.
 */
export function Stinger() {
  const { pathname } = useLocation()
  const root = useRef<HTMLDivElement>(null)
  const shown = useRef(pathname)

  useEffect(() => {
    if (root.current) gsap.set(root.current.querySelectorAll('.st-panel'), { xPercent: -135 })
  }, [])

  useEffect(() => {
    if (shown.current === pathname || !root.current) return
    shown.current = pathname
    if (reducedMotion()) return
    play('whoosh')
    const el = root.current
    const panels = el.querySelectorAll('.st-panel')
    const mark = el.querySelector('.st-mark')
    const tl = gsap.timeline({ defaults: { overwrite: 'auto' } })
    tl.set(el, { autoAlpha: 1 })
      .fromTo(panels, { xPercent: -135 }, { xPercent: 0, duration: 0.28, ease: 'power3.in', stagger: 0.03 })
      .fromTo(mark, { autoAlpha: 0, scale: 0.85, skewX: -12 }, { autoAlpha: 1, scale: 1, skewX: 0, duration: 0.2 }, 0.2)
      .to(mark, { autoAlpha: 0, scale: 1.08, duration: 0.14 }, 0.5)
      .to(panels, { xPercent: 135, duration: 0.42, ease: 'power3.inOut', stagger: { each: 0.03, from: 'end' } }, 0.46)
      .set(el, { autoAlpha: 0 })
    return () => {
      tl.kill()
      gsap.set(el, { autoAlpha: 0 })
    }
  }, [pathname])

  return (
    <div className="stinger" ref={root} aria-hidden="true">
      <div className="st-panel edge" />
      <div className="st-panel accent" />
      <div className="st-panel carbon" />
      <div className="st-mark">
        <img src="/apex21-logo.png" alt="" />
        <span />
      </div>
    </div>
  )
}
