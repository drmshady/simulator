import { describe, expect, it } from 'vitest';

import type { Difference } from '../src/api/types';
import {
  changedBlocks,
  cleanTags,
  describeDifference,
  formatDelta,
  formatValue,
  framing,
  parseRunIds,
  settingChanges,
} from '../src/organise';

// Story 3 (tasks T118-T120): projects, duplicate and change, comparison.

function difference(changes: Partial<Difference>): Difference {
  return { path: 'implant-1.length_mm', change: 'changed', before: 10, after: 8, block: 'implant-1', load: null, group: 'setting', ...changes };
}

describe('tags', () => {
  it('are trimmed, kept once ignoring case, and empty ones dropped', () => {
    expect(cleanTags([' lengths ', 'Lengths', '', 'short  implant'])).toEqual(['lengths', 'short implant']);
  });
});

describe('settings differences', () => {
  it('show both values, or what was added or removed', () => {
    expect(describeDifference(difference({}))).toBe('implant-1.length_mm: 10 → 8');
    expect(describeDifference(difference({ path: 'cap-1', change: 'added', before: null, after: { type_id: 'cap', type_version: '1.0.0' }, group: 'block' }))).toBe(
      'cap-1 added (cap 1.0.0)',
    );
    expect(describeDifference(difference({ path: 'load:oblique', change: 'removed', after: null, group: 'load', block: null }))).toBe('load:oblique removed');
    expect(describeDifference(difference({ change: 'removed', after: null }))).toBe('implant-1.length_mm removed (was 10)');
  });

  it('format library values, lists and flags', () => {
    expect(formatValue({ value: 200, entry_id: 'E', entry_version: 2, choice: 'typical' })).toBe('200 (library v2, typical)');
    expect(formatValue([0.5, 0, 0])).toBe('(0.5, 0, 0)');
    expect(formatValue(true)).toBe('yes');
    expect(formatValue(null)).toBe('–');
    expect(formatValue({ instance_id: 'jaw', attach_point: 'site:36' })).toBe('jaw at site:36');
  });

  it('pick out the changed settings of a block, and the changed blocks', () => {
    const all = [
      difference({}),
      difference({ path: 'implant-1.placement.position_mm', group: 'placement' }),
      difference({ path: 'crown-1.material', block: 'crown-1' }),
      difference({ path: 'load:axial.force', block: null, load: 'axial', group: 'load' }),
    ];
    expect([...settingChanges(all, 'implant-1').keys()]).toEqual(['length_mm']);
    expect([...changedBlocks(all)].sort()).toEqual(['crown-1', 'implant-1']);
  });
});

describe('comparison', () => {
  it('formats key-number differences with their sign', () => {
    expect(formatDelta({ run_id: 'B', absolute: 300, relative: 0.3 }, 'µε')).toBe('+300 µε (+30.0%)');
    expect(formatDelta({ run_id: 'B', absolute: -12.5, relative: -0.05 }, 'MPa')).toBe('−12.5 MPa (−5.0%)');
    expect(formatDelta({ run_id: 'B', absolute: 3, relative: null }, 'µm')).toBe('+3 µm');
    expect(formatDelta(null, 'µε')).toBe('–');
  });

  it('reads the run ids once each, in order', () => {
    expect(parseRunIds(' A,B,,A , C')).toEqual(['A', 'B', 'C']);
    expect(parseRunIds(null)).toEqual([]);
  });

  it('frames every panel on the centre of the shared box', () => {
    const pose = framing([
      [0, 0, 0],
      [10, 20, 10],
    ]);
    expect(pose.target).toEqual([5, 10, 5]);
    const distance = Math.hypot(...pose.position.map((p, i) => p - pose.target[i]!));
    expect(distance).toBeCloseTo(1.7 * Math.hypot(10, 20, 10));
    expect(pose.position[1]).toBeLessThan(pose.target[1]);
  });
});
