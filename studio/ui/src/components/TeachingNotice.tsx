import { Paper, Text } from '@mantine/core';

import { TEACHING_NOTICE } from '../viewer/colourScales.generated';

export interface TeachingNoticeProps {
  /** `compact` is one small line for panels and the shell footer; `full` is a bordered box. */
  variant?: 'full' | 'compact';
}

/** The teaching-only notice from the conventions (FR-028). Always rendered; no prop hides it. */
export function TeachingNotice({ variant = 'full' }: TeachingNoticeProps) {
  if (variant === 'compact') {
    return (
      <Text role="note" aria-label="Teaching-only notice" size="xs" lh={1.4}>
        {TEACHING_NOTICE}
      </Text>
    );
  }
  return (
    <Paper role="note" aria-label="Teaching-only notice" withBorder radius="sm" px="md" py="sm">
      <Text size="sm" fw={500} lh={1.5}>
        {TEACHING_NOTICE}
      </Text>
    </Paper>
  );
}
