import { useEffect, type RefObject } from 'react'

import { finePointer, reducedMotion } from './gsap'

/** A slight 3D tilt toward the mouse, for photos on cards. Mouse/trackpad only; off under
    reduced motion. */
export function useTilt(ref: RefObject<HTMLElement | null>, max = 5): void {
  useEffect(() => {
    const el = ref.current
    if (!el || reducedMotion() || !finePointer()) return
    const move = (e: MouseEvent) => {
      const r = el.getBoundingClientRect()
      const x = (e.clientX - r.left) / r.width - 0.5
      const y = (e.clientY - r.top) / r.height - 0.5
      el.style.transform = `perspective(700px) rotateX(${(-y * max).toFixed(2)}deg) rotateY(${(x * max).toFixed(2)}deg)`
    }
    const leave = () => {
      el.style.transform = ''
    }
    el.addEventListener('mousemove', move)
    el.addEventListener('mouseleave', leave)
    return () => {
      el.removeEventListener('mousemove', move)
      el.removeEventListener('mouseleave', leave)
    }
  }, [ref, max])
}
