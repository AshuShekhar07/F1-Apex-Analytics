import { useEffect, useRef } from 'react'

import { gsap, reducedMotion } from '../motion/gsap'

/**
 * A stat that counts up from zero when it appears. Text that is not a plain number
 * (e.g. "P1", "–") is shown as is. The count edits React's own text node, so a new
 * value from React is never overwritten by a stale count.
 */
export function CountNum({ value, delay = 0 }: { value: number | string | null | undefined; delay?: number }) {
  const ref = useRef<HTMLSpanElement>(null)
  const text = value == null ? '–' : String(value)
  useEffect(() => {
    const node = ref.current?.firstChild as Text | null
    const target = Number(text)
    if (!node || !Number.isFinite(target) || text.trim() === '' || reducedMotion()) return
    const decimals = (text.split('.')[1] ?? '').length
    const counter = { v: 0 }
    let written = (0).toFixed(decimals)
    node.nodeValue = written
    const tween = gsap.to(counter, {
      v: target,
      duration: 1.2,
      delay,
      ease: 'expo.out',
      onUpdate: () => {
        if (node.nodeValue !== written) return tween.kill()   // React changed it meanwhile
        written = counter.v.toFixed(decimals)
        node.nodeValue = written
      },
      onComplete: () => {
        if (node.nodeValue === written) node.nodeValue = text
      },
    })
    return () => {
      tween.kill()
      if (node.nodeValue === written) node.nodeValue = text
    }
  }, [text, delay])
  return <span ref={ref}>{text}</span>
}
