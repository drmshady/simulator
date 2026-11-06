import { describe, expect, it } from 'vitest';

import type { BlockTypeEntry, Scenario } from '../src/api/types';
import { addBlock, socketsFor } from '../src/builder';

// Slice 1b (task T103, T106): a keyed attach point is offered once per value of a list setting, still without the
// builder knowing any block type. The type ids here are made up on purpose.

function entry(id: string, points: BlockTypeEntry['attach_points'], attachesTo: string[], defaults: Record<string, unknown> = {}): BlockTypeEntry {
  return {
    type_id: id,
    version: '1.0.0',
    slice: '1b',
    label: id,
    description: '',
    settings_schema: {
      title: id,
      properties: Object.fromEntries(Object.entries(defaults).map(([k, v]) => [k, { title: k, group: 'patient_bone', kind: 'design', type: 'string', default: v }])),
      required: [],
      groups: [],
    },
    attach_points: points,
    attaches_to: attachesTo,
    needs_physics: [],
    result_types: [],
    report_regions: [],
    status: 'ready',
    awaiting: [],
  };
}

const TYPES = [
  entry('base', [{ name: 'spot', label: 'Spot', description: '', keys_from: 'places', default_key_from: 'home' }], [], { places: ['a', 'b'], home: 'a' }),
  entry('fixture', [], ['spot']),
];

function scenario(blocks: Scenario['blocks']): Scenario {
  return { format: 'dentalsim.scenario/1', name: 'T', conventions_version: '1.1.0', detail_level: 'standard', blocks, loads: [] } as unknown as Scenario;
}

const BASE = {
  instance_id: 'root',
  type_id: 'base',
  type_version: '1.0.0',
  attach_to: null,
  settings: { places: { kind: 'design', value: ['a', 'b', 'c'] } },
} as unknown as Scenario['blocks'][number];

describe('keyed sockets', () => {
  it('offers one socket per key, from the scenario value or the default', () => {
    expect(socketsFor(scenario([BASE]), TYPES[1]!, TYPES).map((s) => [s.socket, s.label])).toEqual([
      ['spot:a', 'root: Spot a'],
      ['spot:b', 'root: Spot b'],
      ['spot:c', 'root: Spot c'],
    ]);
    const plain = { ...BASE, settings: {} } as Scenario['blocks'][number];
    expect(socketsFor(scenario([plain]), TYPES[1]!, TYPES).map((s) => s.socket)).toEqual(['spot:a', 'spot:b']);
  });

  it('counts a block on the bare name as holding the default key', () => {
    const held = addBlock(addBlock(scenario([BASE]), TYPES[1]!, 'root', 'spot'), TYPES[1]!, 'root', 'spot:c');
    expect(socketsFor(held, TYPES[1]!, TYPES).map((s) => s.socket)).toEqual(['spot:b']);
  });
});
