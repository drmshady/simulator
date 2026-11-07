import {
  ActionIcon,
  Alert,
  Button,
  Group,
  List,
  Modal,
  SegmentedControl,
  Select,
  Stack,
  Text,
  TextInput,
} from '@mantine/core';
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router';

import { isStudioError } from '../api/client';
import { createStudy } from '../api/studio';
import type { BlockTypeEntry, DetailLevel, ScenarioView } from '../api/types';
import { formatAxisValue, parseValues, runCount, settingOptions, type SettingOption } from '../study';

export interface StudyDialogProps {
  opened: boolean;
  onClose: () => void;
  scenario: ScenarioView;
  types: readonly BlockTypeEntry[];
  /** Open on a sensitivity study of this library setting (the sensitivity button of a library field). */
  sensitivity?: string | null;
}

interface AxisDraft {
  path: string | null;
  text: string;
}

const LEVELS: { value: Exclude<DetailLevel, 'preview'>; label: string }[] = [
  { value: 'standard', label: 'Standard' },
  { value: 'fine', label: 'Fine' },
  { value: 'extra_fine', label: 'Extra fine' },
];

function selectData(options: readonly SettingOption[]) {
  const groups = new Map<string, { value: string; label: string }[]>();
  for (const option of options) {
    const items = groups.get(option.group) ?? [];
    items.push({ value: option.value, label: option.label + (option.unit ? ` (${option.unit})` : '') });
    groups.set(option.group, items);
  }
  return [...groups].map(([group, items]) => ({ group, items }));
}

/** Queue a study of the scenario (FR-020, FR-021): a sweep over the settings and values chosen, or the minimum,
 * typical and maximum runs of one library setting. The studio checks every run and estimates the whole study before
 * anything is queued; the runs then go one at a time, overnight if need be. */
export function StudyDialog({ opened, onClose, scenario, types, sensitivity }: StudyDialogProps) {
  const navigate = useNavigate();
  const options = useMemo(() => settingOptions(scenario.scenario, types), [scenario.scenario, types]);
  const libraryOptions = options.filter((o) => o.kind === 'library');
  const [kind, setKind] = useState<'sweep' | 'sensitivity'>(sensitivity ? 'sensitivity' : 'sweep');
  const [parameter, setParameter] = useState<string | null>(sensitivity ?? null);
  const [axes, setAxes] = useState<AxisDraft[]>([{ path: null, text: '' }]);
  const [name, setName] = useState('');
  const [level, setLevel] = useState<Exclude<DetailLevel, 'preview'>>('standard');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ message: string; problems: string[] } | null>(null);

  const parsed = axes.map((axis) => {
    const option = options.find((o) => o.value === axis.path);
    return { axis, option, ...(option ? parseValues(axis.text, option) : { values: [], error: null }) };
  });
  const complete = parsed.filter((p) => p.option && !p.error && p.values.length);
  const runs = kind === 'sensitivity' ? (parameter ? 3 : 0) : runCount(complete);
  const loadOnly = kind === 'sweep' && complete.some((p) => p.option?.loadOnly);
  const ready =
    kind === 'sensitivity' ? parameter !== null : complete.length === axes.length && new Set(axes.map((a) => a.path)).size === axes.length;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const study = await createStudy({
        name: name.trim(),
        base_scenario_id: scenario.id,
        project_id: scenario.project_id,
        kind,
        detail_level: level,
        axes: kind === 'sweep' ? complete.map((p) => ({ path: p.option!.value, values: p.values })) : [],
        parameter: kind === 'sensitivity' ? parameter : null,
      });
      onClose();
      void navigate(`/studies?id=${study.id}`);
    } catch (e) {
      setError({
        message: e instanceof Error ? e.message : String(e),
        problems: isStudioError(e) ? e.problems.map((p) => p.message) : [],
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal opened={opened} onClose={onClose} title={`Queue a study of ${scenario.name}`} size="lg">
      <Stack gap="sm">
        <SegmentedControl
          value={kind}
          onChange={(value) => setKind(value as 'sweep' | 'sensitivity')}
          data={[
            { value: 'sweep', label: 'Vary settings' },
            { value: 'sensitivity', label: 'Sensitivity (min, typical, max)' },
          ]}
        />
        {kind === 'sweep' ? (
          <Stack gap="xs">
            <Text size="sm" c="dimmed">
              Choose each setting to vary and type its values, separated by commas. Every combination becomes one run.
            </Text>
            {parsed.map(({ axis, option, error: problem }, index) => (
              <Group key={index} align="flex-start" gap="xs" wrap="nowrap">
                <Select
                  aria-label={`Setting ${index + 1}`}
                  placeholder="Setting"
                  data={selectData(options)}
                  value={axis.path}
                  searchable
                  onChange={(path) => setAxes(axes.map((a, i) => (i === index ? { ...a, path } : a)))}
                  style={{ flex: 1 }}
                />
                <TextInput
                  aria-label={`Values ${index + 1}`}
                  placeholder={option ? `now ${formatAxisValue(option.current)}` : 'values'}
                  value={axis.text}
                  error={axis.text ? problem : null}
                  description={option?.choices ? option.choices.join(', ') : undefined}
                  onChange={(e) => {
                    const text = e.currentTarget.value;
                    setAxes(axes.map((a, i) => (i === index ? { ...a, text } : a)));
                  }}
                  style={{ flex: 1 }}
                />
                <ActionIcon
                  variant="subtle"
                  color="red"
                  aria-label={`Remove setting ${index + 1}`}
                  disabled={axes.length === 1}
                  onClick={() => setAxes(axes.filter((_, i) => i !== index))}
                  mt={4}
                >
                  ✕
                </ActionIcon>
              </Group>
            ))}
            <Group>
              <Button size="xs" variant="subtle" onClick={() => setAxes([...axes, { path: null, text: '' }])}>
                Add a setting
              </Button>
            </Group>
          </Stack>
        ) : (
          <Stack gap="xs">
            <Text size="sm" c="dimmed">
              Three runs: the minimum, typical and maximum of the approved library range. The summary shows how much
              each key number changes across it.
            </Text>
            <Select
              label="Library setting"
              data={selectData(libraryOptions)}
              value={parameter}
              searchable
              onChange={setParameter}
            />
          </Stack>
        )}
        <Group grow>
          <TextInput label="Name" placeholder="Optional" value={name} onChange={(e) => setName(e.currentTarget.value)} />
          <Select
            label="Detail level"
            data={LEVELS}
            value={level}
            allowDeselect={false}
            onChange={(value) => value && setLevel(value as Exclude<DetailLevel, 'preview'>)}
          />
        </Group>
        <Text size="sm">
          {runs ? `${runs} run${runs === 1 ? '' : 's'}, solved one at a time on the main PC.` : 'No runs yet.'}
          {loadOnly && ' Runs that differ only in a load\'s force or angle reuse one solve.'}
        </Text>
        {error && (
          <Alert color="red" title="Not queued">
            <Text size="sm">{error.message}</Text>
            {error.problems.length > 0 && (
              <List size="xs" mt={4}>
                {error.problems.map((p) => (
                  <List.Item key={p}>{p}</List.Item>
                ))}
              </List>
            )}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={!ready || runs === 0} loading={busy}>
            Check and queue {runs || ''} run{runs === 1 ? '' : 's'}
          </Button>
        </Group>
        {busy && (
          <Text size="xs" c="dimmed">
            Checking every run and estimating the study; this takes a few seconds per distinct model.
          </Text>
        )}
      </Stack>
    </Modal>
  );
}
