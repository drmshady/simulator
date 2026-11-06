import { Bounds, OrbitControls } from '@react-three/drei';
import { Canvas } from '@react-three/fiber';
import { Suspense, type ReactNode } from 'react';

export interface SceneProps {
  children: ReactNode;
  height?: number | string;
  /** Fit the view to the content when it changes. */
  fit?: boolean;
}

/** The 3D view: millimetres in the conventions frame (+z occlusal), orbit controls, soft lights. */
export function Scene({ children, height = 480, fit = true }: SceneProps) {
  return (
    <div style={{ height, width: '100%', borderRadius: 8, overflow: 'hidden', background: '#f3f4f6' }}>
      <Canvas camera={{ position: [40, -60, 30], up: [0, 0, 1], fov: 35, near: 0.5, far: 2000 }} dpr={[1, 2]}>
        <ambientLight intensity={0.55} />
        <directionalLight position={[50, -80, 120]} intensity={1.1} />
        <directionalLight position={[-60, 60, -40]} intensity={0.35} />
        <Suspense fallback={null}>
          {fit ? (
            <Bounds fit clip observe margin={1.2}>
              {children}
            </Bounds>
          ) : (
            children
          )}
        </Suspense>
        <OrbitControls makeDefault enableDamping />
      </Canvas>
    </div>
  );
}
