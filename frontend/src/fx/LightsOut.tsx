import { useEffect, useRef } from 'react'

import { gsap, reducedMotion } from '../motion/gsap'
import { play } from './sound'

/**
 * Locking in a pick: five red lights come on one by one (a beep each), hold, then go out
 * with an engine rev and a flash. Calls onDone at lights out. Instant under reduced motion.
 */
export function LightsOut({ onDone }: { onDone: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const done = useRef(onDone)
  done.current = onDone

  useEffect(() => {
    const el = ref.current
    if (!el || reducedMotion()) {
      done.current()
      return
    }
    const lights = el.querySelectorAll('.lights span')
    const tl = gsap.timeline({ onComplete: () => done.current() })
    tl.from(el, { opacity: 0, y: 12, duration: 0.25 })
    lights.forEach((light, i) => {
      tl.call(() => {
        light.classList.add('on')
        play('beep')
      }, [], 0.3 + i * 0.32)
    })
    tl.call(() => {
      lights.forEach((l) => l.classList.remove('on'))
      play('go')
    }, [], 0.3 + 5 * 0.32 + 0.45)
    tl.fromTo(el.querySelector('.flash'), { opacity: 0.9, scaleX: 0 }, { opacity: 0, scaleX: 1, duration: 0.5, ease: 'expo.out' }, '>')
    return () => {
      tl.kill()
    }
  }, [])

  return (
    <div className="lights-out" ref={ref} role="status" aria-label="Lights out countdown">
      <div className="lights" aria-hidden="true"><span /><span /><span /><span /><span /></div>
      <span className="label">Locking in…</span>
      <i className="flash" aria-hidden="true" />
    </div>
  )
}
