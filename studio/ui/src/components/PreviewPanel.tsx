import { Alert, Badge, Group, Loader, SegmentedControl, Select, Stack, Table, Text } from '@mantine/core';
import { useCallback, useEffect, useRef, useState } from 'react';

import { isStudioError } from '../api/client';
import { useStudioEvents } from '../api/events';
import { getPreview, requestPreview } from '../api/studio';
import type { KeyNumber, LoadCase, PreviewView } from '../api/types';
import { headlineNumbers } from '../display';
import { FIELDS, type FieldName } from '../viewer/colourScale';
import { ResultView } from '../viewer/ResultView';
import { TeachingNotice } from './TeachingNotice';

const PREVIEW_DELAY_MS = 600;

export interface PreviewPanelProps {
  scenarioId: string;
  /** The saved revision; a new one starts a new preview after `PREVIEW_DELAY_MS`. */
  revision: number;
  loads: LoadCase[];
  /** Whether the scenario passed its checks; an invalid one is not previewed. */
  valid: boolean;
}

type PanelState =
  | { kind: 'idle' }
  | { kind: 'waiting'; previewId: string | null }
  | { kind: 'ready'; preview: PreviewView }
  | { kind: 'refused'; message: string; missing: string[] }
  | { kind: 'failed'; message: string };

function NumbersTable({ numbers }: { numbers: KeyNumber[] }) {
  return (
    <Table striped withTableBorder fz="xs">
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Result</Table.Th>
          <Table.Th>Statistic</Table.Th>
          <Table.Th ta="right">Value</Table.Th>
          <Table.Th ta="right">% of reference</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {numbers.map((n) => (
          <Table.Tr key={`${n.load_case}-${n.id}`}>
            <Table.Td>
              {n.label}
              {n.side ? `, ${n.side} side` : ''} ({n.block})
            </Table.Td>
            <Table.Td>{n.statistic === 'p95' ? '95th percentile' : 'mean'}</Table.Td>
            <Table.Td ta="right">
              {n.value.toLocaleString(undefined, { maximumFractionDigits: 1 })} {n.unit}
            </Table.Td>
            <Table.Td ta="right">
              {n.percent_of_reference != null ? `${n.percent_of_reference.toFixed(1)}%` : n.frost_category ?? ''}
            </Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

/** The automatic preview (FR-017, SC-002): 600 ms after the last saved change, of the active load case; labelled
 * "preview", with its accuracy note; a refusal names the missing benchmark. */
export function PreviewPanel({ scenarioId, revision, loads, valid }: PreviewPanelProps) {
  const [chosen, setActive] = useState<string | null>(loads[0]?.id ?? null);
  const active = loads.some((l) => l.id === chosen) ? chosen : (loads[0]?.id ?? null);
  const [field, setField] = useState<FieldName>('frost');
  const [current, setState] = useState<PanelState>({ kind: 'idle' });
  const waitingFor = useRef<string | null>(null);

  const show = useCallback(async (previewId: string) => {
    const preview = await getPreview(previewId);
    if (preview.status === 'succeeded') setState({ kind: 'ready', preview });
    else if (preview.status === 'failed') setState({ kind: 'failed', message: preview.failure_reason ?? 'The preview failed.' });
  }, []);

  useStudioEvents({
    'preview.ready': (data) => {
      if (data.preview_id && data.preview_id === waitingFor.current) void show(String(data.preview_id));
    },
  });

  useEffect(() => {
    if (!valid || !active) return undefined;
    let cancelled = false;
    const timer = setTimeout(async () => {
      setState({ kind: 'waiting', previewId: null });
      try {
        const queued = await requestPreview(scenarioId, [active]);
        if (cancelled) return;
        waitingFor.current = queued.preview_id;
        setState({ kind: 'waiting', previewId: queued.preview_id });
        if (queued.status === 'succeeded') await show(queued.preview_id);
      } catch (error) {
        if (cancelled) return;
        if (isStudioError(error, 'run_refused')) {
          setState({ kind: 'refused', message: error.message, missing: error.missing.map((m) => m.message) });
        } else {
          setState({ kind: 'failed', message: error instanceof Error ? error.message : String(error) });
        }
      }
    }, PREVIEW_DELAY_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [scenarioId, revision, active, valid, show]);

  const state: PanelState = valid && active ? current : { kind: 'idle' };
  const assets = state.kind === 'ready' ? state.preview.assets.filter((a) => a.load_case === active) : [];
  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Group gap="xs">
          <Text fw={600}>Preview</Text>
          <Badge color="yellow" variant="filled">
            preview
          </Badge>
          {state.kind === 'waiting' && <Loader size="xs" />}
        </Group>
        <Select
          size="xs"
          aria-label="Active load case"
          data={loads.map((l) => ({ value: l.id, label: l.name ?? l.id }))}
          value={active}
          onChange={setActive}
          allowDeselect={false}
        />
      </Group>
      {!valid && <Text size="sm" c="dimmed">The scenario has problems; fix them to see a preview.</Text>}
      {state.kind === 'refused' && (
        <Alert color="orange" title="Preview refused">
          <Text size="sm">{state.message}</Text>
          {state.missing.map((m) => (
            <Text key={m} size="xs">
              {m}
            </Text>
          ))}
        </Alert>
      )}
      {state.kind === 'failed' && (
        <Alert color="red" title="Preview failed">
          {state.message}
        </Alert>
      )}
      {state.kind === 'ready' && (
        <>
          <Text size="xs" c="dimmed">
            {state.preview.accuracy_note}
            {state.preview.time_s != null && ` · ${state.preview.dof?.toLocaleString()} DOF, ${state.preview.time_s.toFixed(1)} s`}
          </Text>
          <SegmentedControl
            size="xs"
            value={field}
            onChange={(next) => setField(next as FieldName)}
            data={(['frost', 'strain', 'stress', 'pct_strength'] as FieldName[]).map((f) => ({ value: f, label: FIELDS[f].label }))}
          />
          <ResultView assets={assets} field={field} height={360} />
          <NumbersTable numbers={headlineNumbers(state.preview.key_numbers.filter((n) => n.load_case === active))} />
          {state.preview.locked_results.map((r) => (
            <Text key={`${r.block}-${r.result_type}-${r.missing_capability}`} size="xs" c="dimmed">
              {r.result_type} ({r.block}): {r.message}
            </Text>
          ))}
        </>
      )}
      <TeachingNotice variant="compact" />
    </Stack>
  );
}
