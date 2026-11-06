import { useGLTF } from '@react-three/drei';
import { useEffect, useMemo } from 'react';
import * as THREE from 'three';

import { colourise, FIELDS, type FieldName } from './colourScale';

export interface GlbModelProps {
  url: string;
  /** The field the surface is coloured by; null draws it in `colour`. */
  field: FieldName | null;
  /** The shared colour range of a continuous field. */
  range?: readonly [number, number];
  /** The displacement magnification: the weight of the glTF's displacement morph target. */
  magnification?: number;
  colour?: string;
  opacity?: number;
  wireframe?: boolean;
  onPointerDown?: () => void;
}

/** A result or scene surface from a `.glb`: custom vertex attributes become vertex colours, and the displacement
 * morph target's weight is the magnification (research R11). */
export function GlbModel({
  url,
  field,
  range = [0, 1],
  magnification = 0,
  colour = '#c8c2b4',
  opacity = 1,
  wireframe = false,
  onPointerDown,
}: GlbModelProps) {
  const gltf = useGLTF(url);
  const scene = useMemo(() => gltf.scene.clone(true), [gltf.scene]);
  const low = range[0];
  const high = range[1];

  useEffect(() => {
    scene.traverse((object) => {
      if (!(object instanceof THREE.Mesh)) return;
      const geometry = object.geometry as THREE.BufferGeometry;
      const values = field ? geometry.getAttribute(FIELDS[field].attribute) : undefined;
      if (values) {
        geometry.setAttribute(
          'color',
          new THREE.BufferAttribute(colourise(values.array as ArrayLike<number>, field!, [low, high]), 3),
        );
      } else {
        geometry.deleteAttribute('color');
      }
      geometry.computeVertexNormals();
      object.material = new THREE.MeshStandardMaterial({
        color: values ? '#ffffff' : colour,
        vertexColors: Boolean(values),
        transparent: opacity < 1,
        opacity,
        wireframe,
        side: THREE.DoubleSide,
        roughness: 0.75,
        metalness: 0.05,
      });
      if (object.morphTargetInfluences && object.morphTargetInfluences.length > 0) {
        object.morphTargetInfluences[0] = magnification;
      }
    });
  }, [scene, field, low, high, magnification, colour, opacity, wireframe]);

  return <primitive object={scene} onPointerDown={onPointerDown} />;
}
