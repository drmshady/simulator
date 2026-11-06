// Pure scenario editing for the builder (task T093). Nothing here knows a block type (SC-012): blocks are handled
// through their attach points and settings schemas from GET /api/blocks.

import type { BlockInstance, BlockTypeEntry, Placement, Scenario } from './api/types';

export const ZERO_PLACEMENT: Placement = {
  position_mm: [0, 0, 0],
  axis_angles_deg: { mesiodistal: 0, buccolingual: 0 },
  rotation_deg: 0,
};

export interface TreeRow {
  block: BlockInstance;
  depth: number;
}

/** The blocks root first, each parent before its children (siblings in scenario order), with their depth. */
export function blockTree(scenario: Scenario): TreeRow[] {
  const children = new Map<string | null, BlockInstance[]>();
  for (const block of scenario.blocks) {
    const parent = block.attach_to?.instance_id ?? null;
    children.set(parent, [...(children.get(parent) ?? []), block]);
  }
  const rows: TreeRow[] = [];
  const visit = (parent: string | null, depth: number) => {
    for (const block of children.get(parent) ?? []) {
      rows.push({ block, depth });
      visit(block.instance_id, depth + 1);
    }
  };
  visit(null, 0);
  return rows;
}

/** A block and every block attached below it. */
export function subtree(scenario: Scenario, instanceId: string): Set<string> {
  const found = new Set([instanceId]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const block of scenario.blocks) {
      const parent = block.attach_to?.instance_id;
      if (parent && found.has(parent) && !found.has(block.instance_id)) {
        found.add(block.instance_id);
        grew = true;
      }
    }
  }
  return found;
}

/** The scenario without a block, the blocks attached below it, and the contact points on any of them. */
export function removeBlock(scenario: Scenario, instanceId: string): Scenario {
  const gone = subtree(scenario, instanceId);
  const loads = (scenario.loads ?? [])
    .map((load) => ({ ...load, contact_points: (load.contact_points ?? []).filter((p) => !gone.has(p.block)) }))
    .filter((load) => load.pattern === 'chewing_cycle' || (load.contact_points ?? []).length > 0);
  return { ...scenario, blocks: scenario.blocks.filter((b) => !gone.has(b.instance_id)), loads };
}

/** Sockets a new block of `type` can plug into: each placed block's attach points that the type attaches to and
 * that still have room (a socket holds `capacity` blocks, one by default). */
export function socketsFor(
  scenario: Scenario,
  type: BlockTypeEntry,
  types: BlockTypeEntry[],
): { instanceId: string; socket: string; label: string }[] {
  const byId = new Map(types.map((t) => [t.type_id, t]));
  const held = new Map<string, number>();
  for (const block of scenario.blocks) {
    if (block.attach_to) {
      const key = `${block.attach_to.instance_id}|${block.attach_to.attach_point}`;
      held.set(key, (held.get(key) ?? 0) + 1);
    }
  }
  const found = [];
  for (const block of scenario.blocks) {
    const parent = byId.get(block.type_id);
    for (const point of parent?.attach_points ?? []) {
      const full = (held.get(`${block.instance_id}|${point.name}`) ?? 0) >= (point.capacity ?? 1);
      if (type.attaches_to.includes(point.name) && !full) {
        found.push({ instanceId: block.instance_id, socket: point.name, label: `${block.instance_id}: ${point.label}` });
      }
    }
  }
  return found;
}

/** A fresh instance id `<type>-<n>` not used yet. */
export function newInstanceId(scenario: Scenario, typeId: string): string {
  const used = new Set(scenario.blocks.map((b) => b.instance_id));
  for (let n = 1; ; n++) {
    const id = `${typeId.replace(/_/g, '-')}-${n}`;
    if (!used.has(id)) return id;
  }
}

/** The scenario with a new block of `type` on a socket; its library values are filled in when it is saved. */
export function addBlock(scenario: Scenario, type: BlockTypeEntry, parent: string, socket: string): Scenario {
  const block: BlockInstance = {
    instance_id: newInstanceId(scenario, type.type_id),
    type_id: type.type_id,
    type_version: type.version,
    attach_to: { instance_id: parent, attach_point: socket },
    placement: ZERO_PLACEMENT,
    settings: {},
  };
  return { ...scenario, blocks: [...scenario.blocks, block] };
}

export function updateBlock(scenario: Scenario, instanceId: string, change: (block: BlockInstance) => BlockInstance): Scenario {
  return { ...scenario, blocks: scenario.blocks.map((b) => (b.instance_id === instanceId ? change(b) : b)) };
}

/** Moves a block by `delta` mm along its socket's axes (the frame's axes for blocks on the jaw). */
export function moveBlock(scenario: Scenario, instanceId: string, delta: readonly [number, number, number]): Scenario {
  return updateBlock(scenario, instanceId, (block) => {
    const placement = block.placement ?? ZERO_PLACEMENT;
    const [x, y, z] = placement.position_mm ?? [0, 0, 0];
    const round = (v: number) => Math.round(v * 100) / 100;
    return { ...block, placement: { ...placement, position_mm: [round(x + delta[0]), round(y + delta[1]), round(z + delta[2])] } };
  });
}

/** Every block whose type has a newer version, set to it (the owner accepted the upgrade). */
export function upgradeBlocks(scenario: Scenario, upgrades: { instance_id: string; to_version: string }[]): Scenario {
  const to = new Map(upgrades.map((u) => [u.instance_id, u.to_version]));
  return { ...scenario, blocks: scenario.blocks.map((b) => (to.has(b.instance_id) ? { ...b, type_version: to.get(b.instance_id)! } : b)) };
}
