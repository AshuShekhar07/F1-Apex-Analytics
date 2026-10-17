/** Quick search: what can be found and how a query ranks it. Pure. */

export type SearchKind = 'page' | 'race' | 'driver' | 'team' | 'car' | 'track' | 'season'

export interface SearchItem {
  kind: SearchKind
  title: string
  sub?: string
  /** extra words that find it (nationality, country, chassis name, ...) */
  words?: string
  to: string
  colour?: string | null
  /** a small boost among equal matches (e.g. recent seasons first), 0-1 */
  rank?: number
}

const norm = (s: string) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
export const words = (s: string | null | undefined) => norm(s ?? '').split(/[^a-z0-9]+/).filter(Boolean)

/** How well an item matches: every query word must start a word of the item (or, from three
    letters, sit inside one); title words count more. 0 = no match. */
export function score(item: SearchItem, query: string): number {
  const q = words(query)
  if (!q.length) return 0
  const title = words(item.title)
  const all = [...title, ...words(`${item.sub ?? ''} ${item.words ?? ''}`)]
  let total = 0
  for (const w of q) {
    let best = 0
    all.forEach((t, i) => {
      const s = t === w ? 3 : t.startsWith(w) ? 2 : w.length >= 3 && t.includes(w) ? 1 : 0
      if (s) best = Math.max(best, s + (i < title.length ? 1 : 0))
    })
    if (!best) return 0
    total += best
  }
  if (norm(item.title).startsWith(norm(query.trim()))) total += 2
  return total + (item.rank ?? 0)
}

/** The best matches, best first (equal scores keep the index order). */
export function search(items: SearchItem[], query: string, limit = 30): SearchItem[] {
  return items
    .map((item, i) => ({ item, i, s: score(item, query) }))
    .filter((x) => x.s > 0)
    .sort((a, b) => b.s - a.s || a.i - b.i)
    .slice(0, limit)
    .map((x) => x.item)
}
