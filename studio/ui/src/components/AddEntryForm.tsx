import {
  Alert,
  Button,
  Card,
  Group,
  List,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Text,
  Textarea,
  TextInput,
} from '@mantine/core';
import { useState } from 'react';

import { isStudioError } from '../api/client';
import { addOwnerEntry } from '../api/studio';
import type { ImportedVersion, LibraryCategory, ProposalSource } from '../api/types';
import {
  CATEGORIES,
  emptyDraft,
  emptySource,
  ownerEntryBody,
  STUDY_TYPES,
  type EntryDraft,
  type SourceDraft,
} from '../library';

function numberOrBlank(value: string | number): number | '' {
  return typeof value === 'number' ? value : '';
}

function SourceFields({
  index,
  source,
  units,
  onChange,
  onRemove,
}: {
  index: number;
  source: SourceDraft;
  units: string;
  onChange: (next: SourceDraft) => void;
  onRemove: (() => void) | null;
}) {
  const set = <K extends keyof SourceDraft>(key: K, value: SourceDraft[K]) => onChange({ ...source, [key]: value });
  return (
    <Card withBorder padding="sm">
      <Stack gap="xs">
        <Group justify="space-between">
          <Text fw={600} size="sm">
            Source {index + 1}
          </Text>
          {onRemove && (
            <Button size="compact-xs" variant="subtle" color="red" onClick={onRemove}>
              Remove
            </Button>
          )}
        </Group>
        <TextInput
          label="Authors"
          description="First author first, as 'Surname AB'; separate authors with ';' (or ',' as in 'Rho JY, Ashman RB')"
          value={source.authors}
          onChange={(e) => set('authors', e.currentTarget.value)}
          required
        />
        <TextInput label="Title" value={source.title} onChange={(e) => set('title', e.currentTarget.value)} required />
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          <NumberInput label="Year" value={source.year} onChange={(v) => set('year', numberOrBlank(v))} min={1800} max={2100} required />
          <TextInput label="Journal" value={source.journal} onChange={(e) => set('journal', e.currentTarget.value)} />
          <Select
            label="Study type"
            data={STUDY_TYPES}
            value={source.study_type}
            onChange={(v) => v && set('study_type', v as ProposalSource['study_type'])}
            allowDeselect={false}
          />
        </SimpleGrid>
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <TextInput label="DOI" placeholder="10.xxxx/…" value={source.doi} onChange={(e) => set('doi', e.currentTarget.value)} />
          <TextInput label="PMID" value={source.pmid} onChange={(e) => set('pmid', e.currentTarget.value)} />
        </SimpleGrid>
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <NumberInput
            label="Value this paper reports"
            value={source.value}
            onChange={(v) => set('value', numberOrBlank(v))}
            suffix={units && units !== '1' ? ` ${units}` : undefined}
            required
          />
          <TextInput
            label="Note"
            placeholder="Where in the paper, and how the value was taken"
            value={source.note}
            onChange={(e) => set('note', e.currentTarget.value)}
          />
        </SimpleGrid>
      </Stack>
    </Card>
  );
}

/** The owner enters a paper and its values (FR-053). The entry is saved as `proposed`; it follows the same source
 * check and approval as an imported one, and a parameter key that exists gets a new version. */
export function AddEntryForm({ onSaved, onCancel }: { onSaved: (result: ImportedVersion) => void; onCancel: () => void }) {
  const [draft, setDraft] = useState<EntryDraft>(emptyDraft);
  const [problems, setProblems] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const set = <K extends keyof EntryDraft>(key: K, value: EntryDraft[K]) => setDraft((d) => ({ ...d, [key]: value }));
  const setSource = (index: number, next: SourceDraft) =>
    setDraft((d) => ({ ...d, sources: d.sources.map((s, i) => (i === index ? next : s)) }));

  const save = async () => {
    const { body, problems: found } = ownerEntryBody(draft);
    setProblems(found);
    if (!body) return;
    setSaving(true);
    try {
      onSaved(await addOwnerEntry(body));
    } catch (e) {
      setProblems(isStudioError(e) ? [e.message, ...e.problems.map((p) => p.message)] : [String(e)]);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Stack gap="sm">
      <SimpleGrid cols={{ base: 1, sm: 2 }}>
        <TextInput
          label="Parameter key"
          placeholder="bone.cancellous.E.D4"
          value={draft.parameter_key}
          onChange={(e) => set('parameter_key', e.currentTarget.value)}
          required
        />
        <Select
          label="Category"
          data={CATEGORIES.map((c) => ({ value: c, label: c.replace('_', ' ') }))}
          value={draft.category}
          onChange={(v) => v && set('category', v as LibraryCategory)}
          allowDeselect={false}
        />
      </SimpleGrid>
      <TextInput label="Label" placeholder="Cancellous bone elastic modulus, density class D4" value={draft.label} onChange={(e) => set('label', e.currentTarget.value)} />
      <SimpleGrid cols={{ base: 2, sm: 4 }}>
        <TextInput label="Units" placeholder="MPa" value={draft.units} onChange={(e) => set('units', e.currentTarget.value)} required />
        <NumberInput label="Typical" value={draft.typical} onChange={(v) => set('typical', numberOrBlank(v))} required />
        <NumberInput label="Range minimum" value={draft.range_min} onChange={(v) => set('range_min', numberOrBlank(v))} required />
        <NumberInput label="Range maximum" value={draft.range_max} onChange={(v) => set('range_max', numberOrBlank(v))} required />
      </SimpleGrid>
      <Textarea label="Notes" autosize minRows={2} value={draft.notes} onChange={(e) => set('notes', e.currentTarget.value)} />
      {draft.sources.map((source, index) => (
        <SourceFields
          key={index}
          index={index}
          source={source}
          units={draft.units.trim()}
          onChange={(next) => setSource(index, next)}
          onRemove={draft.sources.length > 1 ? () => setDraft((d) => ({ ...d, sources: d.sources.filter((_, i) => i !== index) })) : null}
        />
      ))}
      <Group>
        <Button variant="light" size="xs" onClick={() => setDraft((d) => ({ ...d, sources: [...d.sources, emptySource()] }))}>
          Add another source
        </Button>
      </Group>
      {problems.length > 0 && (
        <Alert color="red" title="The entry cannot be saved yet">
          <List size="sm">
            {problems.map((p) => (
              <List.Item key={p}>{p}</List.Item>
            ))}
          </List>
        </Alert>
      )}
      <Text size="xs" c="dimmed">
        The entry is saved as proposed. Check its sources and approve it in the library before a scenario can use it.
      </Text>
      <Group justify="flex-end">
        <Button variant="default" onClick={onCancel}>
          Cancel
        </Button>
        <Button onClick={() => void save()} loading={saving}>
          Save as proposed
        </Button>
      </Group>
    </Stack>
  );
}
