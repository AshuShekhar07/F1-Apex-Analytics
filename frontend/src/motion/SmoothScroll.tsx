/**
 * Lenis smooth scrolling, driven by the GSAP ticker so ScrollTrigger animations stay in step.
 * Off under reduced motion (the browser's own scrolling is used). Touch devices keep native
 * scrolling (Lenis only smooths the wheel by default).
 *
 * Also tracks a smoothed scroll velocity (px per frame) that the speed lines and the ticker use.
 */
import Lenis from 'lenis'
import 'lenis/dist/lenis.css'
import { useEffect, type ReactNode } from 'react'

import { gsap, reducedMotion, ScrollTrigger } from './gsap'

let lenis: Lenis | null = null
let velocity = 0

/** Smoothed scroll speed in px per frame; positive when scrolling down. */
export function scrollVelocity(): number {
  return velocity
}

/** Jump to the top at once (a new page opens at its top). */
export function scrollToTop(): void {
  if (lenis) lenis.scrollTo(0, { immediate: true, force: true })
  else window.scrollTo(0, 0)
}

/** Freeze page scrolling while an overlay (the driver drawer) is open. */
export function lockScroll(locked: boolean): void {
  if (lenis) {
    if (locked) lenis.stop()
    else lenis.start()
  } else {
    document.documentElement.style.overflow = locked ? 'hidden' : ''
  }
}

export function SmoothScroll({ children }: { children: ReactNode }) {
  useEffect(() => {
    let last = window.scrollY
    const measure = () => {
      const y = window.scrollY
      velocity += (y - last - velocity) * 0.18
      last = y
    }
    gsap.ticker.add(measure)

    let raf: ((time: number) => void) | null = null
    if (!reducedMotion()) {
      lenis = new Lenis({ duration: 1.15, easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)) })
      lenis.on('scroll', ScrollTrigger.update)
      raf = (time) => lenis?.raf(time * 1000)
      gsap.ticker.add(raf)
      gsap.ticker.lagSmoothing(0)
    }

    // pages grow when their data arrives: re-measure every scroll-linked animation
    let timer = 0
    const observer = new ResizeObserver(() => {
      window.clearTimeout(timer)
      timer = window.setTimeout(() => ScrollTrigger.refresh(), 180)
    })
    observer.observe(document.body)

    return () => {
      gsap.ticker.remove(measure)
      if (raf) gsap.ticker.remove(raf)
      lenis?.destroy()
      lenis = null
      observer.disconnect()
      window.clearTimeout(timer)
    }
  }, [])
  return <>{children}</>
}
