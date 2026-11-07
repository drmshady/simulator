import { describe, expect, it } from 'vitest';

import type { BlockTypeEntry, Scenario, Spread } from '../src/api/types';
import { formatAxisValue, formatSpread, isActive, parseValues, runCount, settingOptions } from '../src/study';

// Story 4 (task T126): the study dialog's settings and values, and the study page's spreads.

const IMPLANT: BlockTypeEntry = {
  type_id: 'implant',
  version: '1.0.0',
  slice: '1a',
  label: 'Implant',
  description: '',
  settings_schema: {
    title: 'Implant',
    properties: {
      length_mm: { title: 'Length', group: 'implant', kind: 'design', type: 'number', unit: 'mm', default: 10 },
      thread: { title: 'Thread', group: 'implant', kind: 'design', type: 'string', enum: ['v_thread', 'square'] },
      region: { title: 'Region', group: 'implant', kind: 'design', type: 'array', items: { type: 'string' } },
      E: { title: 'Elastic modulus', group: 'implant', kind: 'library', type: 'number', unit: 'MPa' },
    },
    required: [],
    groups: [],
  },
  attach_points: [],
  attaches_to: [],
  needs_physics: [],
  result_types: [],
  report_regions: [],
  status: 'ready',
  awaiting: [],
};

const SCENARIO = {
  format: 'dentalsim.scenario/1',
  name: 'Single crown',
  conventions_version: '1.1.0',
  blocks: [
    {
      instance_id: 'implant-1',
      type_id: 'implant',
      type_version: '1.0.0',
      settings: {
        length_mm: { kind: 'design', value: 8 },
        E: { kind: 'library', entry_id: 'E1', entry_version: 1, choice: 'typical', value: 104500 },
      },
    },
  ],
  loads: [
    {
      id: 'oblique',
      name: 'Oblique bite',
      pattern: 'single_bite',
      force: { kind: 'library', entry_id: 'F', entry_version: 1, choice: 'typical', value: 601.83 },
      direction: { angle_deg: 30, azimuth_deg: 90 },
      contact_points: [],
    },
  ],
} as unknown as Scenario;

describe('the settings a study can vary', () => {
  const options = settingOptions(SCENARIO, [IMPLANT]);
  const option = (path: string) => {
    const found = options.find((o) => o.value === path);
    if (!found) throw new Error(`no option ${path}`);
    return found;
  };

  it('are every single-valued block setting and each load case force and direction', () => {
    expect(options.map((o) => o.value)).toEqual([
      'implant-1.length_mm',
      'implant-1.thread',
      'implant-1.E',
      'load:oblique.force',
      'load:oblique.direction.angle_deg',
      'load:oblique.direction.azimuth_deg',
    ]);
    const length = option('implant-1.length_mm');
    expect([length.current, length.unit, length.loadOnly]).toEqual([8, 'mm', false]);
    expect(options.find((o) => o.value === 'implant-1.E')?.kind).toBe('library');
    expect(options.filter((o) => o.loadOnly).map((o) => o.value)).toEqual([
      'load:oblique.force',
      'load:oblique.direction.angle_deg',
      'load:oblique.direction.azimuth_deg',
    ]);
  });

  it('take typed values, checked against the setting', () => {
    const length = option('implant-1.length_mm');
    const thread = option('implant-1.thread');
    expect(parseValues('8, 10 11.5;13', length)).toEqual({ values: [8, 10, 11.5, 13], error: null });
    expect(parseValues('8, long', length).error).toBe('"long" is not a number.');
    expect(parseValues('8, 8', length).error).toBe('A value is listed twice.');
    expect(parseValues('  ', length).error).toBe('Type at least one value.');
    expect(parseValues('v_thread square', thread).values).toEqual(['v_thread', 'square']);
    expect(parseValues('round', thread).error).toBe('"round" is not one of v_thread, square.');
  });
});

describe('studies', () => {
  it('make one run per combination of values', () => {
    expect(runCount([{ values: [8, 9, 10, 11, 12, 13] }, { values: ['D1', 'D2', 'D3'] }])).toBe(18);
    expect(runCount([])).toBe(0);
  });

  it('show values and spreads plainly', () => {
    expect(formatAxisValue(11.5)).toBe('11.5');
    expect(formatAxisValue(0.30000000000000004)).toBe('0.3');
    expect(formatAxisValue(true)).toBe('yes');
    const spread = { min: 800, max: 1200, range: 400, percent_change: 33.333, unit: 'µε' } as Spread;
    expect(formatSpread(spread)).toBe('800–1200 µε (range 400, 33.3%)');
    expect(formatSpread({ ...spread, min: 0.5, max: 0.75, range: 0.25, percent_change: null, unit: 'mm' })).toBe(
      '0.5–0.75 mm (range 0.25)',
    );
  });

  it('can be cancelled while queued or running', () => {
    expect(isActive({ status: 'running' })).toBe(true);
    expect(isActive({ status: 'finished_with_failures' })).toBe(false);
  });
});
