import { useEffect, useRef, type RefObject } from 'react'

import { finePointer, gsap, reducedMotion, ScrollTrigger, useGSAP } from './gsap'

/**
 * Elements marked data-reveal inside `scope` slide up out of a slight skew as they scroll
 * into view, a few at a time. Elements that appear later (data arriving) are picked up when
 * `deps` change; ones already shown are never animated twice.
 */
export function useReveal(scope: RefObject<HTMLElement | null>, deps: unknown[] = []) {
  useGSAP(
    () => {
      if (!scope.current || reducedMotion()) return
      const els = gsap.utils.toArray<HTMLElement>('[data-reveal]:not([data-revealed])', scope.current)
      if (!els.length) return
      els.forEach((el) => el.setAttribute('data-revealed', ''))
      gsap.set(els, { opacity: 0, y: 46, skewY: 2.5 })
      ScrollTrigger.batch(els, {
        start: 'top 90%',
        once: true,
        onEnter: (batch) =>
          gsap.to(batch, { opacity: 1, y: 0, skewY: 0, duration: 0.9, ease: 'expo.out', stagger: 0.07, overwrite: true }),
      })
    },
    { scope, dependencies: deps },
  )
}

/** A card that tilts toward the pointer in 3D (mouse only, not under reduced motion). */
export function useTilt<T extends HTMLElement>(max = 7) {
  const ref = useRef<T>(null)
  useEffect(() => {
    const el = ref.current
    if (!el || !finePointer() || reducedMotion()) return
    gsap.set(el, { transformPerspective: 900 })
    const rx = gsap.quickTo(el, 'rotationX', { duration: 0.5, ease: 'power3' })
    const ry = gsap.quickTo(el, 'rotationY', { duration: 0.5, ease: 'power3' })
    const move = (e: PointerEvent) => {
      const r = el.getBoundingClientRect()
      rx(-((e.clientY - r.top) / r.height - 0.5) * max)
      ry(((e.clientX - r.left) / r.width - 0.5) * max)
    }
    const leave = () => {
      rx(0)
      ry(0)
    }
    el.addEventListener('pointermove', move)
    el.addEventListener('pointerleave', leave)
    return () => {
      el.removeEventListener('pointermove', move)
      el.removeEventListener('pointerleave', leave)
    }
  }, [max])
  return ref
}
