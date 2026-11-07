// Studies (Story 4, task T126): the settings a study can vary, the values the owner types, and how runs and spreads
// are shown. Nothing here knows a block type: the settings come from the scenario and the block settings schemas.

import type { BlockTypeEntry, Scenario, SettingSchema, Spread, StudyView } from './api/types';

/** A setting a study can vary, by its path (the same paths as the diff and the search). */
export interface SettingOption {
  value: string;
  label: string;
  group: string;
  kind: 'design' | 'library';
  type: 'number' | 'integer' | 'string' | 'boolean';
  unit?: string;
  choices?: string[];
  current: unknown;
  /** Only a load's force or direction: the runs reuse one solve (research R4). */
  loadOnly: boolean;
}

function unitOf(schema: SettingSchema): string | undefined {
  return schema.unit && schema.unit !== '1' ? schema.unit : undefined;
}

/** Every block setting that takes one value, and each load case's force and direction. */
export function settingOptions(scenario: Scenario, types: readonly BlockTypeEntry[]): SettingOption[] {
  const options: SettingOption[] = [];
  for (const block of scenario.blocks) {
    const type = types.find((t) => t.type_id === block.type_id && t.version === block.type_version) ??
      types.find((t) => t.type_id === block.type_id);
    if (!type) continue;
    for (const [id, schema] of Object.entries(type.settings_schema.properties)) {
      if (schema.type === 'array') continue;
      const given = block.settings?.[id];
      options.push({
        value: `${block.instance_id}.${id}`,
        label: `${block.instance_id}: ${schema.title}`,
        group: block.instance_id,
        kind: schema.kind,
        type: schema.kind === 'library' ? 'number' : schema.type,
        unit: unitOf(schema),
        choices: schema.enum,
        current: given ? given.value : schema.default,
        loadOnly: false,
      });
    }
  }
  for (const load of scenario.loads ?? []) {
    const name = load.name ?? load.id;
    const group = `load ${load.id}`;
    options.push({
      value: `load:${load.id}.force`, label: `${name}: force`, group, kind: 'library', type: 'number', unit: 'N',
      current: load.force.value, loadOnly: true,
    });
    if (load.direction) {
      options.push(
        { value: `load:${load.id}.direction.angle_deg`, label: `${name}: angle from the axis`, group, kind: 'design',
          type: 'number', unit: '°', current: load.direction.angle_deg, loadOnly: true },
        { value: `load:${load.id}.direction.azimuth_deg`, label: `${name}: azimuth`, group, kind: 'design',
          type: 'number', unit: '°', current: load.direction.azimuth_deg, loadOnly: true },
      );
    }
  }
  return options;
}

export interface ParsedValues {
  values: (number | string | boolean)[];
  error: string | null;
}

/** The values typed for an axis, separated by commas, semicolons or spaces (`8, 10, 11.5`; `D1 D2 D3`). */
export function parseValues(text: string, option: SettingOption): ParsedValues {
  const words = text.split(/[\s,;]+/).map((w) => w.trim()).filter(Boolean);
  if (!words.length) return { values: [], error: 'Type at least one value.' };
  const values: (number | string | boolean)[] = [];
  for (const word of words) {
    if (option.type === 'number' || option.type === 'integer') {
      const number = Number(word);
      if (!Number.isFinite(number)) return { values: [], error: `"${word}" is not a number.` };
      if (option.type === 'integer' && !Number.isInteger(number)) return { values: [], error: `"${word}" is not a whole number.` };
      values.push(number);
    } else if (option.type === 'boolean') {
      if (!['yes', 'no', 'true', 'false'].includes(word.toLowerCase())) return { values: [], error: `"${word}" is not yes or no.` };
      values.push(['yes', 'true'].includes(word.toLowerCase()));
    } else {
      if (option.choices && !option.choices.includes(word)) {
        return { values: [], error: `"${word}" is not one of ${option.choices.join(', ')}.` };
      }
      values.push(word);
    }
  }
  if (new Set(values.map(String)).size !== values.length) return { values: [], error: 'A value is listed twice.' };
  return { values, error: null };
}

/** How many runs the axes make: the product of their value counts. */
export function runCount(axes: readonly { values: readonly unknown[] }[]): number {
  return axes.length ? axes.reduce((n, axis) => n * axis.values.length, 1) : 0;
}

export function formatAxisValue(value: unknown): string {
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : String(Number(value.toPrecision(6)));
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  return String(value);
}

/** A key number's spread as one line: `min–max unit (range, % change)`. */
export function formatSpread(spread: Spread): string {
  const digits = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : Number(v.toPrecision(3)).toString());
  const percent = spread.percent_change === null ? '' : `, ${spread.percent_change.toFixed(1)}%`;
  return `${digits(spread.min)}–${digits(spread.max)} ${spread.unit} (range ${digits(spread.range)}${percent})`;
}

export const STATUS_COLOURS: Record<string, string> = {
  queued: 'gray',
  running: 'blue',
  interrupted: 'orange',
  succeeded: 'green',
  finished: 'green',
  failed: 'red',
  finished_with_failures: 'orange',
  cancelled: 'gray',
};

export function statusLabel(status: string): string {
  return status.replace(/_/g, ' ');
}

/** The study is still to finish: it can be cancelled, and its page refreshes. */
export function isActive(study: Pick<StudyView, 'status'>): boolean {
  return study.status === 'queued' || study.status === 'running';
}

/** The library path of a library setting field, for the sensitivity button. */
export function settingPath(instanceId: string, settingId: string): string {
  return `${instanceId}.${settingId}`;
}
