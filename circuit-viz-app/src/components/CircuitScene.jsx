import { Suspense } from 'react'
import { Canvas } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import { EffectComposer, Bloom } from '@react-three/postprocessing'
import CircuitTrack3D from './CircuitTrack3D'

export default function CircuitScene({ track }) {
  return (
    <Canvas
      shadows
      dpr={[1, 2]}
      camera={{ position: [500, 650, 900], fov: 42, near: 1, far: 6000 }}
      gl={{ antialias: true }}
    >
      <color attach="background" args={['#05070a']} />
      <fog attach="fog" args={['#05070a', 1500, 4500]} />
      <ambientLight intensity={0.25} />
      <directionalLight
        position={[400, 800, 200]}
        intensity={0.6}
        castShadow
        shadow-mapSize-width={1024}
        shadow-mapSize-height={1024}
      />
      <directionalLight position={[-500, 300, -400]} intensity={0.15} color="#2be0d3" />
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -8, 0]} receiveShadow>
        <planeGeometry args={[4000, 4000]} />
        <meshStandardMaterial color="#0b0d0e" roughness={1} />
      </mesh>
      <Suspense fallback={null}>
        <CircuitTrack3D track={track} />
      </Suspense>
      <OrbitControls
        enablePan={false}
        minDistance={300}
        maxDistance={2200}
        maxPolarAngle={Math.PI / 2.1}
      />
      <EffectComposer>
        <Bloom
          intensity={0.9}
          luminanceThreshold={0.15}
          luminanceSmoothing={0.3}
          mipmapBlur
        />
      </EffectComposer>
    </Canvas>
  )
}
