import { describe, expect, it } from 'vitest';

import type { BlockTypeEntry, KeyNumber, Scenario, SettingSchema } from '../src/api/types';
import { addBlock, blockTree, moveBlock, removeBlock, socketsFor, subtree, upgradeBlocks } from '../src/builder';
import {
  designValueOf,
  evenShares,
  formatDuration,
  groupedSettings,
  limitProblem,
  paired,
  sharesOk,
} from '../src/display';
import {
  colourise,
  continuousColour,
  displayRange,
  formatMagnification,
  frostColour,
  hexToRgb,
  NOT_APPLICABLE,
} from '../src/viewer/colourScale';

function block(id: string, type: string, parent: [string, string] | null) {
  return {
    instance_id: id,
    type_id: type,
    type_version: '1.0.0',
    attach_to: parent ? { instance_id: parent[0], attach_point: parent[1] } : null,
    placement: { position_mm: [0, 0, 0], axis_angles_deg: { mesiodistal: 0, buccolingual: 0 }, rotation_deg: 0 },
    settings: {},
  };
}

const SCENARIO = {
  format: 'dentalsim.scenario/1',
  name: 'Test',
  conventions_version: '1.1.0',
  detail_level: 'standard',
  blocks: [
    block('crown-1', 'cap', ['abutment-1', 'seat']),
    block('base', 'root_type', null),
    block('abutment-1', 'post', ['fixture-1', 'platform']),
    block('fixture-1', 'fixture', ['base', 'site']),
  ],
  loads: [
    {
      id: 'bite',
      pattern: 'single_bite',
      force: { kind: 'library', entry_id: 'E', entry_version: 1, choice: 'typical', value: 600, outside_range: false },
      direction: { angle_deg: 30, azimuth_deg: 90 },
      contact_points: [{ block: 'crown-1', point: 'cusp', share: 1 }],
      instants: [],
      cycles: null,
    },
  ],
} as unknown as Scenario;

function type(id: string, offers: string[], attachesTo: string[]): BlockTypeEntry {
  return {
    type_id: id,
    version: '2.0.0',
    slice: '1a',
    label: id,
    description: '',
    settings_schema: { title: id, properties: {}, required: [], groups: [] },
    attach_points: offers.map((name) => ({ name, label: name, description: '' })),
    attaches_to: attachesTo,
    needs_physics: [],
    result_types: [],
    report_regions: [],
    status: 'ready',
    awaiting: [],
  };
}

describe('the builder edits scenarios without knowing block types', () => {
  it('orders the tree root first, parents before children', () => {
    expect(blockTree(SCENARIO).map((r) => [r.block.instance_id, r.depth])).toEqual([
      ['base', 0],
      ['fixture-1', 1],
      ['abutment-1', 2],
      ['crown-1', 3],
    ]);
  });

  it('removes a block with everything attached below it, and their loads', () => {
    expect([...subtree(SCENARIO, 'fixture-1')].sort()).toEqual(['abutment-1', 'crown-1', 'fixture-1']);
    const left = removeBlock(SCENARIO, 'abutment-1');
    expect(left.blocks.map((b) => b.instance_id)).toEqual(['base', 'fixture-1']);
    expect(left.loads).toEqual([]);
  });

  it('offers the sockets a new block fits, and adds it with a fresh id', () => {
    const types = [type('root_type', ['site'], []), type('fixture', ['platform'], ['site']), type('post', ['seat'], ['platform']), type('cap', [], ['seat'])];
    // fixture-1 already holds the base's one site, so it is not offered; without it, it is.
    expect(socketsFor(SCENARIO, types[1]!, types)).toEqual([]);
    const free = removeBlock(SCENARIO, 'fixture-1');
    expect(socketsFor(free, types[1]!, types)).toEqual([{ instanceId: 'base', socket: 'site', label: 'base: site' }]);
    const roomy = types.map((t) => (t.type_id === 'root_type' ? { ...t, attach_points: [{ ...t.attach_points[0]!, capacity: 2 }] } : t));
    expect(socketsFor(SCENARIO, types[1]!, roomy)).toHaveLength(1);
    const added = addBlock(SCENARIO, types[1]!, 'base', 'site');
    const created = added.blocks.at(-1)!;
    expect(created.instance_id).toBe('fixture-2');
    expect(created.type_version).toBe('2.0.0');
    expect(created.settings).toEqual({});
  });

  it('moves a block by a drag and upgrades block versions', () => {
    const moved = moveBlock(SCENARIO, 'fixture-1', [0.123, -2, 0.5]);
    expect(moved.blocks.find((b) => b.instance_id === 'fixture-1')!.placement!.position_mm).toEqual([0.12, -2, 0.5]);
    const upgraded = upgradeBlocks(SCENARIO, [{ instance_id: 'crown-1', to_version: '1.1.0' }]);
    expect(upgraded.blocks.find((b) => b.instance_id === 'crown-1')!.type_version).toBe('1.1.0');
  });
});

describe('colour scales', () => {
  it('follows the viridis stops and clamps', () => {
    expect(continuousColour(0)).toEqual(hexToRgb('#440154'));
    expect(continuousColour(1)).toEqual(hexToRgb('#FDE725'));
    expect(continuousColour(2)).toEqual(hexToRgb('#FDE725'));
    expect(continuousColour(Number.NaN)).toEqual(hexToRgb('#440154'));
  });

  it('colours Frost classes, and leaves surfaces a field does not apply to grey', () => {
    expect(frostColour(1)).toEqual(hexToRgb('#009E73'));
    expect(frostColour(-1)).toBeNull();
    const colours = colourise([0, -1], 'frost', [0, 1]);
    expect([...colours.slice(0, 3)]).toEqual([...hexToRgb('#56B4E9')].map((c) => Math.fround(c)));
    expect([...colours.slice(3)]).toEqual([...NOT_APPLICABLE].map((c) => Math.fround(c)));
  });

  it('shares one display range across parts, ignoring not-compared markers', () => {
    const values = Array.from({ length: 100 }, (_, i) => i + 1);
    expect(displayRange([values.slice(0, 50), [-1, -1], values.slice(50)])).toEqual([0, 98]);
    expect(displayRange([])).toEqual([0, 1]);
    expect(formatMagnification(347)).toBe('×350');
  });
});

describe('display helpers', () => {
  it('formats durations and pairs mean with p95', () => {
    expect(formatDuration(42)).toBe('about 42 s');
    expect(formatDuration(600)).toBe('about 10 min');
    expect(formatDuration(9000)).toBe('about 2.5 h');
    const n = (id: string, statistic: 'mean' | 'p95') => ({ id, statistic }) as KeyNumber;
    const rows = paired([n('a.implant.mean', 'mean'), n('a.implant.p95', 'p95'), n('b.crown.mean', 'mean')]);
    expect(rows.map((r) => [r.key, Boolean(r.mean), Boolean(r.p95)])).toEqual([
      ['a.implant', true, true],
      ['b.crown', true, false],
    ]);
  });

  it('checks contact-point shares', () => {
    const points = evenShares([
      { block: 'a', point: 'x', share: 1 },
      { block: 'a', point: 'y', share: 1 },
      { block: 'a', point: 'z', share: 1 },
    ]);
    expect(sharesOk(points)).toBe(true);
    expect(sharesOk([{ block: 'a', point: 'x', share: 0.5 }])).toBe(false);
  });

  it('enforces design limits and groups settings in the schema order', () => {
    const schema: SettingSchema = { title: 'Length', group: 'g1', kind: 'design', type: 'number', unit: 'mm', minimum: 6, maximum: 16, default: 10 };
    expect(designValueOf(schema, undefined)).toBe(10);
    expect(limitProblem(schema, 5)).toBe('At least 6 mm.');
    expect(limitProblem(schema, 12)).toBeNull();
    expect(limitProblem({ ...schema, type: 'string', enum: ['a', 'b'] }, 'c')).toBe('Choose one of a, b.');
    const entry = type('x', [], []);
    entry.settings_schema = {
      title: 'x',
      required: [],
      groups: [
        { id: 'g0', label: 'Empty' },
        { id: 'g1', label: 'First' },
      ],
      properties: { length: schema },
    };
    expect(groupedSettings(entry).map((g) => [g.id, g.settings.length])).toEqual([['g1', 1]]);
  });
});
