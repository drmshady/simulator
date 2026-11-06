// Generated from docs/conventions.md version 1.0.0 (hash dca81af57ab8fbbd3a1e40260f0cc15a1b1732d3d2465811b40f78c751a7e834).
// Do not edit: change the conventions block, then run `npm --prefix studio/ui run gen:conventions`.

export const CONVENTIONS_VERSION: string = '1.0.0';
export const CONVENTIONS_HASH: string = 'dca81af57ab8fbbd3a1e40260f0cc15a1b1732d3d2465811b40f78c751a7e834';

export const UNITS: {
  readonly length: string;
  readonly force: string;
  readonly stress: string;
  readonly strain: string;
  readonly strainSymbol: string;
} = {
  length: 'mm',
  force: 'N',
  stress: 'MPa',
  strain: 'microstrain',
  strainSymbol: 'µε',
} as const;

export const TEACHING_NOTICE: string = 'For teaching only. These results come from a generic model, not from any patient, and are not a prediction for any patient.';

export const FROST_SCALE: {
  readonly label: string;
  readonly measure: string;
  readonly categories: readonly { readonly id: string; readonly label: string; readonly colour: string }[];
  readonly edges: readonly string[];
} = {
  label: 'teaching reference',
  measure: 'von_mises_equivalent',
  categories: [
    { id: 'disuse', label: 'Disuse', colour: '#56B4E9' },
    { id: 'adapted', label: 'Adapted window', colour: '#009E73' },
    { id: 'mild_overload', label: 'Mild overload', colour: '#E69F00' },
    { id: 'pathological_overload', label: 'Pathological overload', colour: '#D55E00' },
  ],
  edges: [
    'frost.disuse_upper',
    'frost.adapted_upper',
    'frost.mild_overload_upper',
  ],
} as const;

export const CONTINUOUS_SCALE: {
  readonly name: string;
  readonly stops: readonly (readonly [number, string])[];
} = {
  name: 'viridis',
  stops: [
    [0, '#440154'],
    [0.125, '#472D7B'],
    [0.25, '#3B528B'],
    [0.375, '#2C728E'],
    [0.5, '#21918C'],
    [0.625, '#28AE80'],
    [0.75, '#5EC962'],
    [0.875, '#ADDC30'],
    [1, '#FDE725'],
  ],
} as const;
