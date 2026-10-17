import { useEffect, useRef, useState } from 'react'

import { gsap, reducedMotion } from '../motion/gsap'
import { sessionFlag, setSessionFlag } from '../storage'

/**
 * The opening, once per browser session: the Apex21 mark wipes in, a red line runs under
 * it, and the screen lifts away (about 1.6 s). A click or any key skips it.
 */
export function Intro({ onDone }: { onDone: () => void }) {
  const root = useRef<HTMLDivElement>(null)
  const done = useRef(onDone)
  done.current = onDone

  useEffect(() => {
    const el = root.current
    if (!el) return
    const finish = () => done.current()
    if (reducedMotion()) {
      const t = gsap.to(el, { opacity: 0, duration: 0.3, delay: 0.5, onComplete: finish })
      return () => {
        t.kill()
      }
    }
    const tl = gsap.timeline({ onComplete: finish })
    tl.fromTo(el.querySelector('.mark img'), { clipPath: 'inset(0 100% 0 0)', x: -16 },
      { clipPath: 'inset(0 0% 0 0)', x: 0, duration: 0.6, ease: 'power3.out' })
      .fromTo(el.querySelector('.line'), { scaleX: 0 }, { scaleX: 1, duration: 0.5, ease: 'power2.inOut' }, 0.3)
      .fromTo(el.querySelector('.tag'), { opacity: 0, y: 6 }, { opacity: 1, y: 0, duration: 0.35 }, 0.55)
      .to(el, { yPercent: -100, duration: 0.55, ease: 'power3.inOut' }, '+=0.35')
    const skip = () => tl.progress(1)
    window.addEventListener('keydown', skip)
    el.addEventListener('click', skip)
    return () => {
      tl.kill()
      window.removeEventListener('keydown', skip)
      el.removeEventListener('click', skip)
    }
  }, [])

  return (
    <div className="intro" ref={root} role="presentation">
      <div className="mark">
        <img src="/apex21-logo.png" alt="Apex21" />
        <div className="line" />
        <div className="tag">Formula 1 race prediction</div>
      </div>
    </div>
  )
}

/** True on the first visit of this browser session (then remembered). */
export function useIntroOnce(): [boolean, () => void] {
  const [show, setShow] = useState(() => !sessionFlag('intro-seen'))
  const done = () => {
    setSessionFlag('intro-seen')
    setShow(false)
  }
  return [show, done]
}
