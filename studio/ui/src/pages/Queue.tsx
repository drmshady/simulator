import { Badge, Button, Group, Progress, Stack, Table, Text, Title } from '@mantine/core';
import { useState } from 'react';
import { Link } from 'react-router';

import { useStudioEvents } from '../api/events';
import { cancelRun, useQueue } from '../api/studio';
import type { QueueItem, RunProgressEvent } from '../api/types';
import { formatDuration } from '../display';

function LaneTable({ title, items, progress }: { title: string; items: QueueItem[]; progress: Record<string, RunProgressEvent> }) {
  return (
    <Stack gap="xs">
      <Title order={2} size="h4">
        {title}
      </Title>
      {items.length === 0 ? (
        <Text size="sm" c="dimmed">
          Nothing queued.
        </Text>
      ) : (
        <Table withTableBorder striped fz="sm">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>#</Table.Th>
              <Table.Th>Job</Table.Th>
              <Table.Th>Status</Table.Th>
              <Table.Th>Progress</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {items.map((item) => {
              const event = item.target_id ? progress[item.target_id] : undefined;
              return (
                <Table.Tr key={item.id}>
                  <Table.Td>{item.position}</Table.Td>
                  <Table.Td>
                    {item.run ? (
                      <Link to={`/results?run=${item.target_id}`}>
                        {item.run.name} ({item.run.detail_level})
                      </Link>
                    ) : (
                      item.kind
                    )}
                    {item.run?.estimate && (
                      <Text size="xs" c="dimmed">
                        expected {formatDuration(item.run.estimate.time_s)}, {item.run.estimate.dof.toLocaleString()} DOF
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Badge variant="light" color={item.status === 'running' ? 'blue' : 'gray'}>
                      {item.status}
                    </Badge>
                  </Table.Td>
                  <Table.Td miw={200}>
                    {event ? (
                      <Stack gap={2}>
                        <Progress value={event.percent} size="sm" />
                        <Text size="xs">
                          {event.stage}
                          {event.remaining_s != null && ` · ${formatDuration(event.remaining_s)} left`}
                        </Text>
                      </Stack>
                    ) : (
                      <Text size="xs" c="dimmed">
                        {item.status === 'running' ? 'starting…' : 'waiting'}
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    {item.kind === 'run' && item.target_id && (
                      <Button size="compact-xs" color="red" variant="subtle" onClick={() => void cancelRun(item.target_id!)}>
                        Cancel
                      </Button>
                    )}
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  );
}

const OUTCOME_COLOURS: Record<string, string> = { succeeded: 'green', failed: 'red', cancelled: 'gray' };

/** The latest finished jobs, so a run that finishes does not simply vanish from the queue. */
function RecentTable({ items }: { items: QueueItem[] }) {
  return (
    <Stack gap="xs">
      <Title order={2} size="h4">
        Recently finished
      </Title>
      {items.length === 0 ? (
        <Text size="sm" c="dimmed">
          Nothing has finished yet.
        </Text>
      ) : (
        <Table withTableBorder striped fz="sm">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Job</Table.Th>
              <Table.Th>Outcome</Table.Th>
              <Table.Th>Finished</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {items.map((item) => {
              const took =
                item.started_at && item.finished_at ? (Date.parse(item.finished_at) - Date.parse(item.started_at)) / 1000 : null;
              return (
                <Table.Tr key={item.id}>
                  <Table.Td>
                    {item.run ? (
                      <Link to={`/results?run=${item.target_id}`}>
                        {item.run.name} ({item.run.detail_level})
                      </Link>
                    ) : (
                      item.kind
                    )}
                    {item.failure_reason && (
                      <Text size="xs" c="red">
                        {item.failure_reason}
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Badge variant="light" color={OUTCOME_COLOURS[item.status] ?? 'gray'}>
                      {item.status}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    {item.finished_at ? new Date(item.finished_at).toLocaleString() : ''}
                    {took != null && took >= 0 && (
                      <Text size="xs" c="dimmed">
                        took {formatDuration(took)}
                      </Text>
                    )}
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  );
}

/** Both lanes in order, with progress, time remaining and cancel (FR-019), then the latest finished jobs. */
export default function Queue() {
  const queue = useQueue();
  const [progress, setProgress] = useState<Record<string, RunProgressEvent>>({});
  useStudioEvents({ 'run.progress': (event) => setProgress((current) => ({ ...current, [event.run_id]: event })) });
  return (
    <Stack gap="lg">
      <Group justify="space-between">
        <Title order={1} size="h2">
          Queue
        </Title>
        <Text size="sm" c="dimmed">
          One heavy run and one preview at a time.
        </Text>
      </Group>
      <LaneTable title="Heavy lane (runs, studies, verification)" items={queue.data?.heavy ?? []} progress={progress} />
      <LaneTable title="Preview lane" items={queue.data?.preview ?? []} progress={progress} />
      <RecentTable items={queue.data?.recent ?? []} />
    </Stack>
  );
}
