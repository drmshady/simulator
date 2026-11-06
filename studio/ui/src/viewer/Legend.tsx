import { Badge, Group, Paper, Stack, Text } from '@mantine/core';

import { useLibraryEntry } from '../api/studio';
import { continuousColour, FIELDS, type FieldName } from './colourScale';
import { FROST_SCALE } from './colourScales.generated';

function swatch(colour: string) {
  return <span style={{ display: 'inline-block', width: 14, height: 14, borderRadius: 3, background: colour }} />;
}

function rgbCss([r, g, b]: readonly [number, number, number]): string {
  return `rgb(${Math.round(r * 255)} ${Math.round(g * 255)} ${Math.round(b * 255)})`;
}

/** One Frost window edge, with its approved value and source (an approved library entry, conventions). */
function FrostEdge({ parameterKey }: { parameterKey: string }) {
  const entry = useLibraryEntry(parameterKey);
  const current = entry.data?.versions.find((v) => v.version === entry.data?.entry.current_version);
  if (!current) return <Text size="xs">{parameterKey}</Text>;
  const sources = entry.data?.sources.map((s) => `${s.authors[0]?.split(' ')[0] ?? ''} ${s.year}`).join('; ');
  return (
    <Text size="xs">
      {current.typical.toLocaleString()} µε ({parameterKey} v{current.version}; {sources})
    </Text>
  );
}

/** The Frost legend, labelled "teaching reference" with the source of each window edge (conventions). */
export function FrostLegend() {
  return (
    <Paper withBorder p="sm" radius="sm">
      <Stack gap={6}>
        <Group gap="xs">
          <Text size="sm" fw={600}>
            Bone strain, Frost windows
          </Text>
          <Badge size="sm" variant="light">
            {FROST_SCALE.label}
          </Badge>
        </Group>
        {FROST_SCALE.categories.map((category, index) => (
          <Stack key={category.id} gap={0}>
            <Group gap="xs">
              {swatch(category.colour)}
              <Text size="sm">{category.label}</Text>
            </Group>
            {index < FROST_SCALE.edges.length && <FrostEdge parameterKey={FROST_SCALE.edges[index]!} />}
          </Stack>
        ))}
        <Text size="xs" c="dimmed">
          Grey: not bone. Colours are for display; the reported values come from integration points.
        </Text>
      </Stack>
    </Paper>
  );
}

/** A continuous legend: the shared range of the field in its unit. */
export function ContinuousLegend({ field, range }: { field: FieldName; range: readonly [number, number] }) {
  const info = FIELDS[field];
  const gradient = Array.from({ length: 9 }, (_, i) => rgbCss(continuousColour(i / 8))).join(', ');
  return (
    <Paper withBorder p="sm" radius="sm">
      <Stack gap={6}>
        <Text size="sm" fw={600}>
          {info.label}
        </Text>
        <div style={{ height: 12, borderRadius: 3, background: `linear-gradient(to right, ${gradient})` }} />
        <Group justify="space-between">
          <Text size="xs">
            {range[0].toLocaleString(undefined, { maximumFractionDigits: 1 })} {info.unit}
          </Text>
          <Text size="xs">
            {range[1].toLocaleString(undefined, { maximumFractionDigits: 1 })} {info.unit} and above
          </Text>
        </Group>
        <Text size="xs" c="dimmed">
          One range for every part shown. Colours are for display; the reported values come from integration points.
        </Text>
      </Stack>
    </Paper>
  );
}
