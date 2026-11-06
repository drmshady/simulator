import { Badge, Group, NumberInput, Select, Stack, Text, Tooltip } from '@mantine/core';

import { useLibraryEntry } from '../api/studio';
import type { LibraryValue, LibraryValueView, SettingSchema } from '../api/types';

export interface LibraryValueFieldProps {
  setting: SettingSchema;
  value: LibraryValue | undefined;
  /** The server's view of the value: its parameter key, range and whether it lies outside it. */
  view: LibraryValueView | undefined;
  onChange: (value: LibraryValue) => void;
}

/** A library setting (FR-015, FR-052): the approved range, the typical value (preselected), every source with its
 * value, and the choice of typical, a source's value or a custom value. A value outside the published range is
 * marked: it blocks publishing. */
export function LibraryValueField({ setting, value, view, onChange }: LibraryValueFieldProps) {
  const entry = useLibraryEntry(value?.entry_id ?? null);
  const version = entry.data?.versions.find((v) => v.version === value?.entry_version);
  const sources = (version?.values_by_source ?? []).map((item) => {
    const source = entry.data?.sources.find((s) => s.id === item.source_id);
    const who = source ? `${source.authors[0]?.split(' ')[0] ?? ''} ${source.year}` : item.source_id;
    return { id: item.source_id, label: `${who}: ${item.value.toLocaleString()}`, value: item.value };
  });
  const unit = setting.unit && setting.unit !== '1' ? setting.unit : '';
  const outside = view?.outside_range ?? value?.outside_range ?? false;

  const choose = (choice: string | null) => {
    if (!value || !version || !choice) return;
    if (choice === 'typical') onChange({ ...value, choice, value: version.typical, outside_range: false });
    else if (choice === 'custom') onChange({ ...value, choice });
    else {
      const source = sources.find((s) => `source:${s.id}` === choice);
      if (source) onChange({ ...value, choice, value: source.value, outside_range: false });
    }
  };

  return (
    <Stack gap={4}>
      <Group gap="xs" justify="space-between" wrap="nowrap">
        <Text size="sm" fw={500}>
          {setting.title}
        </Text>
        {outside && (
          <Tooltip label="Outside the published range of the approved entry. A run with it cannot be published.">
            <Badge color="red" size="sm" variant="filled">
              outside published range
            </Badge>
          </Tooltip>
        )}
      </Group>
      <Select
        size="xs"
        aria-label={`${setting.title}: how the value is chosen`}
        value={value?.choice ?? 'typical'}
        onChange={choose}
        data={[
          { value: 'typical', label: version ? `Typical: ${version.typical.toLocaleString()} ${unit}` : 'Typical' },
          ...sources.map((s) => ({ value: `source:${s.id}`, label: `${s.label} ${unit}` })),
          { value: 'custom', label: 'Custom value' },
        ]}
        allowDeselect={false}
      />
      {value?.choice === 'custom' && (
        <NumberInput
          size="xs"
          aria-label={`${setting.title}: custom value`}
          value={value.value}
          suffix={unit ? ` ${unit}` : undefined}
          onChange={(next) => typeof next === 'number' && onChange({ ...value, value: next })}
        />
      )}
      <Text size="xs" c="dimmed">
        {view?.parameter_key ?? setting.parameter_key}
        {version && ` v${version.version}: range ${version.range_min.toLocaleString()}–${version.range_max.toLocaleString()} ${unit}`}
        {value && value.choice !== 'typical' && ` · now ${value.value.toLocaleString()} ${unit}`}
      </Text>
      {view?.error && (
        <Text size="xs" c="red">
          {view.error}
        </Text>
      )}
    </Stack>
  );
}
