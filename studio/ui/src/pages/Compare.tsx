import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Checkbox,
  Group,
  SegmentedControl,
  Select,
  SimpleGrid,
  Slider,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router';

import { useComparison, useProject, useProjects } from '../api/studio';
import type { CompareView, ComparedRunView, KeyNumberRow } from '../api/types';
import { TeachingNotice } from '../components/TeachingNotice';
import { describeDifference, formatDelta, framing, parseRunIds } from '../organise';
import { CameraLink } from '../viewer/cameraLink';
import { FIELDS, formatMagnification, type FieldName } from '../viewer/colourScale';
import { GlbModel } from '../viewer/GlbModel';
import { ContinuousLegend, FrostLegend } from '../viewer/Legend';
import { SyncedScene } from '../viewer/SyncedScene';

// --- choosing runs ----------------------------------------------------------------------------------------------------

function ChooseRuns({ onChosen }: { onChosen: (ids: string[]) => void }) {
  const projects = useProjects();
  const [projectId, setProjectId] = useState<string | null>(null);
  const project = useProject(projectId);
  const [chosen, setChosen] = useState<string[]>([]);
  const finished = (project.data?.runs ?? []).filter((r) => r.status === 'succeeded');
  return (
    <Stack gap="md" maw={640}>
      <Title order={1} size="h2">
        Compare
      </Title>
      <Text size="sm" c="dimmed">
        Choose two or more finished runs. The first one chosen is the base the others are compared with. The
        comparison uses the stored results only, so nothing is solved again.
      </Text>
      <Select
        label="Project"
        data={(projects.data ?? []).map((p) => ({ value: p.id, label: p.name }))}
        value={projectId}
        onChange={(id) => {
          setProjectId(id);
          setChosen([]);
        }}
      />
      {project.data && !finished.length && <Text c="dimmed">This project has no finished runs.</Text>}
      <Stack gap={4}>
        {finished.map((r) => (
          <Checkbox
            key={r.id}
            label={`${r.name} · ${r.detail_level} · ${new Date(r.finished_at ?? r.created_at).toLocaleString()}${chosen[0] === r.id ? ' (base)' : ''}`}
            checked={chosen.includes(r.id)}
            onChange={() => setChosen((c) => (c.includes(r.id) ? c.filter((x) => x !== r.id) : [...c, r.id]))}
          />
        ))}
      </Stack>
      <Group>
        <Button disabled={chosen.length < 2} onClick={() => onChosen(chosen)}>
          Compare
        </Button>
      </Group>
    </Stack>
  );
}

// --- the comparison ---------------------------------------------------------------------------------------------------

function quantity(value: number | null | undefined, unit: string): string {
  return value === null || value === undefined ? '–' : `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })} ${unit}`;
}

function RunTitle({ run, base }: { run: ComparedRunView; base: boolean }) {
  return (
    <Stack gap={2}>
      <Group gap="xs">
        <Anchor component={Link} to={`/results?run=${run.run_id}`} fw={600} size="sm">
          {run.name}
        </Anchor>
        {base && <Badge size="xs">base</Badge>}
        <Text size="xs" c="dimmed">
          {run.detail_level}
        </Text>
      </Group>
      {run.flags.length > 0 && (
        <Group gap={4}>
          {run.flags.map((flag) => (
            <Badge key={flag} size="xs" color="red" variant="light">
              {flag.replace(/_/g, ' ')}
            </Badge>
          ))}
        </Group>
      )}
    </Stack>
  );
}

function KeyNumberTable({ rows, runs }: { rows: KeyNumberRow[]; runs: ComparedRunView[] }) {
  return (
    <Table striped withTableBorder fz="xs">
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Key number</Table.Th>
          {runs.map((run, i) => (
            <Table.Th key={run.run_id} ta="right">
              {run.name}
              {i === 0 ? ' (base)' : ''}
            </Table.Th>
          ))}
          {runs.slice(1).map((run) => (
            <Table.Th key={run.run_id} ta="right">
              {run.name} − base
            </Table.Th>
          ))}
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {rows.map((row) => (
          <Table.Tr key={`${row.id}-${row.load_case}`}>
            <Table.Td>
              {row.label}
              {row.block ? ` (${row.block})` : ''}, {row.statistic === 'p95' ? '95th percentile' : 'mean'}
            </Table.Td>
            {row.values.map((value, i) => (
              <Table.Td key={runs[i]?.run_id ?? i} ta="right">
                {quantity(value, row.unit)}
                {row.percent_of_reference[i] !== null && row.percent_of_reference[i] !== undefined
                  ? ` (${row.percent_of_reference[i]!.toFixed(1)}% of reference)`
                  : ''}
              </Table.Td>
            ))}
            {row.differences.map((delta, i) => (
              <Table.Td key={runs[i + 1]?.run_id ?? i} ta="right">
                {formatDelta(delta, row.unit)}
              </Table.Td>
            ))}
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

function ComparisonBody({ comparison }: { comparison: CompareView }) {
  const [loadCase, setLoadCase] = useState(comparison.load_cases[0] ?? '');
  const [field, setField] = useState<FieldName>('frost');
  const [gauge, setGauge] = useState<'strain' | 'strain_maxp'>('strain');
  const [magnification, setMagnification] = useState(comparison.deformation.magnification);
  const link = useMemo(() => new CameraLink(framing(comparison.bounds_mm)), [comparison.bounds_mm]);
  const shown: FieldName = field === 'strain' ? gauge : field;
  const scale = comparison.scales[loadCase]?.[shown];
  const range: [number, number] = scale && !scale.categorical ? [scale.min, scale.max] : [0, 1];
  const names = new Map(comparison.runs.map((r) => [r.run_id, r.name]));
  const base = comparison.runs[0];
  const rows = comparison.key_numbers.filter((row) => row.load_case === loadCase);
  const largest = comparison.deformation.max_displacement_mm;

  return (
    <Stack gap="md">
      {comparison.warnings.map((warning) => (
        <Alert key={warning} color="orange" p="xs">
          {warning}
        </Alert>
      ))}
      <Group gap="sm" align="flex-end">
        <Select
          size="xs"
          label="Load case"
          data={comparison.load_cases}
          value={loadCase}
          onChange={(next) => next && setLoadCase(next)}
          allowDeselect={false}
        />
        <SegmentedControl
          size="xs"
          value={field}
          onChange={(next) => setField(next as FieldName)}
          data={(['frost', 'strain', 'stress', 'pct_strength'] as FieldName[]).map((f) => ({ value: f, label: FIELDS[f].label }))}
        />
        {field === 'strain' && (
          <SegmentedControl
            size="xs"
            value={gauge}
            onChange={(next) => setGauge(next as 'strain' | 'strain_maxp')}
            data={[
              { value: 'strain', label: 'Von Mises equivalent strain' },
              { value: 'strain_maxp', label: '|Maximum principal strain|' },
            ]}
          />
        )}
      </Group>
      <SimpleGrid cols={{ base: 1, md: Math.min(3, comparison.runs.length) }} spacing="sm">
        {comparison.runs.map((run, i) => (
          <Card key={run.run_id} withBorder padding="xs">
            <Stack gap="xs">
              <RunTitle run={run} base={i === 0} />
              <SyncedScene link={link}>
                {run.assets
                  .filter((a) => a.load_case === loadCase)
                  .map((asset) => (
                    <GlbModel key={asset.url} url={asset.url} field={shown} range={range} magnification={magnification} />
                  ))}
              </SyncedScene>
            </Stack>
          </Card>
        ))}
      </SimpleGrid>
      <Text size="xs" c="dimmed">
        Every panel shares one camera (turn or zoom any of them), one colour scale and one deformation scale.
      </Text>
      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="sm">
        {shown === 'frost' ? <FrostLegend /> : <ContinuousLegend field={shown} range={range} />}
        <Card withBorder padding="sm">
          <Stack gap={4}>
            <Text size="sm" fw={600}>
              Movement, on every panel: displacement magnified {formatMagnification(magnification)}
            </Text>
            <Slider min={1} max={Math.max(2000, comparison.deformation.magnification * 4)} step={1} value={magnification} onChange={setMagnification} label={formatMagnification} />
            <Text size="xs" c="dimmed">
              Largest displacement of any run: {(1000 * largest).toLocaleString(undefined, { maximumFractionDigits: 1 })} µm. The
              starting magnification draws it as 5% of the model's size.
            </Text>
          </Stack>
        </Card>
      </SimpleGrid>
      <Card withBorder padding="sm">
        <Stack gap="xs">
          <Text fw={600}>Settings that differ</Text>
          {comparison.settings.map((entry) => (
            <Stack key={entry.run_id} gap={2}>
              <Text size="sm">
                {names.get(entry.run_id)} compared with {base?.name}:
              </Text>
              {entry.differences.length ? (
                entry.differences.map((d) => (
                  <Text key={d.path} size="xs" pl="sm">
                    {describeDifference(d)}
                  </Text>
                ))
              ) : (
                <Text size="xs" pl="sm" c="dimmed">
                  No setting differs.
                </Text>
              )}
            </Stack>
          ))}
        </Stack>
      </Card>
      <Card withBorder padding="sm">
        <Stack gap="xs">
          <Group gap="xs">
            <Text fw={600}>Key numbers, {loadCase}</Text>
            <Badge size="sm" variant="light">
              teaching reference
            </Badge>
          </Group>
          <KeyNumberTable rows={rows} runs={comparison.runs} />
          <Text size="xs" c="dimmed">
            Region means and 95th percentiles from integration points. Differences are from the base: absolute, and
            relative to the base value.
          </Text>
          <TeachingNotice variant="compact" />
        </Stack>
      </Card>
      <TeachingNotice />
    </Stack>
  );
}

/** Side-by-side comparison of finished runs (Story 3, FR-027, SC-008): shared camera, colour scale and deformation
 * scale; the settings that differ; key-number differences; the teaching-only notice. */
export default function Compare() {
  const [params, setParams] = useSearchParams();
  const runIds = useMemo(() => parseRunIds(params.get('runs')), [params]);
  const comparison = useComparison(runIds);
  if (runIds.length < 2) return <ChooseRuns onChosen={(ids) => setParams({ runs: ids.join(',') })} />;
  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={1} size="h2">
          Compare {runIds.length} runs
        </Title>
        <Button variant="default" size="xs" onClick={() => setParams({})}>
          Choose other runs
        </Button>
      </Group>
      {comparison.isPending && <Text>Reading the stored results…</Text>}
      {comparison.error && <Alert color="red">{comparison.error.message}</Alert>}
      {comparison.data && <ComparisonBody key={comparison.data.run_ids.join(',')} comparison={comparison.data} />}
    </Stack>
  );
}
