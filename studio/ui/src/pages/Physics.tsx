import { Anchor, Badge, Stack, Table, Text, Title } from '@mantine/core';

import { usePhysics } from '../api/studio';

/** Each physics capability with its status, the cases that gate it and the report that made it ready (FR-011a). */
export default function Physics() {
  const physics = usePhysics();
  return (
    <Stack gap="md">
      <Title order={1} size="h2">
        Physics
      </Title>
      <Text size="sm" c="dimmed">
        A capability is ready only when every one of its required verification cases passes in one report; until
        then, scenarios that need it can be built and saved but not previewed or run.
      </Text>
      <Table withTableBorder striped>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Capability</Table.Th>
            <Table.Th>Status</Table.Th>
            <Table.Th>Required cases</Table.Th>
            <Table.Th>Report</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {(physics.data ?? []).map((capability) => (
            <Table.Tr key={capability.id}>
              <Table.Td>
                <Text fw={500}>{capability.label}</Text>
                <Text size="xs" c="dimmed">
                  {capability.linear ? 'linear: unit load cases are combined' : 'non-linear'}
                </Text>
              </Table.Td>
              <Table.Td>
                <Badge color={capability.status === 'ready' ? 'green' : 'orange'} variant="light">
                  {capability.status === 'ready' ? 'ready' : 'awaiting benchmark'}
                </Badge>
                {capability.message && (
                  <Text size="xs" c="dimmed">
                    {capability.message}
                  </Text>
                )}
              </Table.Td>
              <Table.Td>
                {capability.required_cases.length ? (
                  capability.required_cases.map((c) => (
                    <Text key={c} size="xs">
                      {c}
                    </Text>
                  ))
                ) : (
                  <Text size="xs" c="dimmed">
                    none written yet
                  </Text>
                )}
              </Table.Td>
              <Table.Td>
                {capability.report_url ? (
                  <Anchor size="sm" href={capability.report_url} target="_blank">
                    {capability.report_id}
                  </Anchor>
                ) : (
                  <Text size="xs" c="dimmed">
                    —
                  </Text>
                )}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Stack>
  );
}
