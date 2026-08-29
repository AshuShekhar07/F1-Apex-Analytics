import { useMemo } from 'react'
import * as THREE from 'three'

const TUBE_RADIUS = 6
const TUBE_RADIAL_SEGMENTS = 12
const SEGMENTS_PER_METER = 0.4

function pointsToCurve(points) {
  const vectors = points.map(([x, y]) => new THREE.Vector3(x, 0, y))
  return new THREE.CatmullRomCurve3(vectors, false, 'centripetal', 0.15)
}

function SectorTube({ points, color, emissiveIntensity }) {
  const geometry = useMemo(() => {
    const curve = pointsToCurve(points)
    const length = curve.getLength()
    const tubularSegments = Math.max(24, Math.round(length * SEGMENTS_PER_METER))
    return new THREE.TubeGeometry(curve, tubularSegments, TUBE_RADIUS, TUBE_RADIAL_SEGMENTS, false)
  }, [points])

  return (
    <mesh geometry={geometry} castShadow receiveShadow>
      <meshStandardMaterial
        color={color}
        emissive={color}
        emissiveIntensity={emissiveIntensity}
        roughness={0.3}
        metalness={0.15}
      />
    </mesh>
  )
}

export default function CircuitTrack3D({ track, emissiveIntensity = 1.1 }) {
  const { sector1, sector2, sector3, colors } = track

  const allPoints = [...sector1, ...sector2, ...sector3]
  const xs = allPoints.map((p) => p[0])
  const ys = allPoints.map((p) => p[1])
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2
  const cy = (Math.min(...ys) + Math.max(...ys)) / 2
  const recentre = (pts) => pts.map(([x, y]) => [x - cx, y - cy])

  return (
    <group>
      <SectorTube points={recentre(sector1)} color={colors.sector1.hex} emissiveIntensity={emissiveIntensity} />
      <SectorTube points={recentre(sector2)} color={colors.sector2.hex} emissiveIntensity={emissiveIntensity} />
      <SectorTube points={recentre(sector3)} color={colors.sector3.hex} emissiveIntensity={emissiveIntensity} />
    </group>
  )
}
