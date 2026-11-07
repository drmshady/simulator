import { Alert, Anchor, Badge, Button, Card, Group, Progress, Select, Stack, Table, Text, Title } from '@mantine/core';
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router';

import { useStudioEvents } from '../api/events';
import { cancelStudy, useProject, useProjects, useStudy } from '../api/studio';
import type { AxisSpread, RunProgressEvent, StudyView } from '../api/types';
import { TeachingNotice } from '../components/TeachingNotice';
import { formatDuration } from '../display';
import { formatGb } from '../format';
import { formatAxisValue, formatSpread, isActive, STATUS_COLOURS, statusLabel } from '../study';

const COMPARE_LIMIT = 6;

// --- choosing a study -------------------------------------------------------------------------------------------------

function ChooseStudy({ onChosen }: { onChosen: (id: string) => void }) {
  const projects = useProjects();
  const [projectId, setProjectId] = useState<string | null>(null);
  const project = useProject(projectId);
  return (
    <Stack gap="md" maw={640}>
      <Title order={1} size="h2">
        Studies
      </Title>
      <Text size="sm" c="dimmed">
        A study varies settings over listed values, or runs the minimum, typical and maximum of a library range. Queue
        one from the builder (Study…, or a library setting's Sensitivity button).
      </Text>
      <Select
        label="Project"
        data={(projects.data ?? []).map((p) => ({ value: p.id, label: p.name }))}
        value={projectId}
        onChange={setProjectId}
      />
      {project.data && !project.data.studies.length && <Text c="dimmed">This project has no studies.</Text>}
      {project.data?.studies.map((s) => (
        <Anchor key={s.id} component="button" ta="left" onClick={() => onChosen(s.id)}>
          {s.name || `${s.kind} study`}: {s.runs} runs, {statusLabel(s.status)}
        </Anchor>
      ))}
    </Stack>
  );
}

// --- one study --------------------------------------------------------------------------------------------------------

function Estimate({ study }: { study: StudyView }) {
  const estimate = study.estimate;
  if (!estimate) return null;
  return (
    <Card withBorder padding="sm">
      <Text size="sm" fw={600}>
        Expected when queued
      </Text>
      <Text size="sm">
        {estimate.runs} runs, {estimate.solves} solved
        {estimate.solves < estimate.runs ? ` (the others reuse a solve)` : ''}
        {estimate.time_s !== null && ` · solving ${formatDuration(estimate.time_s)} in all`}
        {estimate.memory_gb !== null && ` · at most ${formatGb(estimate.memory_gb)} of memory`}
        {estimate.disk_gb !== null && ` · ${formatGb(estimate.disk_gb)} of disk`}
      </Text>
      {estimate.warnings.map((w) => (
        <Text key={w} size="xs" c="orange.8">
          {w}
        </Text>
      ))}
    </Card>
  );
}

function elapsed(started: string | null, finished: string | null): string {
  if (!started || !finished) return '';
  return formatDuration((Date.parse(finished) - Date.parse(started)) / 1000);
}

function RunPercent({ progress }: { progress: RunProgressEvent | undefined }) {
  if (!progress) return null;
  return (
    <Text size="xs" c="dimmed">
      {progress.stage} {Math.round(progress.percent)}%
    </Text>
  );
}

function Runs({ study }: { study: StudyView }) {
  const [percent, setPercent] = useState<Record<string, RunProgressEvent>>({});
  useStudioEvents({
    'run.progress': (data) => {
      if (study.runs.some((r) => r.run_id === data.run_id)) setPercent((p) => ({ ...p, [data.run_id]: data }));
    },
  });
  const number = new Map(study.runs.map((r, i) => [r.run_id, i + 1]));
  return (
    <Table striped withTableBorder>
      <Table.Thead>
        <Table.Tr>
          <Table.Th>#</Table.Th>
          <Table.Th>Run</Table.Th>
          <Table.Th>Status</Table.Th>
          <Table.Th>Time</Table.Th>
          <Table.Th />
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {study.runs.map((run, index) => (
          <Table.Tr key={run.run_id}>
            <Table.Td>{index + 1}</Table.Td>
            <Table.Td>
              <Text size="sm">{run.label}</Text>
              {run.reuse_of && (
                <Text size="xs" c="dimmed">
                  reuses the solve of run {number.get(run.reuse_of) ?? run.reuse_of}
                </Text>
              )}
              {run.failure_reason && (
                <Text size="xs" c="red">
                  {run.failure_reason}
                </Text>
              )}
            </Table.Td>
            <Table.Td>
              <Badge color={STATUS_COLOURS[run.status] ?? 'gray'} variant="light">
                {statusLabel(run.status)}
              </Badge>
              {run.status === 'running' && <RunPercent progress={percent[run.run_id]} />}
            </Table.Td>
            <Table.Td>
              <Text size="xs">{elapsed(run.started_at, run.finished_at)}</Text>
            </Table.Td>
            <Table.Td>
              {run.status === 'succeeded' && (
                <Anchor component={Link} to={`/results?run=${run.run_id}`} size="sm">
                  Results
                </Anchor>
              )}
            </Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

function AxisSummary({ axis, kind }: { axis: AxisSpread; kind: StudyView['kind'] }) {
  const values = axis.values.filter((v, i) => axis.values.findIndex((w) => formatAxisValue(w) === formatAxisValue(v)) === i);
  const reference = formatAxisValue(axis.reference_value);
  return (
    <Stack gap={4}>
      <Text size="sm" fw={600}>
        Spread over {axis.path}
      </Text>
      <Text size="xs" c="dimmed">
        The mean over the finished runs at each value; % change is the range against the {kind === 'sensitivity' ? 'typical' : 'first'} value ({reference}).
      </Text>
      <Table.ScrollContainer minWidth={480}>
        <Table withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Key number</Table.Th>
              {values.map((v) => (
                <Table.Th key={formatAxisValue(v)} ta="right">
                  {formatAxisValue(v)}
                  {formatAxisValue(v) === reference ? ' ★' : ''}
                </Table.Th>
              ))}
              <Table.Th>Spread</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {axis.key_numbers.map((row) => (
              <Table.Tr key={`${row.id}/${row.load_case}`}>
                <Table.Td>
                  <Text size="sm">{row.label}</Text>
                  <Text size="xs" c="dimmed">
                    {row.load_case} · {row.statistic === 'p95' ? '95th percentile' : 'mean'}
                  </Text>
                </Table.Td>
                {values.map((v) => {
                  const found = row.by_value.find((m) => formatAxisValue(m.value) === formatAxisValue(v));
                  return (
                    <Table.Td key={formatAxisValue(v)} ta="right">
                      {found ? Number(found.mean.toPrecision(4)).toLocaleString() : '–'}
                    </Table.Td>
                  );
                })}
                <Table.Td>
                  <Text size="xs">{formatSpread(row)}</Text>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Stack>
  );
}

function StudyDetail({ id }: { id: string }) {
  const study = useStudy(id);
  const [cancelling, setCancelling] = useState(false);
  if (study.isPending) return <Text>Loading the study…</Text>;
  if (study.error) return <Alert color="red">{study.error.message}</Alert>;
  const data = study.data;
  const { progress, summary } = data;
  const finished = data.runs.filter((r) => r.status === 'succeeded').map((r) => r.run_id);
  const cancel = async () => {
    if (!window.confirm('Cancel the study? Its queued runs are cancelled and the running one is stopped.')) return;
    setCancelling(true);
    try {
      await cancelStudy(id);
      await study.refetch();
    } finally {
      setCancelling(false);
    }
  };
  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-start">
        <Stack gap={2}>
          <Title order={1} size="h2">
            {data.name || `${data.kind === 'sweep' ? 'Sweep' : 'Sensitivity study'} of ${data.base_scenario_name ?? 'a scenario'}`}
          </Title>
          <Group gap="xs">
            <Badge color={STATUS_COLOURS[data.status] ?? 'gray'}>{statusLabel(data.status)}</Badge>
            <Badge variant="light">{data.kind}</Badge>
            <Badge variant="light">{statusLabel(data.detail_level)}</Badge>
            <Anchor component={Link} to={`/builder?scenario=${data.base_scenario_id}`} size="sm">
              {data.base_scenario_name ?? 'base scenario'}
            </Anchor>
          </Group>
        </Stack>
        <Group gap="xs">
          {finished.length >= 2 && (
            <Button component={Link} variant="default" to={`/compare?runs=${finished.slice(0, COMPARE_LIMIT).join(',')}`}>
              Compare finished runs
            </Button>
          )}
          {isActive(data) && (
            <Button color="red" variant="light" onClick={() => void cancel()} loading={cancelling}>
              Cancel study
            </Button>
          )}
        </Group>
      </Group>
      <Stack gap={4}>
        <Progress.Root size="lg">
          <Progress.Section value={(100 * (progress.done - progress.failed)) / Math.max(1, progress.total)} color="green" />
          <Progress.Section value={(100 * progress.failed) / Math.max(1, progress.total)} color="red" />
        </Progress.Root>
        <Text size="sm">
          {progress.done} of {progress.total} runs ended
          {progress.failed ? `, ${progress.failed} failed or cancelled` : ''}
          {progress.running ? ', one running' : ''}. The runs go one at a time on the main PC and carry on with the
          browser closed.
        </Text>
      </Stack>
      <Estimate study={data} />
      <Runs study={data} />
      <Stack gap="sm">
        <Title order={2} size="h4">
          Summary of the spread
        </Title>
        {summary && summary.runs_succeeded > 0 ? (
          <>
            {summary.runs_pending > 0 && (
              <Text size="sm" c="dimmed">
                {summary.runs_pending} run{summary.runs_pending === 1 ? '' : 's'} still to finish; the summary fills in as they do.
              </Text>
            )}
            {summary.runs_failed > 0 && (
              <Text size="sm" c="orange.8">
                {summary.runs_failed} run{summary.runs_failed === 1 ? '' : 's'} did not finish and {summary.runs_failed === 1 ? 'is' : 'are'} left out.
              </Text>
            )}
            {summary.axes.map((axis) => (
              <AxisSummary key={axis.path} axis={axis} kind={data.kind} />
            ))}
          </>
        ) : (
          <Text size="sm" c="dimmed">
            The summary appears once a run has finished.
          </Text>
        )}
      </Stack>
      <TeachingNotice variant="compact" />
    </Stack>
  );
}

/** Studies (Story 4; FR-020, FR-021): progress, each run's status with its failure reason, and the spread summary. */
export default function Study() {
  const [params, setParams] = useSearchParams();
  const id = params.get('id');
  if (!id) return <ChooseStudy onChosen={(chosen) => setParams({ id: chosen })} />;
  return <StudyDetail key={id} id={id} />;
}
