import { OrbitControls } from '@react-three/drei';
import { Canvas, useThree } from '@react-three/fiber';
import { Suspense, useEffect, useRef, type ReactNode } from 'react';
import type * as THREE from 'three';

import type { Vec3 } from '../organise';
import type { CameraLink } from './cameraLink';

/** What the camera link needs of the orbit controls. */
interface Controls {
  target: THREE.Vector3;
  update: () => void;
  addEventListener: (type: 'change', listener: () => void) => void;
  removeEventListener: (type: 'change', listener: () => void) => void;
}

/** Keeps this view's camera on the link's pose, and publishes the pose when the owner moves this view. */
function Follow({ link }: { link: CameraLink }) {
  const camera = useThree((state) => state.camera);
  const controls = useThree((state) => state.controls) as unknown as Controls | null;
  const token = useRef({});
  useEffect(() => {
    if (!controls) return;
    let applying = false;
    const apply = ({ position, target }: { position: Vec3; target: Vec3 }) => {
      applying = true;
      camera.position.set(...position);
      controls.target.set(...target);
      controls.update();
      applying = false;
    };
    const onChange = () => {
      if (applying) return;
      link.publish(
        { position: camera.position.toArray() as Vec3, target: controls.target.toArray() as Vec3 },
        token.current,
      );
    };
    apply(link.pose);
    controls.addEventListener('change', onChange);
    const unsubscribe = link.subscribe((pose, source) => {
      if (source !== token.current) apply(pose);
    });
    return () => {
      controls.removeEventListener('change', onChange);
      unsubscribe();
    };
  }, [camera, controls, link]);
  return null;
}

export interface SyncedSceneProps {
  link: CameraLink;
  children: ReactNode;
  height?: number;
}

/** A 3D view whose camera is shared through `link`: no fit to its own content, so every panel keeps one scale. */
export function SyncedScene({ link, children, height = 320 }: SyncedSceneProps) {
  return (
    <div style={{ height, width: '100%', borderRadius: 8, overflow: 'hidden', background: '#f3f4f6' }}>
      <Canvas camera={{ position: link.pose.position, up: [0, 0, 1], fov: 35, near: 0.5, far: 2000 }} dpr={[1, 2]}>
        <ambientLight intensity={0.55} />
        <directionalLight position={[50, -80, 120]} intensity={1.1} />
        <directionalLight position={[-60, 60, -40]} intensity={0.35} />
        <Suspense fallback={null}>{children}</Suspense>
        <OrbitControls makeDefault />
        <Follow link={link} />
      </Canvas>
    </div>
  );
}
