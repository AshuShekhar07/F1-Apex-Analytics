import { useEffect, useRef, type RefObject } from 'react'

import { swipeDirection } from './swipe'

/** Touch swipes left / right on an element (phones). A touch that starts inside something that
    scrolls sideways itself (a wide table, a strip), a form control or a [data-no-swipe] area is
    left alone. `ready` re-attaches once the element is on the page (after its data loaded). */
export function useSwipe(ref: RefObject<HTMLElement | null>, onSwipe: (dir: 'left' | 'right') => void, ready = true): void {
  const handler = useRef(onSwipe)
  handler.current = onSwipe
  useEffect(() => {
    const el = ref.current
    if (!el) return
    let start: { x: number; y: number; t: number } | null = null
    const ownGesture = (target: EventTarget | null) => {
      for (let n = target instanceof Element ? target : null; n && n !== el; n = n.parentElement) {
        if (n.matches('input, select, textarea, [data-no-swipe]')) return true
        const overflow = getComputedStyle(n).overflowX
        if ((overflow === 'auto' || overflow === 'scroll') && n.scrollWidth > n.clientWidth + 2) return true
      }
      return false
    }
    const down = (e: TouchEvent) => {
      const t = e.touches[0]
      start = e.touches.length === 1 && !ownGesture(e.target) ? { x: t.clientX, y: t.clientY, t: Date.now() } : null
    }
    const up = (e: TouchEvent) => {
      if (!start) return
      const t = e.changedTouches[0]
      const dir = swipeDirection(t.clientX - start.x, t.clientY - start.y, Date.now() - start.t)
      start = null
      if (dir) handler.current(dir)
    }
    const cancel = () => {
      start = null
    }
    el.addEventListener('touchstart', down, { passive: true })
    el.addEventListener('touchend', up, { passive: true })
    el.addEventListener('touchcancel', cancel)
    return () => {
      el.removeEventListener('touchstart', down)
      el.removeEventListener('touchend', up)
      el.removeEventListener('touchcancel', cancel)
    }
  }, [ref, ready])
}
