import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Checkbox,
  Grid,
  Group,
  Modal,
  NavLink,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';

import {
  annotateRun,
  annotateScenario,
  createProject,
  duplicateScenario,
  keys,
  startBackup,
  startRestore,
  updateProject,
  useProject,
  useProjects,
  useSearch,
  type SearchQuery,
} from '../api/studio';
import { SYSTEM_QUERY_KEY, useSystemInfo } from '../api/system';
import type { ProjectView, RunSummary, ScenarioSummary, SearchHit } from '../api/types';
import { NotesEditor } from '../components/NotesEditor';
import { TeachingNotice } from '../components/TeachingNotice';

const RUN_COLOUR: Record<string, string> = {
  succeeded: 'green',
  failed: 'red',
  cancelled: 'gray',
  running: 'blue',
  queued: 'blue',
  interrupted: 'orange',
};

function when(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '–';
}

function Tags({ tags }: { tags: string[] }) {
  return (
    <Group gap={4}>
      {tags.map((tag) => (
        <Badge key={tag} size="xs" variant="outline">
          {tag}
        </Badge>
      ))}
    </Group>
  );
}

// --- search -----------------------------------------------------------------------------------------------------------

function HitList({ title, hits, link }: { title: string; hits: SearchHit[]; link: (hit: SearchHit) => string }) {
  if (!hits.length) return null;
  return (
    <Stack gap={4}>
      <Text size="sm" fw={600}>
        {title} ({hits.length})
      </Text>
      {hits.map((hit) => (
        <Stack key={hit.id} gap={0}>
          <Group gap="xs">
            <Anchor component={Link} to={link(hit)} size="sm">
              {hit.name || hit.id}
            </Anchor>
            {hit.status && (
              <Badge size="xs" variant="light" color={RUN_COLOUR[hit.status]}>
                {hit.status}
              </Badge>
            )}
            {hit.detail_level && hit.kind === 'run' && <Text size="xs">{hit.detail_level}</Text>}
            <Tags tags={hit.tags} />
          </Group>
          {hit.snippet && (
            <Text size="xs" c="dimmed">
              {hit.snippet}
            </Text>
          )}
        </Stack>
      ))}
    </Stack>
  );
}

/** Search by text, tag and setting value across projects, scenarios and runs (FR-031). */
function Search() {
  const [query, setQuery] = useState<SearchQuery>({ q: '', tag: '', setting: '' });
  const [debounced] = useDebouncedValue(query, 300);
  const search = useSearch(debounced);
  const set = (key: keyof SearchQuery) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setQuery({ ...query, [key]: e.currentTarget.value });
  const data = search.data;
  const empty = data && !data.projects.length && !data.scenarios.length && !data.runs.length;
  return (
    <Card withBorder padding="sm">
      <Stack gap="sm">
        <Group grow gap="xs" align="flex-start">
          <TextInput size="xs" label="Text" placeholder="Names, notes and tags" value={query.q} onChange={set('q')} />
          <TextInput size="xs" label="Tag" placeholder="An exact tag" value={query.tag} onChange={set('tag')} />
          <TextInput
            size="xs"
            label="Setting value"
            placeholder="implant-1.length:=:8"
            description="block.setting:op:value with =, <, >, <= or >=; implant.length matches every implant"
            value={query.setting}
            onChange={set('setting')}
          />
        </Group>
        {search.error && (
          <Text size="xs" c="red">
            {search.error.message}
          </Text>
        )}
        {empty && (
          <Text size="xs" c="dimmed">
            Nothing matches.
          </Text>
        )}
        {data && (
          <Grid gap="md">
            <Grid.Col span={{ base: 12, md: 4 }}>
              <HitList title="Projects" hits={data.projects} link={(h) => `/projects?project=${h.id}`} />
            </Grid.Col>
            <Grid.Col span={{ base: 12, md: 4 }}>
              <HitList title="Scenarios" hits={data.scenarios} link={(h) => `/builder?scenario=${h.id}`} />
            </Grid.Col>
            <Grid.Col span={{ base: 12, md: 4 }}>
              <HitList title="Runs" hits={data.runs} link={(h) => `/results?run=${h.id}`} />
            </Grid.Col>
          </Grid>
        )}
      </Stack>
    </Card>
  );
}

// --- one project ------------------------------------------------------------------------------------------------------

type Editing = { kind: 'scenario'; item: ScenarioSummary } | { kind: 'run'; item: RunSummary } | null;

function ScenarioTable({
  project,
  onEdit,
  onDuplicate,
}: {
  project: ProjectView;
  onEdit: (s: ScenarioSummary) => void;
  onDuplicate: (s: ScenarioSummary) => void;
}) {
  const names = new Map(project.scenarios.map((s) => [s.id, s.name]));
  if (!project.scenarios.length) return <Text size="sm" c="dimmed">No scenarios yet: start one in the builder.</Text>;
  return (
    <Table striped withTableBorder fz="xs">
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Scenario</Table.Th>
          <Table.Th>Tags</Table.Th>
          <Table.Th>Notes</Table.Th>
          <Table.Th>Changed</Table.Th>
          <Table.Th />
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {project.scenarios.map((s) => (
          <Table.Tr key={s.id}>
            <Table.Td>
              <Anchor component={Link} to={`/builder?scenario=${s.id}`} size="xs" fw={600}>
                {s.name}
              </Anchor>
              {s.valid === false && (
                <Badge size="xs" color="red" variant="light" ml={4}>
                  not valid
                </Badge>
              )}
              {s.derived_from?.kind === 'scenario' && (
                <Text size="xs" c="dimmed">
                  duplicate of {names.get(s.derived_from.id) ?? s.derived_from.id}
                </Text>
              )}
            </Table.Td>
            <Table.Td>
              <Tags tags={s.tags} />
            </Table.Td>
            <Table.Td maw={260}>
              <Text size="xs" lineClamp={2}>
                {s.notes}
              </Text>
            </Table.Td>
            <Table.Td>{when(s.updated_at)}</Table.Td>
            <Table.Td>
              <Group gap={4} wrap="nowrap">
                <Button size="compact-xs" variant="light" onClick={() => onDuplicate(s)}>
                  Duplicate and change
                </Button>
                <Button size="compact-xs" variant="subtle" onClick={() => onEdit(s)}>
                  Notes
                </Button>
              </Group>
            </Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

function RunTable({ runs, onEdit }: { runs: RunSummary[]; onEdit: (r: RunSummary) => void }) {
  const navigate = useNavigate();
  const [selected, setSelected] = useState<string[]>([]);
  if (!runs.length) return <Text size="sm" c="dimmed">No runs yet.</Text>;
  const toggle = (id: string) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  return (
    <Stack gap="xs">
      <Group gap="xs">
        <Button
          size="xs"
          disabled={selected.length < 2}
          onClick={() => void navigate(`/compare?runs=${selected.join(',')}`)}
        >
          Compare {selected.length >= 2 ? `${selected.length} runs` : 'the chosen runs'}
        </Button>
        <Text size="xs" c="dimmed">
          Tick two or more finished runs; the first ticked is the base.
        </Text>
      </Group>
      <Table striped withTableBorder fz="xs">
        <Table.Thead>
          <Table.Tr>
            <Table.Th />
            <Table.Th>Run</Table.Th>
            <Table.Th>Level</Table.Th>
            <Table.Th>Status</Table.Th>
            <Table.Th>Finished</Table.Th>
            <Table.Th>Tags</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {runs.map((r) => (
            <Table.Tr key={r.id}>
              <Table.Td>
                <Checkbox
                  size="xs"
                  aria-label={`Compare ${r.name}`}
                  disabled={r.status !== 'succeeded'}
                  checked={selected.includes(r.id)}
                  onChange={() => toggle(r.id)}
                />
              </Table.Td>
              <Table.Td>
                <Anchor component={Link} to={`/results?run=${r.id}`} size="xs">
                  {r.name}
                </Anchor>
                {selected[0] === r.id && (
                  <Badge size="xs" ml={4}>
                    base
                  </Badge>
                )}
                {r.notes && (
                  <Text size="xs" c="dimmed" lineClamp={1}>
                    {r.notes}
                  </Text>
                )}
              </Table.Td>
              <Table.Td>{r.detail_level}</Table.Td>
              <Table.Td>
                <Badge size="xs" variant="light" color={RUN_COLOUR[r.status]}>
                  {r.status}
                </Badge>
                {r.flags.map((flag) => (
                  <Badge key={flag} size="xs" color="red" variant="outline" ml={4}>
                    {flag.replace(/_/g, ' ')}
                  </Badge>
                ))}
              </Table.Td>
              <Table.Td>{when(r.finished_at)}</Table.Td>
              <Table.Td>
                <Tags tags={r.tags} />
              </Table.Td>
              <Table.Td>
                <Button size="compact-xs" variant="subtle" onClick={() => onEdit(r)}>
                  Notes
                </Button>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Stack>
  );
}

function ProjectDetail({ project }: { project: ProjectView }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState(project.name);
  const [editing, setEditing] = useState<Editing>(null);
  const [error, setError] = useState<string | null>(null);
  const suggestions = useMemo(
    () => [...new Set([...project.tags, ...project.scenarios.flatMap((s) => s.tags), ...project.runs.flatMap((r) => r.tags)])],
    [project],
  );
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: keys.project(project.id) });
    void queryClient.invalidateQueries({ queryKey: keys.projects });
    void queryClient.invalidateQueries({ queryKey: ['search'] });
  };
  const duplicate = async (s: ScenarioSummary) => {
    setError(null);
    try {
      const created = await duplicateScenario(project.id, s.id);
      refresh();
      void navigate(`/builder?scenario=${created.scenario.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Stack gap="md">
      <Group gap="xs" align="flex-end">
        <TextInput
          label="Project"
          value={name}
          onChange={(e) => setName(e.currentTarget.value)}
          style={{ flex: 1 }}
          styles={{ input: { fontWeight: 600 } }}
        />
        <Button
          variant="light"
          disabled={!name.trim() || name === project.name}
          onClick={() => void updateProject(project.id, { name: name.trim() }).then(refresh)}
        >
          Rename
        </Button>
      </Group>
      <NotesEditor
        key={`${project.id}-${project.updated_at}`}
        notes={project.notes}
        tags={project.tags}
        suggestions={suggestions}
        onSave={(notes, tags) => updateProject(project.id, { notes, tags }).then(refresh)}
      />
      {error && <Alert color="red">{error}</Alert>}
      <Stack gap="xs">
        <Title order={2} size="h4">
          Scenarios
        </Title>
        <ScenarioTable project={project} onEdit={(item) => setEditing({ kind: 'scenario', item })} onDuplicate={(s) => void duplicate(s)} />
      </Stack>
      <Stack gap="xs">
        <Title order={2} size="h4">
          Runs
        </Title>
        <RunTable runs={project.runs} onEdit={(item) => setEditing({ kind: 'run', item })} />
      </Stack>
      <Stack gap={4}>
        <Title order={2} size="h4">
          Studies
        </Title>
        {project.studies.length ? (
          project.studies.map((s) => (
            <Text key={s.id} size="xs">
              <Anchor component={Link} to={`/studies?id=${s.id}`} size="xs">
                {s.name || `${s.kind} study`}
              </Anchor>{' '}
              of {project.scenarios.find((x) => x.id === s.base_scenario_id)?.name ?? s.base_scenario_id}: {s.runs} runs,{' '}
              {s.status.replace(/_/g, ' ')} ({when(s.created_at)})
            </Text>
          ))
        ) : (
          <Text size="sm" c="dimmed">
            No studies yet.
          </Text>
        )}
      </Stack>
      <Modal
        opened={editing !== null}
        onClose={() => setEditing(null)}
        title={editing ? `Notes and tags: ${editing.item.name}` : ''}
      >
        {editing && (
          <NotesEditor
            notes={editing.item.notes ?? ''}
            tags={editing.item.tags ?? []}
            suggestions={suggestions}
            onSave={(notes, tags) =>
              (editing.kind === 'scenario'
                ? annotateScenario(editing.item.id, { notes, tags })
                : annotateRun(editing.item.id, { notes, tags })
              ).then(() => {
                refresh();
                setEditing(null);
              })
            }
          />
        )}
      </Modal>
    </Stack>
  );
}

// --- backup -----------------------------------------------------------------------------------------------------------

/** Back up and restore everything (FR-034): both are queued in the heavy lane and run on the main PC. */
function Backup() {
  const system = useSystemInfo();
  const queryClient = useQueryClient();
  const [folder, setFolder] = useState('');
  const [archive, setArchive] = useState('');
  const [confirm, setConfirm] = useState(false);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);
  const last = system.data?.last_backup;
  const restored = system.data?.last_restore;
  const run = async (start: () => Promise<{ job_id: string }>, what: string) => {
    setMessage(null);
    try {
      const job = await start();
      setMessage({ text: `${what} queued (job ${job.job_id}); it runs after the current heavy job. Follow it on the Queue page.`, error: false });
      void queryClient.invalidateQueries({ queryKey: SYSTEM_QUERY_KEY });
    } catch (e) {
      setMessage({ text: e instanceof Error ? e.message : String(e), error: true });
    }
  };
  return (
    <Card withBorder padding="sm">
      <Stack gap="xs">
        <Text fw={600}>Backup</Text>
        <Text size="xs" c="dimmed">
          One archive of the database, the results store, published media and the library, with a SHA-256 for every
          file. Folders and files are on the main PC.
        </Text>
        <Text size="xs">
          {last
            ? `Last backup ${when(last.created_at)}: ${last.archive} (${last.files} files, ${(last.size_bytes / 1024 ** 3).toFixed(2)} GB)`
            : 'No backup made from the studio yet.'}
        </Text>
        <Group gap="xs" align="flex-end">
          <TextInput
            size="xs"
            label="Folder"
            placeholder="Default: the data folder's backups folder"
            value={folder}
            onChange={(e) => setFolder(e.currentTarget.value)}
            style={{ flex: 1 }}
          />
          <Button size="xs" onClick={() => void run(() => startBackup(folder.trim() || null), 'Backup')}>
            Back up now
          </Button>
        </Group>
        <Group gap="xs" align="flex-end">
          <TextInput
            size="xs"
            label="Restore from"
            placeholder="E:\Backups\dentalsim-backup-….zip"
            value={archive}
            onChange={(e) => setArchive(e.currentTarget.value)}
            style={{ flex: 1 }}
          />
          <Button size="xs" color="red" variant="light" disabled={!archive.trim()} onClick={() => setConfirm(true)}>
            Restore…
          </Button>
        </Group>
        {restored && (
          <Text size="xs" c="dimmed">
            Last restore {when(restored.restored_at)} from {restored.archive}; the database before it is kept at{' '}
            {restored.previous_database}.
          </Text>
        )}
        {message && (
          <Text size="xs" c={message.error ? 'red' : undefined}>
            {message.text}
          </Text>
        )}
      </Stack>
      <Modal opened={confirm} onClose={() => setConfirm(false)} title="Restore this backup?">
        <Stack gap="sm">
          <Text size="sm">
            Every project, scenario, run, library entry and setting goes back to how it was in the backup, including the
            studio password. Every file is checked against its SHA-256 first; if one does not match, nothing changes.
            The current database is kept beside the backups, and no stored result is deleted.
          </Text>
          <Text size="sm" ff="monospace">
            {archive}
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setConfirm(false)}>
              Cancel
            </Button>
            <Button
              color="red"
              onClick={() => {
                setConfirm(false);
                void run(() => startRestore(archive.trim()), 'Restore');
              }}
            >
              Restore
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Card>
  );
}

// --- the page ---------------------------------------------------------------------------------------------------------

/** Projects (Story 3, FR-031 to FR-034): the project list and each project's notes, tags, scenarios, runs and
 * studies; search by text, tag and setting value; duplicate and change; choosing runs to compare; backup. */
export default function Projects() {
  const [params, setParams] = useSearchParams();
  const projectId = params.get('project');
  const projects = useProjects();
  const project = useProject(projectId);
  const queryClient = useQueryClient();
  const [newName, setNewName] = useState('');

  const create = async () => {
    const created = await createProject(newName.trim());
    setNewName('');
    void queryClient.invalidateQueries({ queryKey: keys.projects });
    setParams({ project: created.id });
  };

  return (
    <Stack gap="md">
      <Title order={1} size="h2">
        Projects
      </Title>
      <Search />
      <Grid gap="md">
        <Grid.Col span={{ base: 12, md: 3 }}>
          <Stack gap="sm">
            <Card withBorder padding="xs">
              {(projects.data ?? []).map((p) => (
                <NavLink
                  key={p.id}
                  label={p.name}
                  description={p.tags.join(', ') || undefined}
                  active={p.id === projectId}
                  onClick={() => setParams({ project: p.id })}
                />
              ))}
              {projects.data?.length === 0 && (
                <Text size="sm" c="dimmed" p="xs">
                  No projects yet.
                </Text>
              )}
            </Card>
            <Group gap="xs" align="flex-end">
              <TextInput size="xs" label="New project" value={newName} onChange={(e) => setNewName(e.currentTarget.value)} style={{ flex: 1 }} />
              <Button size="xs" disabled={!newName.trim()} onClick={() => void create()}>
                Create
              </Button>
            </Group>
            <Backup />
          </Stack>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 9 }}>
          {!projectId && <Text c="dimmed">Choose a project, or find one with the search above.</Text>}
          {project.error && <Alert color="red">{project.error.message}</Alert>}
          {project.data && <ProjectDetail key={project.data.id} project={project.data} />}
        </Grid.Col>
      </Grid>
      <TeachingNotice variant="compact" />
    </Stack>
  );
}
