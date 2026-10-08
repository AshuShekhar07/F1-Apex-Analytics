import { useRef } from 'react'

import { gsap, reducedMotion, ScrollTrigger, useGSAP } from './gsap'
import { afterIntro, PAGE_ENTER_DELAY } from './stage'

interface CharsProps {
  text: string
  /** Seconds after the page appears (to chain several pieces of one headline). */
  at?: number
  /** 'mount': play when the page opens; 'scroll': play when scrolled into view. */
  play?: 'mount' | 'scroll'
  className?: string
}

/**
 * Text split into letters that rise out of a mask one after another. The split is rendered
 * by React itself (not by mutating the DOM), so the text can change safely; screen readers
 * get the plain text.
 */
export function Chars({ text, at = 0, play = 'mount', className }: CharsProps) {
  const ref = useRef<HTMLSpanElement>(null)
  useGSAP(
    () => {
      if (reducedMotion() || !ref.current) return
      const chars = ref.current.querySelectorAll('.ch')
      gsap.set(chars, { yPercent: 118, rotate: 10, opacity: 0 })
      const run = (delay: number) =>
        gsap.to(chars, { yPercent: 0, rotate: 0, opacity: 1, duration: 0.9, ease: 'expo.out', stagger: 0.022, delay })
      if (play === 'scroll') {
        ScrollTrigger.create({ trigger: ref.current, start: 'top 88%', once: true, onEnter: () => run(at) })
        return
      }
      return afterIntro(() => run(PAGE_ENTER_DELAY + at))
    },
    { scope: ref, dependencies: [text] },
  )
  const words = text.split(' ')
  return (
    <span ref={ref} className={`chars ${className ?? ''}`}>
      <span className="sr-only">{text}</span>
      <span aria-hidden="true">
        {words.map((word, wi) => (
          <span key={`${wi}-${word}`}>
            <span className="w">
              {Array.from(word).map((c, ci) => (
                <span className="ch" key={ci}>{c}</span>
              ))}
            </span>
            {wi < words.length - 1 ? ' ' : ''}
          </span>
        ))}
      </span>
    </span>
  )
}
