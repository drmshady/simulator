// Load arrows for the builder's scene: each contact point of each load case, with the force direction the engine
// applies (conventions `force_direction`, docs/conventions.md "load_angle").

import type { LoadCase, NamedPoint } from '../api/types';

/** Arrow length (mm) for a contact point carrying the whole force; a share draws proportionally shorter. */
export const FULL_ARROW_MM = 6;
const MIN_ARROW_MM = 2;
/** The conventions' default azimuth, used when a load case gives no direction (an axial bite). */
const DEFAULT_AZIMUTH_DEG = 90;

/** Colour-blind-safe colours, one per load case in order. */
export const LOAD_COLOURS = ['#0072B2', '#009E73', '#CC79A7', '#E69F00', '#56B4E9', '#F0E442'];

export interface LoadArrow {
  load: string;
  label: string;
  colour: string;
  /** The contact point, where the arrow's tip touches the surface (mm, scenario frame). */
  tip: [number, number, number];
  /** Unit direction of the force (scenario frame). */
  direction: [number, number, number];
  length: number;
}

/** F̂ = (−sin a·cos b, −sin a·sin b, −cos a): 0° is axial toward apical; the azimuth from +x (mesial) toward +y
 * (buccal) is the side the load comes from. */
export function forceDirection(angleDeg: number, azimuthDeg: number = DEFAULT_AZIMUTH_DEG): [number, number, number] {
  const a = (angleDeg * Math.PI) / 180;
  const b = (azimuthDeg * Math.PI) / 180;
  return [-Math.sin(a) * Math.cos(b), -Math.sin(a) * Math.sin(b), -Math.cos(a)];
}

/** One arrow per contact point of each load case. A chewing cycle draws its instants; a point whose block is not
 * placed is skipped. */
export function loadArrows(loads: readonly LoadCase[], points: readonly NamedPoint[]): LoadArrow[] {
  const at = new Map(points.map((p) => [`${p.block}|${p.point}`, p.position_mm as [number, number, number]]));
  const arrows: LoadArrow[] = [];
  loads.forEach((load, index) => {
    const colour = LOAD_COLOURS[index % LOAD_COLOURS.length]!;
    const label = load.name ?? load.id;
    const groups =
      load.pattern === 'chewing_cycle'
        ? (load.instants ?? []).map((i) => ({ direction: i.direction, points: i.contact_points }))
        : [{ direction: load.direction ?? null, points: load.contact_points ?? [] }];
    for (const group of groups) {
      const direction = forceDirection(group.direction?.angle_deg ?? 0, group.direction?.azimuth_deg ?? DEFAULT_AZIMUTH_DEG);
      for (const point of group.points) {
        const tip = point.position_mm
          ? (point.position_mm as [number, number, number])
          : point.point
            ? at.get(`${point.block}|${point.point}`)
            : undefined;
        if (!tip) continue;
        const length = Math.max(MIN_ARROW_MM, FULL_ARROW_MM * point.share);
        arrows.push({ load: load.id, label, colour, tip, direction, length });
      }
    }
  });
  return arrows;
}
