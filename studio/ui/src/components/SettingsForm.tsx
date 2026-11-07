import { Accordion, Box, NumberInput, Select, Stack, Switch, TagsInput, Text, TextInput } from '@mantine/core';
import type { ReactNode } from 'react';

import type {
  BlockTypeEntry,
  DesignValue,
  Difference,
  LibraryValue,
  LibraryValueView,
  SettingSchema,
  SettingValue,
} from '../api/types';
import { designValueOf, groupedSettings, limitProblem } from '../display';
import { formatValue } from '../organise';
import { settingPath } from '../study';
import { LibraryValueField } from './LibraryValueField';

export interface SettingsFormProps {
  block: BlockTypeEntry;
  instanceId: string;
  settings: Record<string, SettingValue>;
  library: LibraryValueView[];
  onChange: (settingId: string, value: SettingValue) => void;
  /** Settings that differ from the scenario this one was duplicated from, by setting id (FR-032). */
  changes?: ReadonlyMap<string, Difference>;
  /** A library setting's sensitivity button, with the setting's path (FR-021). */
  onSensitivity?: (path: string) => void;
}

/** A setting changed from the original: marked at its edge, with the original's value. */
function Changed({ difference, children }: { difference: Difference | undefined; children: ReactNode }) {
  if (!difference) return children;
  return (
    <Box pl="xs" style={{ borderLeft: '3px solid var(--mantine-color-orange-6)' }}>
      {children}
      <Text size="xs" c="orange.8">
        Changed from the original: {difference.change === 'added' ? 'not set there' : formatValue(difference.before)}
      </Text>
    </Box>
  );
}

function label(text: string, unit?: string): string {
  return unit && unit !== '1' ? `${text} (${unit})` : text;
}

function DesignField({
  id,
  schema,
  value,
  onChange,
}: {
  id: string;
  schema: SettingSchema;
  value: unknown;
  onChange: (value: DesignValue['value']) => void;
}) {
  const problem = limitProblem(schema, value);
  const common = { label: label(schema.title, schema.unit), description: schema.description, error: problem, size: 'xs' as const };
  if (schema.enum) {
    return (
      <Select
        {...common}
        data={schema.enum}
        value={String(value ?? '')}
        onChange={(next) => next !== null && onChange(next)}
        allowDeselect={false}
      />
    );
  }
  if (schema.type === 'boolean') {
    return <Switch label={schema.title} checked={Boolean(value)} onChange={(e) => onChange(e.currentTarget.checked)} />;
  }
  if (schema.type === 'number' || schema.type === 'integer') {
    return (
      <NumberInput
        {...common}
        value={typeof value === 'number' ? value : ''}
        min={schema.minimum}
        max={schema.maximum}
        step={schema.type === 'integer' ? 1 : 0.1}
        decimalScale={schema.type === 'integer' ? 0 : 3}
        allowDecimal={schema.type !== 'integer'}
        onChange={(next) => typeof next === 'number' && onChange(next)}
      />
    );
  }
  if (schema.type === 'array') {
    const items = Array.isArray(value) ? value.map(String) : [];
    return (
      <TagsInput
        {...common}
        value={items}
        onChange={(next) =>
          onChange(schema.items?.type === 'number' ? next.map(Number).filter(Number.isFinite) : next)
        }
        aria-label={id}
      />
    );
  }
  return <TextInput {...common} value={String(value ?? '')} onChange={(e) => onChange(e.currentTarget.value)} />;
}

/** A block's settings in its five groups, with design limits shown and enforced, and library settings as
 * library-value fields. Nothing here knows a block type: the form comes from the block's settings schema. */
export function SettingsForm({ block, instanceId, settings, library, onChange, changes, onSensitivity }: SettingsFormProps) {
  const groups = groupedSettings(block);
  return (
    <Accordion multiple defaultValue={groups.map((g) => g.id)} variant="separated">
      {groups.map((group) => (
        <Accordion.Item key={group.id} value={group.id}>
          <Accordion.Control>
            <Text size="sm" fw={600}>
              {group.label}
            </Text>
          </Accordion.Control>
          <Accordion.Panel>
            <Stack gap="sm">
              {group.settings.map(([id, schema]) => (
                <Changed key={id} difference={changes?.get(id)}>
                  {schema.kind === 'library' ? (
                    <LibraryValueField
                      setting={schema}
                      value={settings[id]?.kind === 'library' ? (settings[id] as LibraryValue) : undefined}
                      view={library.find((v) => v.path === `${instanceId}.${id}`)}
                      onChange={(value) => onChange(id, value)}
                      onSensitivity={onSensitivity && (() => onSensitivity(settingPath(instanceId, id)))}
                    />
                  ) : (
                    <DesignField
                      id={id}
                      schema={schema}
                      value={designValueOf(schema, settings[id])}
                      onChange={(value) => onChange(id, { kind: 'design', value })}
                    />
                  )}
                </Changed>
              ))}
            </Stack>
          </Accordion.Panel>
        </Accordion.Item>
      ))}
    </Accordion>
  );
}
