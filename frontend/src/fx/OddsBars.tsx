import { motion } from 'motion/react'

import { glowColour, normaliseHex } from '../theme/color'

export interface OddsEntry {
  id: number
  name: string
  colour: string | null | undefined
  value: number            // 0..1
}

/** Animated probability bars in team colours; the user's pick is flagged. */
export function OddsBars({ entries, pickId, limit = 10 }: { entries: OddsEntry[]; pickId: number | null; limit?: number }) {
  const sorted = [...entries].sort((a, b) => b.value - a.value)
  const top = sorted.slice(0, limit)
  const pickRank = sorted.findIndex((e) => e.id === pickId)
  const rows = pickRank >= limit ? [...top, sorted[pickRank]] : top
  const max = Math.max(...rows.map((r) => r.value), 0.0001)
  return (
    <div>
      {rows.map((e, i) => {
        const rank = sorted.indexOf(e) + 1
        const colour = glowColour(normaliseHex(e.colour, '#888888'))
        return (
          <motion.div
            key={e.id}
            className={`odds-row ${e.id === pickId ? 'yours' : ''}`}
            initial={{ opacity: 0, x: -24 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.15 + i * 0.06 }}
          >
            <span className="pos">{rank}</span>
            <span className="name">
              <span>{e.name}</span>
              {e.id === pickId && <b>Your pick</b>}
            </span>
            <span className="track">
              <motion.span
                className="fill"
                style={{ display: 'block', background: colour, boxShadow: `0 0 14px ${colour}` }}
                initial={{ width: 0 }}
                animate={{ width: `${(e.value / max) * 100}%` }}
                transition={{ delay: 0.3 + i * 0.06, duration: 0.9, ease: [0.2, 0.8, 0.2, 1] }}
              />
            </span>
            <span className="val">{e.value < 0.001 ? '<0.1%' : `${(e.value * 100).toFixed(e.value < 0.1 ? 1 : 0)}%`}</span>
          </motion.div>
        )
      })}
    </div>
  )
}

export function ordinal(n: number): string {
  const s = ['th', 'st', 'nd', 'rd']
  const v = n % 100
  return `${n}${s[(v - 20) % 10] || s[v] || s[0]}`
}
