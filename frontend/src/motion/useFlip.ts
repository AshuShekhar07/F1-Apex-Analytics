import { useLayoutEffect, useRef, type RefObject } from 'react'

import { gsap, reducedMotion } from './gsap'

/**
 * FLIP (first, last, invert, play): when the order changes, every child marked data-flip="id"
 * glides from where it was to where it is now, like cars changing places on a timing tower.
 */
export function useFlip(root: RefObject<HTMLElement | null>, deps: unknown[]): void {
  const tops = useRef(new Map<string, number>())
  useLayoutEffect(() => {
    const el = root.current
    if (!el) return
    const motion = !reducedMotion()
    const now = new Map<string, number>()
    el.querySelectorAll<HTMLElement>('[data-flip]').forEach((node) => {
      const id = node.dataset.flip!
      const top = node.offsetTop
      now.set(id, top)
      const before = tops.current.get(id)
      if (motion && before !== undefined && before !== top) {
        gsap.fromTo(node, { y: before - top }, { y: 0, duration: 0.55, ease: 'power3.inOut', overwrite: true, clearProps: 'transform' })
      }
    })
    tops.current = now
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
}
