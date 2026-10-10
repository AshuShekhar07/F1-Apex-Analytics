import { motion, useReducedMotion } from 'motion/react'
import { useMemo } from 'react'

import { lapPoints, type Circuit, type Pt } from '../circuitTypes'

/** Map circuit metres into a 1000x1000 box, north up. */
function projector(points: Pt[]) {
  const xs = points.map((p) => p[0])
  const ys = points.map((p) => -p[1])
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys)
  const scale = 900 / Math.max(maxX - minX, maxY - minY)
  const ox = (1000 - (maxX - minX) * scale) / 2, oy = (1000 - (maxY - minY) * scale) / 2
  return ([x, y]: Pt) => [ox + (x - minX) * scale, oy + (-y - minY) * scale] as const
}

const path = (pts: (readonly [number, number])[]) =>
  pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ')

/**
 * The circuit as a clean racing line: white, or split into its three sectors in their timing
 * colours. A short bar marks the start/finish line and a dot laps the circuit.
 */
export function CircuitOutline({ circuit, sectors = false, colour = '#f3f3f5', width = 10 }: {
  circuit: Circuit
  sectors?: boolean
  colour?: string
  width?: number
}) {
  const reduced = useReducedMotion()
  const { lap, parts, start } = useMemo(() => {
    const all = lapPoints(circuit)
    const at = projector(all)
    const lapPts = all.map(at)
    const sectorPaths = circuit.sectors.map((s) => ({ d: path(s.points.map(at)), hex: s.hex }))
    const [x0, y0] = lapPts[0]
    const [x1, y1] = lapPts[Math.min(3, lapPts.length - 1)]
    const len = Math.hypot(x1 - x0, y1 - y0) || 1
    const nx = -(y1 - y0) / len, ny = (x1 - x0) / len
    return {
      lap: `${path(lapPts)} Z`,
      parts: sectorPaths,
      start: { x1: x0 - nx * 26, y1: y0 - ny * 26, x2: x0 + nx * 26, y2: y0 + ny * 26 },
    }
  }, [circuit])

  const draw = reduced ? {} : { initial: { pathLength: 0 }, animate: { pathLength: 1 }, transition: { duration: 1.6, ease: [0.4, 0, 0.2, 1] as const } }

  return (
    <svg viewBox="0 0 1000 1000" role="img" aria-label={`${circuit.name} layout`}>
      <path d={lap} fill="none" stroke="rgba(255,255,255,0.07)" strokeWidth={width * 3} strokeLinejoin="round" />
      {sectors ? (
        parts.map((p, i) => (
          <motion.path key={i} d={p.d} fill="none" stroke={p.hex} strokeWidth={width} strokeLinejoin="round" strokeLinecap="round" {...draw} />
        ))
      ) : (
        <motion.path d={lap} fill="none" stroke={colour} strokeWidth={width} strokeLinejoin="round" strokeLinecap="round" {...draw} />
      )}
      <line {...start} stroke="#fff" strokeWidth={6} />
      {!reduced && (
        <circle r={width * 1.1} fill="#fff" stroke="#08080a" strokeWidth={3}>
          <animateMotion dur="9s" repeatCount="indefinite" path={lap} />
        </circle>
      )}
    </svg>
  )
}
