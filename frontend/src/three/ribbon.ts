/**
 * A flat racing-surface ribbon along a 2-D centre line: a road of `width` with a little
 * `thickness`, instead of a round tube. Pure (no three.js) so it can be tested; the
 * component turns the arrays into a BufferGeometry.
 *
 * points are (x, z) on the ground plane; y is up. For each point the left/right edges sit
 * half the width away along the normal of the local direction. Four faces per segment:
 * top, bottom and the two side walls.
 */
export interface RibbonArrays {
  positions: Float32Array
  indices: Uint32Array
}

export function ribbonArrays(points: [number, number][], width: number, thickness: number): RibbonArrays {
  const n = points.length
  if (n < 2) return { positions: new Float32Array(), indices: new Uint32Array() }
  const half = width / 2
  // 4 vertices per point: top-left, top-right, bottom-left, bottom-right
  const positions = new Float32Array(n * 4 * 3)
  for (let i = 0; i < n; i++) {
    const [px, pz] = points[i]
    const [ax, az] = points[Math.max(0, i - 1)]
    const [bx, bz] = points[Math.min(n - 1, i + 1)]
    let tx = bx - ax
    let tz = bz - az
    const len = Math.hypot(tx, tz) || 1
    tx /= len
    tz /= len
    const nx = -tz
    const nz = tx
    const corners: [number, number, number][] = [
      [px + nx * half, thickness, pz + nz * half],
      [px - nx * half, thickness, pz - nz * half],
      [px + nx * half, 0, pz + nz * half],
      [px - nx * half, 0, pz - nz * half],
    ]
    corners.forEach((c, k) => positions.set(c, (i * 4 + k) * 3))
  }
  const indices: number[] = []
  const quad = (a: number, b: number, c: number, d: number) => indices.push(a, b, c, b, d, c)
  for (let i = 0; i < n - 1; i++) {
    const v = i * 4
    const w = (i + 1) * 4
    quad(v + 0, v + 1, w + 0, w + 1) // top
    quad(v + 3, v + 2, w + 3, w + 2) // bottom
    quad(v + 2, v + 0, w + 2, w + 0) // left wall
    quad(v + 1, v + 3, w + 1, w + 3) // right wall
  }
  return { positions, indices: Uint32Array.from(indices) }
}

/** Centre of the bounding box of every point (to put a circuit at the origin). */
export function centre(sets: [number, number][][]): [number, number] {
  const all = sets.flat()
  const xs = all.map((p) => p[0])
  const zs = all.map((p) => p[1])
  return [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...zs) + Math.max(...zs)) / 2]
}

/** Largest half-extent of the circuit, to frame the camera. */
export function radius(sets: [number, number][][]): number {
  const [cx, cz] = centre(sets)
  return Math.max(...sets.flat().map(([x, z]) => Math.hypot(x - cx, z - cz)), 1)
}
