import { Alert, Button, Group, Loader, Modal, Select, Stack, Table, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { isStudioError } from '../api/client';
import { estimateRun, startRun } from '../api/studio';
import type { DetailLevel } from '../api/types';
import { formatDuration } from '../display';
import { formatGb } from '../format';

export interface EstimateDialogProps {
  scenarioId: string;
  opened: boolean;
  onClose: () => void;
  onStarted: (runId: string) => void;
}

const LEVELS: { value: DetailLevel; label: string }[] = [
  { value: 'standard', label: 'Standard' },
  { value: 'fine', label: 'Fine' },
  { value: 'extra_fine', label: 'Extra fine' },
];

/** The estimate shown before a run starts (FR-009, FR-057): detail level, expected time, memory and disk, warnings,
 * and the refusal when the run would not fit in memory. */
export function EstimateDialog({ scenarioId, opened, onClose, onStarted }: EstimateDialogProps) {
  const [level, setLevel] = useState<DetailLevel>('standard');
  const [startError, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const query = useQuery({
    queryKey: ['estimate', scenarioId, level],
    queryFn: () => estimateRun(scenarioId, level),
    enabled: opened,
    staleTime: 0,
    retry: false,
  });
  const estimate = query.data ?? null;
  const error = startError ?? (query.error ? query.error.message : null);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const queued = await startRun(scenarioId, level);
      onStarted(queued.run_id);
    } catch (e) {
      setError(isStudioError(e) ? [e.message, ...e.problems.map((p) => p.message)].join(' ') : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal opened={opened} onClose={onClose} title="Run in detail" size="lg">
      <Stack gap="sm">
        <Select
          label="Detail level"
          data={LEVELS}
          value={level}
          onChange={(next) => next && setLevel(next as DetailLevel)}
          allowDeselect={false}
        />
        {!estimate && !error && <Loader size="sm" />}
        {estimate && (
          <Table withTableBorder fz="sm">
            <Table.Tbody>
              <Table.Tr>
                <Table.Td>Degrees of freedom</Table.Td>
                <Table.Td>{estimate.dof.toLocaleString()}</Table.Td>
              </Table.Tr>
              <Table.Tr>
                <Table.Td>Unit load cases</Table.Td>
                <Table.Td>{estimate.unit_cases}</Table.Td>
              </Table.Tr>
              <Table.Tr>
                <Table.Td>Expected time</Table.Td>
                <Table.Td>{formatDuration(estimate.time_s)}</Table.Td>
              </Table.Tr>
              <Table.Tr>
                <Table.Td>Peak memory</Table.Td>
                <Table.Td>
                  {formatGb(estimate.memory_gb)} (limit {formatGb(estimate.memory_limit_gb)})
                </Table.Td>
              </Table.Tr>
              <Table.Tr>
                <Table.Td>Disk</Table.Td>
                <Table.Td>{formatGb(estimate.disk_gb)}</Table.Td>
              </Table.Tr>
            </Table.Tbody>
          </Table>
        )}
        {estimate?.warnings.map((w) => (
          <Alert key={w} color={estimate.fits ? 'yellow' : 'red'}>
            {w}
          </Alert>
        ))}
        {error && <Alert color="red">{error}</Alert>}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Close
          </Button>
          <Button onClick={() => void start()} loading={busy} disabled={!estimate?.fits && estimate !== null}>
            Start the run
          </Button>
        </Group>
        <Text size="xs" c="dimmed">
          A run is queued in the heavy lane; one runs at a time.
        </Text>
      </Stack>
    </Modal>
  );
}
