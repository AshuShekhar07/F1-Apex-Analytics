/** Circuit types and geometry helpers (no data, safe to import anywhere). */

export type Pt = [number, number]

export interface SectorInfo {
  points: Pt[]
  lengthM: number
  hex: string
  role: string
  avgSpeedKmh: number
}

export interface Circuit {
  id: string
  name: string
  location: string
  opened: number | null
  firstGp: number | null
  lengthM: number | null
  altitudeM: number | null
  sectors: SectorInfo[]
}

/** One closed outline of the whole lap (sector joins de-duplicated). */
export function lapPoints(c: Circuit): Pt[] {
  const out: Pt[] = []
  for (const s of c.sectors) for (const p of s.points) {
    const last = out[out.length - 1]
    if (!last || last[0] !== p[0] || last[1] !== p[1]) out.push(p)
  }
  return out
}
