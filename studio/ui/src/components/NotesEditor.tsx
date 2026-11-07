import { Button, Group, Stack, TagsInput, Text, Textarea } from '@mantine/core';
import { useState } from 'react';

import { cleanTags } from '../organise';

export interface NotesEditorProps {
  notes: string;
  tags: string[];
  /** Saves the notes and tags; rejects with a message to show. */
  onSave: (notes: string, tags: string[]) => Promise<unknown>;
  /** Tags already used elsewhere, offered while typing. */
  suggestions?: string[];
  compact?: boolean;
}

/** Notes and tags of a project, scenario or run (FR-031): both are searchable. Saved with the button. */
export function NotesEditor({ notes, tags, onSave, suggestions = [], compact = false }: NotesEditorProps) {
  const [text, setText] = useState(notes);
  const [chosen, setChosen] = useState<string[]>(tags);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const changed = text !== notes || cleanTags(chosen).join('\n') !== tags.join('\n');

  const save = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await onSave(text, cleanTags(chosen));
      setMessage('Saved.');
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Stack gap="xs">
      <Textarea
        size="xs"
        label="Notes"
        autosize
        minRows={compact ? 1 : 2}
        maxRows={8}
        value={text}
        onChange={(e) => setText(e.currentTarget.value)}
      />
      <TagsInput
        size="xs"
        label="Tags"
        placeholder="Type a tag and press Enter"
        data={suggestions.filter((s) => !chosen.includes(s))}
        value={chosen}
        onChange={setChosen}
        clearable
      />
      <Group gap="xs">
        <Button size="xs" variant="light" onClick={() => void save()} loading={busy} disabled={!changed}>
          Save notes and tags
        </Button>
        {message && (
          <Text size="xs" c={message === 'Saved.' ? 'dimmed' : 'red'}>
            {message}
          </Text>
        )}
      </Group>
    </Stack>
  );
}
