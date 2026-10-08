/** Matches a circuit from the 3-D track files to a track in the database by name. Pure. */

const STOP = new Set([
  'circuit', 'international', 'internazionale', 'internacional', 'autodromo', 'autódromo', 'nazionale',
  'grand', 'prix', 'street', 'de', 'del', 'do', 'di', 'the', 'of', 'e', 'park', 'racing', 'course',
  'ring', 'city', 'gp', 'autodrome',
])

export function tokens(text: string | null | undefined): Set<string> {
  const plain = (text ?? '')
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
  return new Set(plain.split(' ').filter((t) => t.length > 1 && !STOP.has(t)))
}

export interface DbTrack {
  id: number
  name: string
  country?: string | null
  last_season?: number | null
}

/** The database track sharing the most name/location words with the circuit; null if none. */
export function matchTrack(name: string, location: string, tracks: DbTrack[]): DbTrack | null {
  const wanted = new Set([...tokens(name), ...tokens(location)])
  let best: DbTrack | null = null
  let bestScore = 0
  for (const t of tracks) {
    const have = tokens(`${t.name} ${t.country ?? ''}`)
    const shared = [...have].filter((w) => wanted.has(w)).length
    const score = shared + (t.last_season ?? 0) / 1e5          // ties: the circuit raced most recently
    if (shared > 0 && score > bestScore) {
      best = t
      bestScore = score
    }
  }
  return best
}
