import { Badge, Button, Group, Text, Title, Tooltip } from '@mantine/core';
import { useQueryClient } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router';

import { LOGIN_ROUTE, logout } from '../api/auth';
import { isStudioError } from '../api/client';
import type { ConnectionState } from '../api/events';
import { useSystemInfo } from '../api/system';
import type { SystemInfo } from '../api/types';
import { formatAvailableOf, formatGb, formatLane, sameHash, shortHash } from '../format';
import { CONVENTIONS_HASH, CONVENTIONS_VERSION } from '../viewer/colourScales.generated';

const CONNECTION: Record<ConnectionState, { label: string; colour: string; help: string }> = {
  open: { label: 'Live', colour: 'green', help: 'Live updates from the main PC are arriving.' },
  connecting: { label: 'Connecting…', colour: 'yellow', help: 'Connecting to live updates from the main PC.' },
  closed: {
    label: 'Offline',
    colour: 'red',
    help: 'Live updates are not arriving. The studio keeps retrying and refreshes everything when they return.',
  },
};

function StatusItem({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <Text size="xs" c="dimmed" lh={1.2}>
        {label}
      </Text>
      <Group gap={6} wrap="nowrap">
        {children}
      </Group>
    </div>
  );
}

function SystemStatus({ system }: { system: SystemInfo }) {
  const conventionsMatch = sameHash(system.conventions_hash, CONVENTIONS_HASH);
  return (
    <>
      <StatusItem label="Engine">
        <Text size="sm">{system.engine_version}</Text>
        {system.dev_build && (
          <Tooltip
            multiline
            w={280}
            withArrow
            label={`Development build: the engine runs from uncommitted code (source ${shortHash(system.engine_source_hash)}). Runs from uncommitted engine code can never be published.`}
          >
            <Badge color="orange" variant="light" size="sm" tabIndex={0}>
              development build
            </Badge>
          </Tooltip>
        )}
      </StatusItem>
      <StatusItem label="Conventions">
        <Text size="sm">{system.conventions_version}</Text>
        {!conventionsMatch && (
          <Tooltip
            multiline
            w={300}
            withArrow
            label={`The interface's colour scales were generated from conventions ${CONVENTIONS_VERSION} (${shortHash(CONVENTIONS_HASH)}), but the server uses ${system.conventions_version} (${shortHash(system.conventions_hash)}). Regenerate and rebuild the interface.`}
          >
            <Badge color="red" variant="light" size="sm" tabIndex={0}>
              interface out of date
            </Badge>
          </Tooltip>
        )}
      </StatusItem>
      <StatusItem label="Solver">
        {system.solver_build_id ? (
          <Text size="sm">{system.solver_build_id}</Text>
        ) : (
          <Tooltip multiline w={320} withArrow label={system.solver_error ?? 'No solver build was found.'}>
            <Text size="sm" c="orange" fw={600} truncate="end" maw={220} tabIndex={0}>
              {system.solver_error ?? 'not available'}
            </Text>
          </Tooltip>
        )}
      </StatusItem>
      <StatusItem label="Memory free">
        <Text size="sm">{formatAvailableOf(system.memory.available_gb, system.memory.total_gb)}</Text>
      </StatusItem>
      <StatusItem label="Disk free">
        <Tooltip withArrow label={`${system.disk.path}: ${formatGb(system.disk.total_gb)} in total`}>
          <Text size="sm" tabIndex={0}>
            {formatGb(system.disk.free_gb)}
          </Text>
        </Tooltip>
      </StatusItem>
      <StatusItem label="Heavy lane">
        <Text size="sm">{formatLane(system.queue.heavy)}</Text>
      </StatusItem>
      <StatusItem label="Preview lane">
        <Text size="sm">{formatLane(system.queue.preview)}</Text>
      </StatusItem>
    </>
  );
}

function SignOutButton() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function signOut() {
    setPending(true);
    setFailure(null);
    try {
      await logout();
    } catch (error) {
      // Already signed out is fine; anything else means the session may still be open.
      if (!(isStudioError(error) && error.status === 401)) {
        setFailure(isStudioError(error) ? error.message : 'Sign-out failed.');
        setPending(false);
        return;
      }
    }
    await navigate(LOGIN_ROUTE, { replace: true });
    queryClient.clear();
  }

  return (
    <Group gap="xs" wrap="nowrap">
      {failure && (
        <Text size="xs" c="red" role="alert" truncate="end" maw={240} title={failure}>
          Not signed out: {failure}
        </Text>
      )}
      <Button variant="default" size="xs" loading={pending} onClick={() => void signOut()}>
        Sign out
      </Button>
    </Group>
  );
}

/** The shell header: studio name, the main PC's status (polled every 10 s), live-update state and sign-out. */
export function SystemBar({ connection, burger }: { connection: ConnectionState; burger?: ReactNode }) {
  const system = useSystemInfo();
  const live = CONNECTION[connection];

  return (
    <Group h="100%" px="md" gap="lg" justify="space-between" wrap="nowrap">
      <Group gap="sm" wrap="nowrap">
        {burger}
        <Title order={2} size="h5" style={{ whiteSpace: 'nowrap' }}>
          Simulation Studio
        </Title>
      </Group>

      <Group gap="lg" wrap="nowrap" style={{ overflow: 'hidden', flex: 1 }} visibleFrom="md" aria-label="Main PC status">
        {system.data ? (
          <SystemStatus system={system.data} />
        ) : system.isError ? (
          <Text size="sm" c="red">
            System status unavailable: {system.error.message}
          </Text>
        ) : (
          <Text size="sm" c="dimmed">
            Loading system status…
          </Text>
        )}
      </Group>

      <Group gap="sm" wrap="nowrap">
        <Tooltip label={live.help} multiline w={260} withArrow>
          <Badge variant="dot" color={live.colour} size="md" tabIndex={0} aria-live="polite">
            {live.label}
          </Badge>
        </Tooltip>
        <SignOutButton />
      </Group>
    </Group>
  );
}
