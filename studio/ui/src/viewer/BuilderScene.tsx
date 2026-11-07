import { Bounds, TransformControls, useGLTF } from '@react-three/drei';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';

import { apiGet } from '../api/client';
import type { ConventionsInfo, ValidityProblem } from '../api/types';
import type { LoadArrow } from './loadArrows';

const CLASS_COLOURS: Record<string, string> = {
  metal: '#9aa3ad',
  brittle: '#f2eee4',
  bone: '#e3d3ae',
  not_compared: '#c9b8a8',
};
const KEEP_OUT_COLOUR = '#D55E00';
const SELECTED_EMISSIVE = '#1c64f2';
const PROBLEM_EMISSIVE = '#b42318';

export interface BuilderSceneProps {
  url: string;
  selected: string | null;
  /** Whether the selected block can be dragged (the root block, which sets the frame, cannot). */
  movable: boolean;
  problemBlocks: ReadonlySet<string>;
  problems: ValidityProblem[];
  /** The load cases' arrows, drawn with their tips on the contact points. */
  loads?: LoadArrow[];
  onSelect: (instanceId: string) => void;
  /** Called when a drag of the selected block ends, with how far it moved (mm, frame axes). */
  onMove: (instanceId: string, delta: [number, number, number]) => void;
}

function useMaterialClasses() {
  return useQuery({
    queryKey: ['conventions'],
    queryFn: ({ signal }) => apiGet<ConventionsInfo>('/api/conventions', { signal }),
    staleTime: Infinity,
  });
}

/** The builder's scene: each placed block's parts in its material class's colour, the keep-out volumes in
 * translucent red, problem locations marked, the load cases as arrows, the selected block highlighted and draggable. Use it in a `Scene`
 * with `fit={false}`: the view is fitted to the parts only, since the drag gizmo carries very large invisible
 * planes that would otherwise zoom the view out until the model vanished. The fit is made once, so the view stays
 * put while the scenario is edited. */
export function BuilderScene({ url, selected, movable, problemBlocks, problems, loads = [], onSelect, onMove }: BuilderSceneProps) {
  const gltf = useGLTF(url);
  const conventions = useMaterialClasses();
  const classOf = useMemo(() => {
    const map = new Map<string, string>();
    for (const [name, materials] of Object.entries(conventions.data?.material_classes ?? {})) {
      for (const material of materials as string[]) map.set(material, name);
    }
    return map;
  }, [conventions.data]);

  // One group per block, so a block can be dragged as a whole.
  const groups = useMemo(() => {
    const byBlock = new Map<string, THREE.Group>();
    gltf.scene.clone(true).traverse((object) => {
      if (!(object instanceof THREE.Mesh)) return;
      const block = String(object.userData.block ?? '');
      let group = byBlock.get(block);
      if (!group) {
        group = new THREE.Group();
        group.userData.block = block;
        byBlock.set(block, group);
      }
      const mesh = object.clone();
      mesh.userData = { ...object.userData };
      group.add(mesh);
    });
    return byBlock;
  }, [gltf.scene]);

  useEffect(() => {
    for (const [block, group] of groups) {
      group.traverse((object) => {
        if (!(object instanceof THREE.Mesh)) return;
        const keepOut = object.userData.kind === 'keep_out';
        const colour = keepOut ? KEEP_OUT_COLOUR : CLASS_COLOURS[classOf.get(String(object.userData.material)) ?? ''] ?? '#c8c2b4';
        const emissive = block === selected ? SELECTED_EMISSIVE : problemBlocks.has(block) && !keepOut ? PROBLEM_EMISSIVE : '#000000';
        object.material = new THREE.MeshStandardMaterial({
          color: colour,
          emissive,
          emissiveIntensity: emissive === '#000000' ? 0 : 0.35,
          transparent: keepOut || object.userData.component === 'bone',
          opacity: keepOut ? 0.22 : object.userData.component === 'bone' ? 0.55 : 1,
          depthWrite: !keepOut,
          side: THREE.DoubleSide,
          roughness: 0.8,
        });
        object.geometry.computeVertexNormals();
      });
    }
  }, [groups, selected, problemBlocks, classOf]);

  const dragged = useRef<THREE.Group | null>(null);
  const selectedGroup = selected ? groups.get(selected) : undefined;

  return (
    <group>
      <Bounds fit clip observe margin={1.2}>
        {[...groups.entries()].map(([block, group]) => (
          <primitive
            key={block}
            object={group}
            onPointerDown={(event: { stopPropagation: () => void }) => {
              event.stopPropagation();
              onSelect(block);
            }}
          />
        ))}
      </Bounds>
      {selectedGroup && movable && (
        <TransformControls
          object={selectedGroup}
          mode="translate"
          size={0.6}
          onMouseDown={() => {
            dragged.current = selectedGroup;
          }}
          onMouseUp={() => {
            const group = dragged.current;
            dragged.current = null;
            if (!group || !selected) return;
            const { x, y, z } = group.position;
            if (Math.hypot(x, y, z) > 1e-3) onMove(selected, [x, y, z]);
          }}
        />
      )}
      {problems
        .filter((p) => p.location_mm)
        .map((p, index) => (
          <mesh key={`${p.code}-${index}`} position={p.location_mm as unknown as [number, number, number]}>
            <sphereGeometry args={[0.6, 16, 12]} />
            <meshStandardMaterial color={PROBLEM_EMISSIVE} emissive={PROBLEM_EMISSIVE} emissiveIntensity={0.6} />
          </mesh>
        ))}
      {loads.map((arrow, index) => (
        <ForceArrow key={`${arrow.load}-${index}`} arrow={arrow} />
      ))}
    </group>
  );
}

/** A force arrow whose tip touches the contact point, pointing along the force. */
function ForceArrow({ arrow }: { arrow: LoadArrow }) {
  const helper = useMemo(() => {
    const direction = new THREE.Vector3(...arrow.direction).normalize();
    const origin = new THREE.Vector3(...arrow.tip).addScaledVector(direction, -arrow.length);
    const head = Math.min(1.6, arrow.length * 0.35);
    const made = new THREE.ArrowHelper(direction, origin, arrow.length, arrow.colour, head, head * 0.55);
    // Drawn over the parts, so an arrow is never hidden inside a crown.
    for (const part of [made.line, made.cone]) {
      const material = part.material as THREE.Material;
      material.depthTest = false;
      material.transparent = true;
      part.renderOrder = 10;
    }
    made.raycast = () => undefined;
    made.line.raycast = () => undefined;
    made.cone.raycast = () => undefined;
    return made;
  }, [arrow]);
  useEffect(() => () => helper.dispose(), [helper]);
  return <primitive object={helper} />;
}
