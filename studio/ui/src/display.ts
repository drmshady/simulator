// Display helpers for slice 1a: durations, key-number grouping, load shares and setting limits. Kept apart from the
// components so that each component file exports components only.

import type { BlockTypeEntry, ContactPoint, KeyNumber, SettingSchema, SettingValue } from './api/types';

/** "about 4 min", "about 1.5 h" */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds)) return '–';
  if (seconds < 90) return `about ${Math.max(1, Math.round(seconds))} s`;
  if (seconds < 5400) return `about ${Math.round(seconds / 60)} min`;
  return `about ${(seconds / 3600).toFixed(1)} h`;
}

/** The screw label the conventions require on every report of retention-screw stress ("Analysis assumptions"). */
export const SCREW_NOTE = 'excludes screw preload';

/** Key numbers of one load case, paired as mean and 95th percentile by result. */
export function paired(numbers: KeyNumber[]): { key: string; mean?: KeyNumber; p95?: KeyNumber }[] {
  const rows = new Map<string, { key: string; mean?: KeyNumber; p95?: KeyNumber }>();
  for (const n of numbers) {
    const key = n.id.replace(/\.(mean|p95)$/, '');
    const row = rows.get(key) ?? { key };
    row[n.statistic] = n;
    rows.set(key, row);
  }
  return [...rows.values()];
}

/** The key numbers worth a glance while building: crestal strain and component stress, not displacement. */
export function headlineNumbers(numbers: KeyNumber[]): KeyNumber[] {
  return numbers.filter((n) => n.unit !== 'µm');
}

/** The conventions' sign rule, shown beside the angle (conventions "Load direction and the load-angle sign"). */
export const SIGN_NOTE =
  'Angle from the axial bite (0° pushes straight toward apical). The azimuth is the side the load comes from, ' +
  'from mesial (0°) toward buccal (90°); a positive angle tilts the force away from that side, so 90° and +30° ' +
  'tilt it toward lingual.';

/** Whether a load's contact-point shares add up to 1 (the scenario format refuses it otherwise). */
export function sharesOk(points: ContactPoint[]): boolean {
  return points.length > 0 && Math.abs(points.reduce((sum, p) => sum + p.share, 0) - 1) < 1e-9;
}

/** Shares split evenly over the points, the last one taking the rounding. */
export function evenShares(points: ContactPoint[]): ContactPoint[] {
  if (!points.length) return points;
  const share = Math.round((1 / points.length) * 1e6) / 1e6;
  return points.map((p, i) => ({ ...p, share: i === points.length - 1 ? 1 - share * (points.length - 1) : share }));
}

/** The design value of a setting: the given one, or the block's default. */
export function designValueOf(schema: SettingSchema, given: SettingValue | undefined): unknown {
  return given && given.kind === 'design' ? given.value : schema.default;
}

/** Why a design value breaks the block's limits, or null when it is within them (FR-052). */
export function limitProblem(schema: SettingSchema, value: unknown): string | null {
  if (schema.type === 'number' || schema.type === 'integer') {
    if (typeof value !== 'number' || !Number.isFinite(value)) return 'Enter a number.';
    const unit = schema.unit && schema.unit !== '1' ? ` ${schema.unit}` : '';
    if (schema.minimum !== undefined && value < schema.minimum) return `At least ${schema.minimum}${unit}.`;
    if (schema.maximum !== undefined && value > schema.maximum) return `At most ${schema.maximum}${unit}.`;
  }
  if (schema.enum && !schema.enum.includes(String(value))) return `Choose one of ${schema.enum.join(', ')}.`;
  if (schema.type === 'array' && Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) return `At least ${schema.minItems} item(s).`;
    if (schema.maxItems !== undefined && value.length > schema.maxItems) return `At most ${schema.maxItems} item(s).`;
  }
  return null;
}

/** The settings of a block grouped as the schema's groups are, in their order (FR-003); groups with no setting of
 * this block are left out. */
export function groupedSettings(
  block: BlockTypeEntry,
): { id: string; label: string; settings: [string, SettingSchema][] }[] {
  const entries = Object.entries(block.settings_schema.properties);
  return block.settings_schema.groups
    .map((group) => ({ ...group, settings: entries.filter(([, schema]) => schema.group === group.id) }))
    .filter((group) => group.settings.length > 0);
}
