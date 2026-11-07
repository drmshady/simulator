// Helpers for Story 3 (tasks T118-T120): tags, settings differences, key-number differences, the runs a comparison
// shows and the camera all its panels share. Kept apart from the components so that each component file exports
// components only.

import type { Delta, Difference } from './api/types';

/** Tags trimmed, empty ones dropped and each kept once, ignoring case (as the server stores them). */
export function cleanTags(tags: readonly string[]): string[] {
  const seen = new Set<string>();
  const cleaned: string[] = [];
  for (const tag of tags) {
    const text = tag.split(/\s+/).filter(Boolean).join(' ');
    if (text && !seen.has(text.toLowerCase())) {
      seen.add(text.toLowerCase());
      cleaned.push(text);
    }
  }
  return cleaned;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function number(value: number): string {
  return value.toLocaleString(undefined, { maximumFractionDigits: 3 });
}

/** A setting value as the diff gives it: a number, text, list, library value or a whole block or load case. */
export function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '–';
  if (typeof value === 'number') return number(value);
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return `(${value.map(formatValue).join(', ')})`;
  if (isRecord(value)) {
    if (typeof value.value === 'number' && 'entry_version' in value) {
      return `${number(value.value)} (library v${String(value.entry_version)}, ${String(value.choice)})`;
    }
    if ('type_id' in value) return `${String(value.type_id)} ${String(value.type_version ?? '')}`.trim();
    if ('pattern' in value) return String(value.pattern).replace(/_/g, ' ');
    if ('instance_id' in value) return `${String(value.instance_id)}${value.attach_point ? ` at ${String(value.attach_point)}` : ''}`;
  }
  return JSON.stringify(value);
}

/** "implant-1.length_mm: 10 → 8", "cap-1 added (cap 1.0.0)", "load:oblique removed". */
export function describeDifference(d: Difference): string {
  if (d.change === 'added') return `${d.path} added${d.group === 'block' || d.group === 'load' ? ` (${formatValue(d.after)})` : `: ${formatValue(d.after)}`}`;
  if (d.change === 'removed') return `${d.path} removed${d.group === 'block' || d.group === 'load' ? '' : ` (was ${formatValue(d.before)})`}`;
  return `${d.path}: ${formatValue(d.before)} → ${formatValue(d.after)}`;
}

/** A block's changed settings by setting id (`<block>.<setting>` paths in the settings group). */
export function settingChanges(differences: readonly Difference[], instanceId: string): Map<string, Difference> {
  const prefix = `${instanceId}.`;
  return new Map(
    differences
      .filter((d) => d.group === 'setting' && d.block === instanceId && d.path.startsWith(prefix))
      .map((d) => [d.path.slice(prefix.length), d]),
  );
}

/** The blocks with any difference: a setting, their placement or type, or added. */
export function changedBlocks(differences: readonly Difference[]): Set<string> {
  return new Set(differences.map((d) => d.block).filter((b): b is string => Boolean(b)));
}

/** "+300 µε (+30.0%)"; "–" when either run lacks the number. */
export function formatDelta(delta: Delta | null | undefined, unit: string): string {
  if (!delta) return '–';
  const sign = (x: number) => (x > 0 ? '+' : x < 0 ? '−' : '±');
  const absolute = `${sign(delta.absolute)}${Math.abs(delta.absolute).toLocaleString(undefined, { maximumFractionDigits: 1 })} ${unit}`;
  if (delta.relative === null || delta.relative === undefined) return absolute;
  return `${absolute} (${sign(delta.relative)}${Math.abs(100 * delta.relative).toFixed(1)}%)`;
}

/** The run ids of `?runs=a,b,c`, each once, in order. */
export function parseRunIds(param: string | null): string[] {
  return [...new Set((param ?? '').split(',').map((id) => id.trim()).filter(Boolean))];
}

export type Vec3 = [number, number, number];

export interface CameraPose {
  position: Vec3;
  target: Vec3;
}

/** The view direction of the studio's 3D views (Scene's default camera), from the target toward the camera. */
const VIEW_DIRECTION: Vec3 = [40, -60, 30];

/** One framing for every panel of a comparison: looking at the centre of the box around every run, from far enough
 * away for the whole box to fit (the views' 35° field of view). */
export function framing(bounds: readonly [readonly number[], readonly number[]]): CameraPose {
  const [low, high] = bounds;
  const target: Vec3 = [0, 1, 2].map((i) => ((low[i] ?? 0) + (high[i] ?? 0)) / 2) as Vec3;
  const size = Math.hypot(...[0, 1, 2].map((i) => (high[i] ?? 0) - (low[i] ?? 0)));
  const distance = Math.max(10, 1.7 * size);
  const length = Math.hypot(...VIEW_DIRECTION);
  const position = VIEW_DIRECTION.map((d, i) => target[i]! + (d / length) * distance) as Vec3;
  return { position, target };
}
