import { Stack, Text, Title } from '@mantine/core';

/** Stand-in for a page that a later task delivers. */
export function PagePlaceholder({ title, note }: { title: string; note: string }) {
  return (
    <Stack gap="xs">
      <Title order={1} size="h2">
        {title}
      </Title>
      <Text c="dimmed">{note}</Text>
    </Stack>
  );
}
