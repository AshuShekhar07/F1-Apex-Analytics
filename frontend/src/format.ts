// Pure helpers (no React) so they can be unit-tested with node --test.

export const COMPOUND_COLORS: Record<string, string> = {
  SOFT: '#da291c',
  MEDIUM: '#ffd12e',
  HARD: '#f0f0ec',
  INTERMEDIATE: '#43b02a',
  WET: '#0067ad',
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

export const CONFIDENCE_LABEL: Record<string, string> = { high: 'High', medium: 'Medium', low: 'Low' }

/** "pre_qualifying" -> "Before qualifying" */
export function stageLabel(stage: string): string {
  return stage === 'post_qualifying' ? 'After qualifying' : stage === 'pre_qualifying' ? 'Before qualifying' : stage
}

export interface ChartSeries {
  id: number
  name: string
  points: { x: number; y: number }[]
}

/**
 * The `top` entries with the highest chance in the latest round, as chart series
 * (x = after round, y = title chance 0..1), ordered by that latest chance.
 */
export function topTitleSeries(
  series: { id: number; name: string; rounds: { after_round: number; p_title: number }[] }[],
  top = 4,
): ChartSeries[] {
  const latest = (s: (typeof series)[number]) =>
    s.rounds.reduce((best, r) => (r.after_round >= best.after_round ? r : best), { after_round: -1, p_title: 0 }).p_title
  return [...series]
    .sort((a, b) => latest(b) - latest(a))
    .slice(0, top)
    .map((s) => ({
      id: s.id,
      name: s.name,
      points: [...s.rounds].sort((a, b) => a.after_round - b.after_round).map((r) => ({ x: r.after_round, y: r.p_title })),
    }))
}

/** "Before the season" for round 0, else "After round N". */
export function roundLabel(afterRound: number): string {
  return afterRound === 0 ? 'Before the season' : `After round ${afterRound}`
}

/** Chance for a label: never rounds up to a certain 100% or down to 0% that isn't exact. */
export function pctLabel(value: number): string {
  if (value > 0 && value < 0.005) return '<1%'
  if (value < 1 && value >= 0.995) return '>99%'
  return pct(value)
}

/** Short end-of-line name: a driver's surname, a team without its suffix ("Red Bull Racing" -> "Red Bull"). */
export function shortName(name: string, kind: 'drivers' | 'constructors'): string {
  if (kind === 'drivers') return name.split(' ').slice(-1)[0]
  const short = name.replace(/\s+(Racing|F1 Team|Team)$/i, '').trim()
  return short || name
}

/** "12 min ago", "3 h ago", "2 d ago". */
export function ago(minutes: number): string {
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${Math.round(minutes)} min ago`
  if (minutes < 48 * 60) return `${Math.round(minutes / 60)} h ago`
  return `${Math.round(minutes / 1440)} d ago`
}

// Day of each session relative to race day, by weekend format (sprint weekends since 2024
// run sprint qualifying on Friday, sprint and qualifying on Saturday; 2023 ran qualifying on
// Friday; 2021-22 ran qualifying Friday and the sprint Saturday).
const SESSION_DAYS: Record<string, Record<string, number>> = {
  conventional: { FP1: -2, FP2: -2, FP3: -1, Q: -1, R: 0 },
  sprint_legacy: { FP1: -2, Q: -2, FP2: -1, S: -1, R: 0 },
  sprint_shootout: { FP1: -2, SQ: -2, S: -1, Q: -1, R: 0 },
  sprint_shootout_2023: { FP1: -2, Q: -2, SQ: -1, S: -1, R: 0 },
}

/** The calendar day (YYYY-MM-DD) a session runs on, from the race date and format. */
export function sessionDay(raceDate: string, format: string, season: number, type: string): string | null {
  const table = format === 'sprint_shootout' && season === 2023 ? SESSION_DAYS.sprint_shootout_2023
    : SESSION_DAYS[format] ?? SESSION_DAYS.conventional
  const offset = table[type]
  if (offset === undefined) return null
  const d = new Date(`${raceDate}T12:00:00Z`)
  d.setUTCDate(d.getUTCDate() + offset)
  return d.toISOString().slice(0, 10)
}

export type SessionState = 'done' | 'waiting' | 'today' | 'upcoming'

/** Done when its data is in; "waiting" when its day has passed (or a later session is done)
    but the data is not in yet; "today" on its day; else upcoming. today is YYYY-MM-DD. */
export function sessionStates(
  sessions: { type: string; has_laps: boolean; has_results: boolean }[],
  raceDate: string, format: string, season: number, today: string,
): SessionState[] {
  const done = sessions.map((s) => s.has_laps || s.has_results)
  return sessions.map((s, i) => {
    if (done[i]) return 'done'
    const day = sessionDay(raceDate, format, season, s.type)
    if (done.slice(i + 1).some(Boolean) || (day !== null && day < today)) return 'waiting'
    if (day === today) return 'today'
    return 'upcoming'
  })
}

/** A local date as YYYY-MM-DD. */
export function localDay(d = new Date()): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** +3 / -2 / 0 */
export function signed(n: number): string {
  return n > 0 ? `+${n}` : `${n}`
}
