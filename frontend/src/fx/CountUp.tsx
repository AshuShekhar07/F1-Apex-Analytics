import { animate, useInView, useReducedMotion } from 'motion/react'
import { useEffect, useRef, useState } from 'react'

/** A number that counts up when it scrolls into view. */
export function CountUp({ to, decimals = 0, duration = 1.4 }: { to: number; decimals?: number; duration?: number }) {
  const ref = useRef<HTMLSpanElement>(null)
  const inView = useInView(ref, { once: true, margin: '-40px' })
  const reduced = useReducedMotion()
  const [value, setValue] = useState(reduced ? to : 0)
  useEffect(() => {
    if (!inView || reduced) return
    const controls = animate(0, to, { duration, ease: [0.2, 0.8, 0.2, 1], onUpdate: setValue })
    return () => controls.stop()
  }, [inView, reduced, to, duration])
  return <span ref={ref}>{value.toFixed(decimals)}</span>
}
