import { ActionIcon, Button, Card, Group, NumberInput, Select, Stack, Text, TextInput } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { apiGet } from '../api/client';
import type { ContactPoint, LibraryValue, LoadCase, NamedPoint } from '../api/types';
import { evenShares, sharesOk, SIGN_NOTE } from '../display';

export interface LoadEditorProps {
  loads: LoadCase[];
  points: NamedPoint[];
  onChange: (loads: LoadCase[]) => void;
}

interface EntrySummary {
  id: string;
  parameter_key: string;
  label: string;
  current_version: number | null;
}

interface EntryVersion {
  version: number;
  typical: number;
  units: string;
}

const PATTERNS = [
  { value: 'single_bite', label: 'Single bite' },
  { value: 'clenching', label: 'Clenching (also bruxism)' },
  { value: 'chewing_cycle', label: 'Chewing cycle' },
];

function useLoadEntries() {
  return useQuery({
    queryKey: ['library-entries', 'load'],
    queryFn: ({ signal }) => apiGet<EntrySummary[]>('/api/library/entries?category=load&status=approved', { signal }),
    staleTime: 60_000,
  });
}

async function typicalValue(entry: EntrySummary): Promise<LibraryValue> {
  const detail = await apiGet<{ versions: EntryVersion[] }>(`/api/library/entries/${entry.id}`);
  const version = detail.versions.find((v) => v.version === entry.current_version);
  if (!version) throw new Error(`${entry.parameter_key} has no approved version.`);
  return { kind: 'library', entry_id: entry.id, entry_version: version.version, choice: 'typical', value: version.typical, outside_range: false };
}

function ContactPoints({
  points,
  available,
  onChange,
}: {
  points: ContactPoint[];
  available: NamedPoint[];
  onChange: (points: ContactPoint[]) => void;
}) {
  const options = available.map((p) => ({ value: `${p.block}|${p.point}`, label: `${p.block}: ${p.label}` }));
  return (
    <Stack gap={4}>
      {points.map((point, index) => (
        <Group key={`${point.block}-${point.point}-${index}`} gap="xs" wrap="nowrap">
          <Select
            size="xs"
            style={{ flex: 1 }}
            aria-label="Contact point"
            data={options}
            value={`${point.block}|${point.point}`}
            onChange={(next) => {
              if (!next) return;
              const [block, name] = next.split('|');
              onChange(points.map((p, i) => (i === index ? { ...p, block: block!, point: name! } : p)));
            }}
          />
          <NumberInput
            size="xs"
            w={90}
            aria-label="Share of the force"
            min={0.01}
            max={1}
            step={0.05}
            decimalScale={3}
            value={point.share}
            onChange={(next) =>
              typeof next === 'number' && onChange(points.map((p, i) => (i === index ? { ...p, share: next } : p)))
            }
          />
          <ActionIcon
            variant="subtle"
            color="red"
            aria-label="Remove contact point"
            onClick={() => onChange(evenShares(points.filter((_, i) => i !== index)))}
          >
            ×
          </ActionIcon>
        </Group>
      ))}
      <Group gap="xs">
        <Button
          size="compact-xs"
          variant="light"
          disabled={!available.length}
          onClick={() => {
            const first = available[0]!;
            onChange(evenShares([...points, { block: first.block, point: first.point, share: 1 }]));
          }}
        >
          Add contact point
        </Button>
        {!sharesOk(points) && (
          <Text size="xs" c="red">
            The shares must add up to 1.
          </Text>
        )}
      </Group>
    </Stack>
  );
}

/** The load cases (FR-008): pattern, force from the library, angle and azimuth with the conventions sign, and
 * contact points picked from the blocks' named occlusal points, with shares. */
export function LoadEditor({ loads, points, onChange }: LoadEditorProps) {
  const entries = useLoadEntries();
  const forceOptions = (entries.data ?? [])
    .filter((e) => e.parameter_key.startsWith('load.bite_force') || e.parameter_key === 'load.clenching_force')
    .map((e) => ({ value: e.id, label: `${e.label} (${e.parameter_key})` }));

  const update = (index: number, change: Partial<LoadCase>) =>
    onChange(loads.map((load, i) => (i === index ? ({ ...load, ...change } as LoadCase) : load)));

  const addLoad = async () => {
    const entry = entries.data?.find((e) => e.parameter_key.startsWith('load.bite_force'));
    if (!entry || !points.length) return;
    const force = await typicalValue(entry);
    const first = points[0]!;
    const id = `load-${loads.length + 1}`;
    onChange([
      ...loads,
      {
        id,
        name: null,
        pattern: 'single_bite',
        force,
        direction: { angle_deg: 0, azimuth_deg: 90 },
        contact_points: [{ block: first.block, point: first.point, share: 1 }],
        instants: [],
        cycles: null,
      },
    ]);
  };

  return (
    <Stack gap="sm">
      <Text size="xs" c="dimmed">
        {SIGN_NOTE}
      </Text>
      {loads.map((load, index) => (
        <Card key={load.id} withBorder padding="sm" radius="sm">
          <Stack gap="xs">
            <Group gap="xs" grow>
              <TextInput size="xs" label="Name" value={load.name ?? load.id} onChange={(e) => update(index, { name: e.currentTarget.value })} />
              <Select
                size="xs"
                label="Pattern"
                data={PATTERNS.filter((p) => p.value !== 'chewing_cycle' || load.pattern === 'chewing_cycle')}
                value={load.pattern}
                allowDeselect={false}
                onChange={(next) => next && update(index, { pattern: next as LoadCase['pattern'] })}
              />
            </Group>
            <Select
              size="xs"
              label="Force (from the library; its typical value)"
              data={forceOptions}
              value={load.force.entry_id}
              allowDeselect={false}
              onChange={async (next) => {
                const entry = entries.data?.find((e) => e.id === next);
                if (entry) update(index, { force: await typicalValue(entry) });
              }}
            />
            <Text size="xs">
              {load.force.value.toLocaleString()} N ({load.force.choice})
            </Text>
            {load.direction && (
              <Group gap="xs" grow>
                <NumberInput
                  size="xs"
                  label="Angle (°)"
                  min={0}
                  max={90}
                  value={load.direction.angle_deg}
                  onChange={(next) =>
                    typeof next === 'number' && update(index, { direction: { ...load.direction!, angle_deg: next } })
                  }
                />
                <NumberInput
                  size="xs"
                  label="Azimuth (°)"
                  min={0}
                  max={359.9}
                  value={load.direction.azimuth_deg}
                  onChange={(next) =>
                    typeof next === 'number' && update(index, { direction: { ...load.direction!, azimuth_deg: next } })
                  }
                />
              </Group>
            )}
            {load.pattern !== 'chewing_cycle' ? (
              <ContactPoints
                points={load.contact_points ?? []}
                available={points}
                onChange={(next) => update(index, { contact_points: next })}
              />
            ) : (
              <Text size="xs" c="dimmed">
                A chewing cycle has {load.instants?.length ?? 0} instant(s), each with its own contact points and
                direction; its key numbers are those of the worst instant.
              </Text>
            )}
            <Group justify="flex-end">
              <Button size="compact-xs" color="red" variant="subtle" onClick={() => onChange(loads.filter((_, i) => i !== index))}>
                Remove load case
              </Button>
            </Group>
          </Stack>
        </Card>
      ))}
      <Button size="xs" variant="light" onClick={() => void addLoad()} disabled={!points.length || !entries.data}>
        Add a bite
      </Button>
    </Stack>
  );
}
