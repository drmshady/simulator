import { Center, Loader, Stack, Text } from '@mantine/core';

export function FullPageLoader({ label = 'Loading…' }: { label?: string }) {
  return (
    <Center h="100vh">
      <Stack align="center" gap="xs" role="status">
        <Loader />
        <Text size="sm" c="dimmed">
          {label}
        </Text>
      </Stack>
    </Center>
  );
}
