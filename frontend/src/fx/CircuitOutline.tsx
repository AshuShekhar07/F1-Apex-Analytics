import { motion, useReducedMotion } from 'motion/react'
import { useMemo } from 'react'

import { lapPoints, type Circuit } from '../circuitTypes'

/** The circuit drawn as a glowing racing line, with a car lapping it. */
export function CircuitOutline({ circuit }: { circuit: Circuit }) {
  const reduced = useReducedMotion()
  const d = useMemo(() => {
    const pts = lapPoints(circuit)
    const xs = pts.map((p) => p[0])
    const ys = pts.map((p) => -p[1])            // north up
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys)
    const scale = 900 / Math.max(maxX - minX, maxY - minY)
    const ox = (1000 - (maxX - minX) * scale) / 2, oy = (1000 - (maxY - minY) * scale) / 2
    return pts
      .map(([x, y], i) => `${i ? 'L' : 'M'}${(ox + (x - minX) * scale).toFixed(1)},${(oy + (-y - minY) * scale).toFixed(1)}`)
      .join(' ') + ' Z'
  }, [circuit])

  return (
    <svg viewBox="0 0 1000 1000" role="img" aria-label={`${circuit.name} layout`}>
      <defs>
        <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="8" result="b" />
          <feMerge>
            <feMergeNode in="b" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      <path d={d} fill="none" stroke="rgba(255,255,255,0.07)" strokeWidth="34" strokeLinejoin="round" />
      <motion.path
        d={d}
        fill="none"
        stroke="var(--accent)"
        strokeWidth="9"
        strokeLinejoin="round"
        strokeLinecap="round"
        filter="url(#glow)"
        initial={reduced ? false : { pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={{ duration: 2.2, ease: [0.4, 0, 0.2, 1] }}
      />
      {!reduced && (
        <circle r="13" fill="#fff" filter="url(#glow)" opacity="0">
          <set attributeName="opacity" to="1" begin="2.2s" fill="freeze" />
          <animateMotion dur="6s" repeatCount="indefinite" path={d} begin="2.2s" />
        </circle>
      )}
    </svg>
  )
}
