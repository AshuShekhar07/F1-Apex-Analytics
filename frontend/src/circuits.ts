/** The circuit shapes (centre lines split into 3 sectors) from circuit-viz-app's pipeline.
    Holds every circuit file: import it only from lazily loaded code. */

import type { Circuit, Pt, SectorInfo } from './circuitTypes'

export type { Circuit, Pt, SectorInfo } from './circuitTypes'
export { lapPoints } from './circuitTypes'

interface RawCircuit {
  properties: { id: string; Name: string; Location: string; opened?: number; firstgp?: number; length?: number; altitude?: number }
  sector1: Pt[]
  sector2: Pt[]
  sector3: Pt[]
  sector1_length_m: number
  sector2_length_m: number
  sector3_length_m: number
  colors: Record<string, { hex: string; role: string; avg_speed_kmh: number }>
}

const files = import.meta.glob<RawCircuit>('./data/tracks/*.sectors.json', { eager: true, import: 'default' })

function toCircuit(raw: RawCircuit): Circuit {
  const p = raw.properties
  const sector = (n: 1 | 2 | 3): SectorInfo => ({
    points: raw[`sector${n}`],
    lengthM: raw[`sector${n}_length_m`],
    hex: raw.colors[`sector${n}`].hex,
    role: raw.colors[`sector${n}`].role,
    avgSpeedKmh: raw.colors[`sector${n}`].avg_speed_kmh,
  })
  return {
    id: p.id, name: p.Name, location: p.Location, opened: p.opened ?? null, firstGp: p.firstgp ?? null,
    lengthM: p.length ?? null, altitudeM: p.altitude ?? null, sectors: [sector(1), sector(2), sector(3)],
  }
}

export const CIRCUITS: Circuit[] = Object.values(files).map(toCircuit).sort((a, b) => a.name.localeCompare(b.name))

