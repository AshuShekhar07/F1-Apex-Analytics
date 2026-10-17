import { OrbitControls } from '@react-three/drei'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { Bloom, EffectComposer } from '@react-three/postprocessing'
import { useLayoutEffect, useMemo, useRef, type ReactNode } from 'react'
import * as THREE from 'three'

import { lapPoints, type Circuit, type Pt } from '../circuitTypes'
import { centre, radius, ribbonArrays } from './ribbon'

const SAMPLE_EVERY_M = 5
const BACKGROUND = '#050506'
const ELEVATION = THREE.MathUtils.degToRad(55)   // camera angle above the ground

/** Road width in metres. A real circuit is 12-15 m, which is a hairline at full-circuit zoom,
    so the road is drawn at ~3% of the circuit's radius (never under 18 m). */
function roadSize(radiusM: number) {
  const width = Math.max(18, radiusM * 0.032)
  return { width, thickness: width * 0.35 }
}

/** Smooth a polyline (Catmull-Rom) and resample it every few metres. */
function smooth(points: Pt[], [cx, cz]: [number, number], closed = false): Pt[] {
  const curve = new THREE.CatmullRomCurve3(
    points.map(([x, y]) => new THREE.Vector3(x - cx, 0, -(y - cz))), closed, 'centripetal', 0.3)
  const n = Math.max(16, Math.round(curve.getLength() / SAMPLE_EVERY_M))
  return curve.getSpacedPoints(n).map((v) => [v.x, v.z] as Pt)
}

function Ribbon({ points, colour, width, thickness }: { points: Pt[]; colour: string; width: number; thickness: number }) {
  const geometry = useMemo(() => {
    const { positions, indices } = ribbonArrays(points, width, thickness)
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.BufferAttribute(positions, 3))
    g.setIndex(new THREE.BufferAttribute(indices, 1))
    g.computeVertexNormals()
    return g
  }, [points, width, thickness])
  return (
    <mesh geometry={geometry}>
      <meshStandardMaterial color={colour} emissive={colour} emissiveIntensity={0.45} roughness={0.35} metalness={0.2}
        side={THREE.DoubleSide} />
    </mesh>
  )
}

/** A glowing "car" lapping the circuit. */
function Car({ lap, height, size }: { lap: Pt[]; height: number; size: number }) {
  const ref = useRef<THREE.Mesh>(null)
  const curve = useMemo(() => new THREE.CatmullRomCurve3(lap.map(([x, z]) => new THREE.Vector3(x, height + size, z)), true), [lap, height, size])
  useFrame(({ clock }) => {
    if (ref.current) ref.current.position.copy(curve.getPointAt((clock.elapsedTime / 14) % 1))
  })
  return (
    <mesh ref={ref}>
      <sphereGeometry args={[size, 16, 16]} />
      <meshBasicMaterial color="#ffffff" toneMapped={false} />
    </mesh>
  )
}

/** Tilts the circuit a little toward the pointer, so it reacts to hovering. */
function PointerTilt({ children }: { children: ReactNode }) {
  const group = useRef<THREE.Group>(null)
  const { pointer } = useThree()
  useFrame(() => {
    if (!group.current) return
    group.current.rotation.x = THREE.MathUtils.lerp(group.current.rotation.x, -pointer.y * 0.12, 0.05)
    group.current.rotation.z = THREE.MathUtils.lerp(group.current.rotation.z, pointer.x * 0.08, 0.05)
  })
  return <group ref={group}>{children}</group>
}

/** Puts the camera where the whole circuit fits the canvas at any rotation (the circuit's
    radius against the narrower of the two fields of view), and fogs only what lies beyond it. */
function FitCamera({ size }: { size: number }) {
  const { camera, scene, size: view } = useThree()
  useLayoutEffect(() => {
    const cam = camera as THREE.PerspectiveCamera
    const halfV = THREE.MathUtils.degToRad(cam.fov / 2)
    const halfH = Math.atan(Math.tan(halfV) * (view.width / Math.max(view.height, 1)))
    const distance = (size * 1.12) / Math.tan(Math.min(halfV, halfH))
    cam.position.set(0, distance * Math.sin(ELEVATION), distance * Math.cos(ELEVATION))
    cam.near = distance / 50
    cam.far = distance * 6
    cam.lookAt(0, 0, 0)
    cam.updateProjectionMatrix()
    scene.fog = new THREE.Fog(BACKGROUND, distance + size * 1.2, distance + size * 4)
  }, [camera, scene, size, view.width, view.height])
  return null
}

export default function TrackScene({ circuit }: { circuit: Circuit }) {
  const { sectors, lap, size } = useMemo(() => {
    const raw = circuit.sectors.map((s) => s.points)
    const c = centre(raw)
    return {
      sectors: circuit.sectors.map((s) => ({ points: smooth(s.points, c), colour: s.hex })),
      lap: smooth(lapPoints(circuit), c, true),
      size: radius(raw),
    }
  }, [circuit])
  const road = roadSize(size)

  return (
    <Canvas dpr={[1, 2]} camera={{ fov: 40 }} gl={{ antialias: true }}>
      <color attach="background" args={[BACKGROUND]} />
      <FitCamera size={size} />
      <ambientLight intensity={0.35} />
      <directionalLight position={[size, size * 2, size * 0.6]} intensity={0.9} />
      <PointerTilt>
        {sectors.map((s, i) => <Ribbon key={i} points={s.points} colour={s.colour} {...road} />)}
        <Car lap={lap} height={road.thickness} size={road.width * 0.55} />
        <gridHelper args={[size * 4, 40, '#2a2a33', '#16161c']} position={[0, -road.thickness * 0.4, 0]} />
      </PointerTilt>
      <OrbitControls enableZoom={false} enablePan={false} autoRotate autoRotateSpeed={0.7}
        minPolarAngle={0.25} maxPolarAngle={Math.PI / 2.3} />
      <EffectComposer>
        <Bloom intensity={0.35} luminanceThreshold={0.35} luminanceSmoothing={0.3} mipmapBlur />
      </EffectComposer>
    </Canvas>
  )
}
