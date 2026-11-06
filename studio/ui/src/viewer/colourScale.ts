// Colour scales for result surfaces (conventions "Bone strain", research R11). The colours are display only: key
// numbers always come from integration points. Comparisons share one range per field (Principle VII), so the range
// is chosen by the page, not by each part.

import { CONTINUOUS_SCALE, FROST_SCALE } from './colourScales.generated';

export type Rgb = readonly [number, number, number];

export type FieldName = 'frost' | 'strain' | 'strain_maxp' | 'stress' | 'pct_strength';

export interface FieldInfo {
  /** The vertex attribute as three's glTF loader names it (lower case). */
  attribute: string;
  label: string;
  unit: string;
  categorical: boolean;
}

export const FIELDS: Record<FieldName, FieldInfo> = {
  frost: { attribute: '_frost_class', label: 'Bone strain (Frost windows)', unit: '', categorical: true },
  strain: { attribute: '_strain_ue', label: 'Equivalent strain', unit: 'µε', categorical: false },
  strain_maxp: { attribute: '_strain_maxp_ue', label: '|Maximum principal strain|', unit: 'µε', categorical: false },
  stress: { attribute: '_stress_vm', label: 'Von Mises stress', unit: 'MPa', categorical: false },
  pct_strength: { attribute: '_pct_strength', label: 'Stress as % of strength', unit: '%', categorical: false },
};

/** Grey for surfaces a field does not apply to (Frost classes on metal, % of strength on bone). */
export const NOT_APPLICABLE: Rgb = [0.72, 0.72, 0.74];

export function hexToRgb(hex: string): Rgb {
  const value = hex.replace('#', '');
  const n = Number.parseInt(value.length === 3 ? [...value].map((c) => c + c).join('') : value, 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

const STOPS = CONTINUOUS_SCALE.stops.map(([t, hex]) => [t, hexToRgb(hex)] as const);
const FROST = FROST_SCALE.categories.map((c) => hexToRgb(c.colour));

/** The continuous scale (viridis) at t, clamped to [0, 1]. */
export function continuousColour(t: number): Rgb {
  const x = Number.isFinite(t) ? Math.min(1, Math.max(0, t)) : 0;
  for (let i = 1; i < STOPS.length; i++) {
    const [t1, c1] = STOPS[i]!;
    if (x <= t1) {
      const [t0, c0] = STOPS[i - 1]!;
      const f = t1 === t0 ? 0 : (x - t0) / (t1 - t0);
      return [c0[0] + f * (c1[0] - c0[0]), c0[1] + f * (c1[1] - c0[1]), c0[2] + f * (c1[2] - c0[2])];
    }
  }
  return STOPS[STOPS.length - 1]![1];
}

/** The Frost window colour of a class index, or null for a surface that is not bone (index −1). */
export function frostColour(index: number): Rgb | null {
  if (!Number.isFinite(index) || index < 0) return null;
  return FROST[Math.min(FROST.length - 1, Math.round(index))] ?? null;
}

/** Per-vertex RGB (0–1) for `values` of `field`, the continuous fields mapped over `range`. */
export function colourise(values: ArrayLike<number>, field: FieldName, range: readonly [number, number]): Float32Array {
  const colours = new Float32Array(values.length * 3);
  const [low, high] = range;
  const span = high > low ? high - low : 1;
  for (let i = 0; i < values.length; i++) {
    const v = values[i]!;
    let rgb: Rgb;
    if (field === 'frost') {
      rgb = frostColour(v) ?? NOT_APPLICABLE;
    } else if (field === 'pct_strength' && v < 0) {
      rgb = NOT_APPLICABLE;
    } else {
      rgb = continuousColour((v - low) / span);
    }
    colours[3 * i] = rgb[0];
    colours[3 * i + 1] = rgb[1];
    colours[3 * i + 2] = rgb[2];
  }
  return colours;
}

/** A shared display range: 0 to the 98th percentile of every value given, so a few peak vertices do not wash out
 * the scale. Negative markers (−1, not compared) are left out. */
export function displayRange(values: readonly ArrayLike<number>[]): [number, number] {
  const all: number[] = [];
  for (const array of values) {
    for (let i = 0; i < array.length; i++) {
      const v = array[i]!;
      if (Number.isFinite(v) && v >= 0) all.push(v);
    }
  }
  if (!all.length) return [0, 1];
  all.sort((a, b) => a - b);
  const high = all[Math.min(all.length - 1, Math.floor(0.98 * (all.length - 1)))]!;
  return [0, high > 0 ? high : 1];
}

/** "×500" with the magnification rounded to a readable step. */
export function formatMagnification(factor: number): string {
  return `×${factor >= 100 ? Math.round(factor / 10) * 10 : Math.round(factor)}`;
}
