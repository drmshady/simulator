import { describe, expect, it } from 'vitest';

import type { LoadCase, NamedPoint } from '../src/api/types';
import { FULL_ARROW_MM, forceDirection, loadArrows } from '../src/viewer/loadArrows';

const force = { kind: 'library', entry_id: 'e', entry_version: 1, choice: 'typical', value: 200, outside_range: false } as LoadCase['force'];
const points: NamedPoint[] = [
  { block: 'crown-1', point: 'central_fossa', label: 'Central fossa', position_mm: [0, 0, 9] },
  { block: 'crown-1', point: 'buccal_cusp', label: 'Buccal cusp', position_mm: [0, 2, 10] },
];

function bite(id: string, change: Partial<LoadCase> = {}): LoadCase {
  return { id, name: null, pattern: 'single_bite', force, direction: null, contact_points: [], instants: [], cycles: null, ...change } as LoadCase;
}

describe('load arrows in the builder', () => {
  it('follows the conventions sign: axial is apical, +30° at azimuth 90° tilts toward lingual', () => {
    expect(forceDirection(0)).toEqual([expect.closeTo(0), expect.closeTo(0), -1]);
    const [x, y, z] = forceDirection(30, 90);
    expect(x).toBeCloseTo(0);
    expect(y).toBeCloseTo(-0.5);
    expect(z).toBeCloseTo(-Math.sqrt(3) / 2);
  });

  it('draws one arrow per contact point, its tip on the point and its length by share', () => {
    const arrows = loadArrows(
      [
        bite('load-1', { contact_points: [{ block: 'crown-1', point: 'central_fossa', share: 1 }] }),
        bite('load-2', {
          name: 'Oblique',
          direction: { angle_deg: 30, azimuth_deg: 90 },
          contact_points: [
            { block: 'crown-1', point: 'buccal_cusp', share: 0.5 },
            { block: 'crown-9', point: 'buccal_cusp', share: 0.5 },
          ],
        }),
      ],
      points,
    );
    expect(arrows.map((a) => [a.load, a.label, a.tip, a.length])).toEqual([
      ['load-1', 'load-1', [0, 0, 9], FULL_ARROW_MM],
      ['load-2', 'Oblique', [0, 2, 10], FULL_ARROW_MM / 2],
    ]);
    expect(arrows[0]!.colour).not.toBe(arrows[1]!.colour);
  });
});
