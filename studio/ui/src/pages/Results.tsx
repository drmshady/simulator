import {
  Alert,
  Badge,
  Card,
  Chip,
  Grid,
  Group,
  SegmentedControl,
  Select,
  Slider,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router';

import { useResults, useRun } from '../api/studio';
import type { KeyNumber, ResultsView, RunView } from '../api/types';
import { TeachingNotice } from '../components/TeachingNotice';
import { paired, SCREW_NOTE } from '../display';
import { FIELDS, formatMagnification, type FieldName } from '../viewer/colourScale';
import { ResultView } from '../viewer/ResultView';

function value(n: KeyNumber): string {
  return `${n.value.toLocaleString(undefined, { maximumFractionDigits: 1 })} ${n.unit}`;
}

function CrestalPanel({ numbers }: { numbers: KeyNumber[] }) {
  const rows = paired(numbers.filter((n) => n.unit === 'µε'));
  return (
    <Card withBorder padding="sm">
      <Stack gap="xs">
        <Text fw={600}>Crestal bone strain, worse side</Text>
        {rows.map(({ key, mean, p95 }) => (
          <Text key={key} size="sm">
            {mean?.block}: {mean?.side} side, mean {mean && value(mean)} ({mean?.frost_category?.replace(/_/g, ' ')}), 95th
            percentile {p95 && value(p95)} ({p95?.frost_category?.replace(/_/g, ' ')})
          </Text>
        ))}
        <Badge variant="light" size="sm">
          teaching reference
        </Badge>
        <TeachingNotice variant="compact" />
      </Stack>
    </Card>
  );
}

function StressTable({ numbers }: { numbers: KeyNumber[] }) {
  const rows = paired(numbers.filter((n) => n.reference));
  return (
    <Card withBorder padding="sm">
      <Stack gap="xs">
        <Group gap="xs">
          <Text fw={600}>Component stress, % of the strength reference</Text>
          <Badge variant="light" size="sm">
            teaching reference
          </Badge>
        </Group>
        <Table striped withTableBorder fz="xs">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Region</Table.Th>
              <Table.Th ta="right">Mean</Table.Th>
              <Table.Th ta="right">95th percentile</Table.Th>
              <Table.Th>Reference</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map(({ key, mean, p95 }) => (
              <Table.Tr key={key}>
                <Table.Td>
                  {mean?.label} ({mean?.block}){mean?.region === 'screw' ? `, ${SCREW_NOTE}` : ''}
                </Table.Td>
                <Table.Td ta="right">
                  {mean?.percent_of_reference?.toFixed(1)}% ({mean && value(mean)})
                </Table.Td>
                <Table.Td ta="right">
                  {p95?.percent_of_reference?.toFixed(1)}% ({p95 && value(p95)})
                </Table.Td>
                <Table.Td>
                  {mean?.reference?.parameter_key} v{mean?.reference?.entry_version}: {mean?.reference?.value} MPa
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        <TeachingNotice variant="compact" />
      </Stack>
    </Card>
  );
}

function DisplacementPanel({ numbers }: { numbers: KeyNumber[] }) {
  const rows = paired(numbers.filter((n) => n.unit === 'µm'));
  return (
    <Card withBorder padding="sm">
      <Stack gap="xs">
        <Text fw={600}>Displacement</Text>
        {rows.map(({ key, mean, p95 }) => (
          <Text key={key} size="sm">
            {mean?.label} ({mean?.block}): mean {mean && value(mean)}, 95th percentile {p95 && value(p95)}
          </Text>
        ))}
        <TeachingNotice variant="compact" />
      </Stack>
    </Card>
  );
}

function RunRecord({ run }: { run: RunView }) {
  const r = run.run;
  const worstForce = Math.max(0, ...(r.convergence?.force_balance ?? []).map((f) => f.relative_error));
  const worstEnergy = Math.max(0, ...(r.convergence?.energy_balance ?? []).map((e) => e.relative_error));
  return (
    <Card withBorder padding="sm">
      <Stack gap={4}>
        <Text fw={600}>Run record</Text>
        <Text size="xs">
          Engine {r.engine_version} · conventions {r.conventions_version} · gmsh {r.gmsh_version}
        </Text>
        <Text size="xs" style={{ wordBreak: 'break-all' }}>
          Solver {r.solver_build_id}
        </Text>
        <Text size="xs">
          {r.detail_level} level · {r.actual?.dof.toLocaleString()} DOF · {r.actual?.time_s.toFixed(0)} s
        </Text>
        {r.convergence && (
          <Text size="xs">
            Convergence: force balance {worstForce.toExponential(1)}, energy {worstEnergy.toExponential(1)}, solver
            residual {r.convergence.solver_relative_residual?.toExponential(1) ?? 'not reported'}
          </Text>
        )}
        <Group gap={4}>
          {run.flags.map((flag) => (
            <Badge key={flag} color="red" variant="light" size="sm">
              {flag.replace(/_/g, ' ')}
            </Badge>
          ))}
        </Group>
        {run.stale_reasons.map((reason) => (
          <Text key={reason} size="xs" c="red">
            {reason}
          </Text>
        ))}
        <TeachingNotice variant="compact" />
      </Stack>
    </Card>
  );
}

function ResultsBody({ run, results }: { run: RunView; results: ResultsView }) {
  const cases = results.combined_cases;
  const [loadCase, setLoadCase] = useState<string>(cases[0] ?? '');
  const [field, setField] = useState<FieldName>('frost');
  const [gauge, setGauge] = useState<'strain' | 'strain_maxp'>('strain');
  const [magnification, setMagnification] = useState(200);
  const parts = useMemo(() => [...new Set(results.assets.map((a) => a.part))], [results.assets]);
  const [hidden, setHidden] = useState<string[]>([]);
  const numbers = results.key_numbers.filter((n) => n.load_case === loadCase);
  const shownField: FieldName = field === 'strain' ? gauge : field;

  return (
    <Grid gap="md">
      <Grid.Col span={{ base: 12, md: 7 }}>
        <Stack gap="sm">
          <Group gap="sm">
            <Select
              size="xs"
              label="Load case"
              data={cases}
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
          </Group>
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
          <ResultView
            assets={results.assets.filter((a) => a.load_case === loadCase)}
            field={shownField}
            magnification={magnification}
            hiddenParts={new Set(hidden)}
          />
          <Stack gap={2}>
            <Text size="sm">Movement: displacement magnified {formatMagnification(magnification)}</Text>
            <Slider min={0} max={2000} step={10} value={magnification} onChange={setMagnification} label={formatMagnification} />
          </Stack>
          <Chip.Group multiple value={parts.filter((p) => !hidden.includes(p))} onChange={(shown) => setHidden(parts.filter((p) => !shown.includes(p)))}>
            <Group gap={4}>
              {parts.map((p) => (
                <Chip key={p} value={p} size="xs">
                  {p}
                </Chip>
              ))}
            </Group>
          </Chip.Group>
          <TeachingNotice />
        </Stack>
      </Grid.Col>
      <Grid.Col span={{ base: 12, md: 5 }}>
        <Stack gap="sm">
          <CrestalPanel numbers={numbers} />
          <StressTable numbers={numbers} />
          <DisplacementPanel numbers={numbers} />
          <Card withBorder padding="sm">
            <Stack gap={4}>
              <Text fw={600}>Not reported</Text>
              {results.locked_results.map((r) => (
                <Text key={`${r.block}-${r.result_type}-${r.missing_capability}`} size="sm">
                  {r.result_type.replace(/_/g, ' ')} ({r.block}): {r.message}
                </Text>
              ))}
              <TeachingNotice variant="compact" />
            </Stack>
          </Card>
          <RunRecord run={run} />
        </Stack>
      </Grid.Col>
    </Grid>
  );
}

/** A run's results (Story 1): the Frost-coloured strain map with its legend and sources, the crestal value per
 * implant on its worse side, component stress as % of strength, displacement, the movement animation, the locked
 * results and the run record, with the teaching-only notice on every panel. */
export default function Results() {
  const [params] = useSearchParams();
  const runId = params.get('run');
  const run = useRun(runId);
  const succeeded = run.data?.run.status === 'succeeded';
  const results = useResults(runId, succeeded);
  if (!runId) return <Text c="dimmed">Open a run from the queue or the builder.</Text>;
  if (run.isPending) return <Text>Loading the run…</Text>;
  if (run.error) return <Alert color="red">{run.error.message}</Alert>;
  const status = run.data.run.status;
  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={1} size="h2">
          {run.data.run.snapshot.name}
        </Title>
        <Badge size="lg" color={succeeded ? 'green' : status === 'failed' ? 'red' : 'blue'}>
          {status}
        </Badge>
      </Group>
      {status === 'failed' && (
        <Alert color="red" title="The run failed">
          {run.data.run.failure_reason}
        </Alert>
      )}
      {!succeeded && status !== 'failed' && <Text c="dimmed">The run is {status}; its results appear here when it finishes.</Text>}
      {results.data && <ResultsBody run={run.data} results={results.data} />}
    </Stack>
  );
}
