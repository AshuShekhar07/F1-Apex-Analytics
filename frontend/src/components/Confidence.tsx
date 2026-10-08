import { CONFIDENCE_LABEL } from '../format'
import type { ConfidenceLevel } from '../types'

const ICON: Record<ConfidenceLevel, string> = { high: '▲', medium: '■', low: '▼' }

/** Measured confidence: an icon and a word, never colour alone. */
export function ConfidenceBadge({ level }: { level: ConfidenceLevel }) {
  return (
    <span className={`confidence ${level}`}>
      <span aria-hidden="true">{ICON[level]}</span> {CONFIDENCE_LABEL[level]} confidence
    </span>
  )
}

/** The measured reasons behind a confidence level, one per line. */
export function ConfidenceBasis({ lines }: { lines: (string | undefined)[] }) {
  const shown = lines.filter(Boolean) as string[]
  if (!shown.length) return null
  return (
    <ul className="basis">
      {shown.map((line) => (
        <li key={line}>{line.charAt(0).toUpperCase() + line.slice(1)}</li>
      ))}
    </ul>
  )
}
