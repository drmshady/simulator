import { useGLTF } from '@react-three/drei';
import { useMemo } from 'react';
import * as THREE from 'three';

import { FIELDS, type FieldName } from './colourScale';

/** The values of a field in a `.glb`, for a colour range shared across parts. */
export function useFieldValues(url: string, field: FieldName | null): Float32Array | null {
  const gltf = useGLTF(url);
  return useMemo(() => {
    if (!field) return null;
    const found: number[] = [];
    gltf.scene.traverse((object) => {
      if (object instanceof THREE.Mesh) {
        const attribute = (object.geometry as THREE.BufferGeometry).getAttribute(FIELDS[field].attribute);
        if (attribute) found.push(...Array.from(attribute.array as ArrayLike<number>));
      }
    });
    return Float32Array.from(found);
  }, [gltf.scene, field]);
}
