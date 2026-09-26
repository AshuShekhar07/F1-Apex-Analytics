// Pure helpers (no React) so they can be unit-tested with node --test.

export const COMPOUND_COLORS: Record<string, string> = {
  SOFT: '#e10600',
  MEDIUM: '#f5c400',
  HARD: '#e8e8e8',
  INTERMEDIATE: '#2fa84f',
  WET: '#1e6fd9',
}

export function compoundColor(compound: string | null | undefined): string {
  return COMPOUND_COLORS[(compound ?? '').toUpperCase()] ?? '#666'
}

export function compoundLetter(compound: string | null | undefined): string {
  const c = (compound ?? '').toUpperCase()
  return c === 'INTERMEDIATE' ? 'I' : c ? c[0] : '?'
}

export function pct(value: number, digits = 0): string {
  if (!Number.isFinite(value)) return '–'
  const p = value * 100
  if (p > 0 && p < 0.5 && digits === 0) return '<1%'
  return `${p.toFixed(digits)}%`
}

export function teamColor(hex: string | null | undefined): string {
  if (!hex) return '#888'
  return hex.startsWith('#') ? hex : `#${hex}`
}

/** "10-12, 30" -> [10, 11, 12, 30]; throws on anything outside 1..maxLap. */
export function parseLaps(input: string, maxLap: number): number[] {
  const laps = new Set<number>()
  for (const raw of input.split(',')) {
    const part = raw.trim()
    if (!part) continue
    const range = part.match(/^(\d+)\s*-\s*(\d+)$/)
    const [a, b] = range ? [Number(range[1]), Number(range[2])] : [Number(part), Number(part)]
    if (!Number.isInteger(a) || !Number.isInteger(b) || a < 1 || b > maxLap || a > b) {
      throw new Error(`"${part}" is not a lap or lap range between 1 and ${maxLap}`)
    }
    for (let lap = a; lap <= b; lap++) laps.add(lap)
  }
  return [...laps].sort((x, y) => x - y)
}

/** Stints of a plan: sequence + stop laps -> [{compound, start, end}] */
export function planStints(sequence: string[], stopLaps: number[], totalLaps: number) {
  const bounds = [0, ...stopLaps, totalLaps]
  return sequence.map((compound, i) => ({ compound, start_lap: bounds[i] + 1, end_lap: bounds[i + 1] }))
}

export function positionChange(delta: number): { label: string; tone: 'up' | 'down' | 'flat' } {
  if (Math.abs(delta) < 0.05) return { label: '±0.0', tone: 'flat' }
  // a lower position number is better
  return delta < 0 ? { label: `▲ ${Math.abs(delta).toFixed(1)}`, tone: 'up' } : { label: `▼ ${delta.toFixed(1)}`, tone: 'down' }
}

/** "era_band(n=173), start=MEDIUM" -> "173 earlier cars from similar grid slots that started on MEDIUM, this regulation era" */
export function evidenceLabel(source: string): string {
  const m = source.match(/^(\w+)\(n=(\d+)\)(?:, start=(\w+))?/)
  if (!m) return source
  const [, level, n, start] = m
  const scope: Record<string, string> = {
    era: 'in this regulation era',
    era_band: 'from similar grid slots in this regulation era',
    track: 'at this track in this regulation era',
    track_band: 'from similar grid slots at this track in this regulation era',
  }
  return `${n} earlier cars ${scope[level] ?? level}${start ? ` that started on ${start.toLowerCase()}` : ''}`
}
