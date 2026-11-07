import {
  Alert,
  Badge,
  Button,
  Card,
  Grid,
  Group,
  Modal,
  NavLink,
  NumberInput,
  Select,
  Stack,
  Tabs,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';

import { apiPost, isStudioError } from '../api/client';
import {
  createProject,
  createScenario,
  keys,
  saveScenario,
  scenarioGeometryUrl,
  useBlocks,
  useProjects,
  useScenario,
  useTemplates,
  validateScenario,
} from '../api/studio';
import type { BlockTypeEntry, Placement, Scenario, ScenarioView, ValidityProblem, ValidityResult } from '../api/types';
import { addBlock, blockTree, moveBlock, removeBlock, socketsFor, updateBlock, upgradeBlocks, ZERO_PLACEMENT } from '../builder';
import { EstimateDialog } from '../components/EstimateDialog';
import { LoadEditor } from '../components/LoadEditor';
import { PreviewPanel } from '../components/PreviewPanel';
import { SettingsForm } from '../components/SettingsForm';
import { TeachingNotice } from '../components/TeachingNotice';
import { BuilderScene } from '../viewer/BuilderScene';
import { loadArrows } from '../viewer/loadArrows';
import { Scene } from '../viewer/Scene';

const SAVE_DELAY_MS = 500;
const FULL_CHECK_DELAY_MS = 1500;

// --- a new scenario from a template ---------------------------------------------------------------------------------

function NewScenario({ onCreated }: { onCreated: (id: string) => void }) {
  const projects = useProjects();
  const templates = useTemplates();
  const [project, setProject] = useState<string | null>(null);
  const [newProject, setNewProject] = useState('');
  const [template, setTemplate] = useState<string | null>('single_crown_implant');
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const projectId = project ?? (await createProject(newProject || 'My scenarios')).id;
      const created = await createScenario(projectId, template!, { name: name || undefined });
      onCreated(created.scenario.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Stack maw={560} gap="md">
      <Title order={1} size="h2">
        New scenario
      </Title>
      <Select
        label="Project"
        placeholder="A new project"
        data={(projects.data ?? []).map((p) => ({ value: p.id, label: p.name }))}
        value={project}
        onChange={setProject}
        clearable
      />
      {!project && <TextInput label="New project name" value={newProject} onChange={(e) => setNewProject(e.currentTarget.value)} />}
      <Select
        label="Template"
        data={(templates.data ?? []).map((t) => ({
          value: t.id,
          label: `${t.name}${t.approval === 'proposed' ? ' (defaults awaiting approval)' : ''}`,
        }))}
        value={template}
        onChange={setTemplate}
        allowDeselect={false}
      />
      <Text size="xs" c="dimmed">
        {templates.data?.find((t) => t.id === template)?.description}
      </Text>
      <TextInput label="Scenario name" placeholder="The template's name" value={name} onChange={(e) => setName(e.currentTarget.value)} />
      {error && <Alert color="red">{error}</Alert>}
      <Group>
        <Button onClick={() => void create()} loading={busy} disabled={!template}>
          Create
        </Button>
      </Group>
    </Stack>
  );
}

// --- placement ------------------------------------------------------------------------------------------------------

function PlacementControls({ placement, onChange }: { placement: Placement; onChange: (p: Placement) => void }) {
  const [x, y, z] = placement.position_mm ?? [0, 0, 0];
  const angles = placement.axis_angles_deg ?? { mesiodistal: 0, buccolingual: 0 };
  const number = (label: string, value: number, set: (v: number) => void, limit?: number) => (
    <NumberInput
      size="xs"
      label={label}
      value={value}
      step={label.includes('°') ? 1 : 0.1}
      min={limit !== undefined ? -limit : undefined}
      max={limit}
      decimalScale={2}
      onChange={(next) => typeof next === 'number' && set(next)}
    />
  );
  return (
    <Stack gap="xs">
      <Text size="sm" fw={600}>
        Placement
      </Text>
      <Text size="xs" c="dimmed">
        Offsets along the axes of the socket the block sits on (for blocks on a site of the jaw: x along the arch,
        mesial, or distal past the midline of a full arch; y buccal; z occlusal).
        You can also drag the selected block in the 3D view.
      </Text>
      <Group grow gap="xs">
        {number('x (mm)', x, (v) => onChange({ ...placement, position_mm: [v, y, z] }))}
        {number('y (mm)', y, (v) => onChange({ ...placement, position_mm: [x, v, z] }))}
        {number('z (mm)', z, (v) => onChange({ ...placement, position_mm: [x, y, v] }))}
      </Group>
      <Group grow gap="xs">
        {number('Mesiodistal tilt (°)', angles.mesiodistal ?? 0, (v) => onChange({ ...placement, axis_angles_deg: { ...angles, mesiodistal: v } }), 45)}
        {number('Buccolingual tilt (°)', angles.buccolingual ?? 0, (v) => onChange({ ...placement, axis_angles_deg: { ...angles, buccolingual: v } }), 45)}
        {number('Rotation (°)', placement.rotation_deg ?? 0, (v) => onChange({ ...placement, rotation_deg: v }))}
      </Group>
    </Stack>
  );
}

// --- adding blocks --------------------------------------------------------------------------------------------------

function AddBlock({ scenario, types, onAdd }: { scenario: Scenario; types: BlockTypeEntry[]; onAdd: (s: Scenario) => void }) {
  const [typeId, setTypeId] = useState<string | null>(null);
  const [socket, setSocket] = useState<string | null>(null);
  const type = types.find((t) => t.type_id === typeId);
  const sockets = type ? socketsFor(scenario, type, types) : [];
  return (
    <Card withBorder padding="sm">
      <Stack gap="xs">
        <Text size="sm" fw={600}>
          Add a block
        </Text>
        <Select
          size="xs"
          placeholder="Block type"
          data={types
            .filter((t) => t.attaches_to.length > 0)
            .map((t) => ({ value: t.type_id, label: `${t.label}${t.status === 'ready' ? '' : ' (awaiting benchmark)'}` }))}
          value={typeId}
          onChange={(next) => {
            setTypeId(next);
            setSocket(null);
          }}
        />
        {type && type.status !== 'ready' && (
          <Badge color="orange" variant="light">
            awaiting benchmark: {type.awaiting.join(', ')}
          </Badge>
        )}
        {type && (
          <Select
            size="xs"
            placeholder={sockets.length ? 'Attach to' : 'Nothing offers a socket it fits'}
            data={sockets.map((s) => ({ value: `${s.instanceId}|${s.socket}`, label: s.label }))}
            value={socket}
            onChange={setSocket}
          />
        )}
        <Button
          size="xs"
          disabled={!type || !socket}
          onClick={() => {
            const [parent, point] = socket!.split('|');
            onAdd(addBlock(scenario, type!, parent!, point!));
            setTypeId(null);
            setSocket(null);
          }}
        >
          Add
        </Button>
      </Stack>
    </Card>
  );
}

// --- problems -------------------------------------------------------------------------------------------------------

function Problems({ validity, misfits }: { validity: ValidityResult | null; misfits: ValidityProblem[] }) {
  const problems = [...(validity?.problems ?? []), ...misfits.filter((m) => !validity?.problems.some((p) => p.message === m.message))];
  if (!validity) return <Text size="sm" c="dimmed">Checking…</Text>;
  if (!problems.length)
    return (
      <Badge color="green" variant="light">
        valid
      </Badge>
    );
  return (
    <Stack gap={4}>
      {problems.map((p, index) => (
        <Alert key={`${p.code}-${index}`} color={p.severity === 'warning' ? 'yellow' : 'red'} p="xs">
          <Text size="xs" fw={600}>
            {p.code.replace(/_/g, ' ')}
            {p.block ? ` (${p.block})` : ''}
          </Text>
          <Text size="xs">{p.message}</Text>
          {p.location_mm && (
            <Text size="xs" c="dimmed">
              at ({p.location_mm.map((c) => c.toFixed(1)).join(', ')}) mm, marked in the 3D view
            </Text>
          )}
        </Alert>
      ))}
    </Stack>
  );
}

// --- the builder ----------------------------------------------------------------------------------------------------

function SaveAsTemplate({ scenarioId, opened, onClose }: { scenarioId: string; opened: boolean; onClose: () => void }) {
  const [name, setName] = useState('');
  const [id, setId] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const save = async () => {
    try {
      await apiPost('/api/templates', { scenario_id: scenarioId, name, template_id: id, branch: 'implant' });
      setMessage('Saved as an owner template.');
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <Modal opened={opened} onClose={onClose} title="Save as an owner template">
      <Stack gap="sm">
        <TextInput label="Name" value={name} onChange={(e) => setName(e.currentTarget.value)} />
        <TextInput label="Template id" description="Letters, digits, - and _" value={id} onChange={(e) => setId(e.currentTarget.value)} />
        {message && <Text size="sm">{message}</Text>}
        <Group justify="flex-end">
          <Button onClick={() => void save()} disabled={!name || !/^[A-Za-z0-9_-]{1,60}$/.test(id)}>
            Save
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function Editor({ view: loaded }: { view: ScenarioView }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const blocks = useBlocks();
  const [view, setView] = useState<ScenarioView>(loaded);
  const [draft, setDraft] = useState<Scenario>(loaded.scenario);
  const [selected, setSelected] = useState<string | null>(loaded.scenario.blocks[1]?.instance_id ?? null);
  const [status, setStatus] = useState<string>(`saved, revision ${loaded.revision}`);
  const [conflict, setConflict] = useState<string | null>(null);
  const [savedRevision, setSavedRevision] = useState(loaded.revision);
  const [fullRevision, setFullRevision] = useState(loaded.revision);
  const [estimateOpen, setEstimateOpen] = useState(false);
  const [templateOpen, setTemplateOpen] = useState(false);
  const revision = useRef(loaded.revision);
  const pending = useRef<ReturnType<typeof setTimeout> | null>(null);
  const fullCheck = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Validity on every saved change: the fast checks at once, the full ones after a pause (FR-011).
  const fast = useQuery({
    queryKey: ['validity', 'fast', loaded.id, savedRevision],
    queryFn: () => validateScenario(loaded.id, 'fast'),
  });
  const full = useQuery({
    queryKey: ['validity', 'full', loaded.id, fullRevision],
    queryFn: () => validateScenario(loaded.id, 'full'),
  });
  const validity: ValidityResult | null =
    fullRevision === savedRevision && full.data ? full.data : (fast.data ?? loaded.validity ?? null);
  const scene = useQuery({
    queryKey: ['geometry', loaded.id, savedRevision],
    queryFn: ({ signal }) => scenarioGeometryUrl(loaded.id, signal),
    retry: false,
    // The last scene stays in view while the changed one is drawn.
    placeholderData: keepPreviousData,
  });
  const geometryUrl = scene.data ?? null;
  const arrows = useMemo(() => loadArrows(draft.loads ?? [], view.occlusal_points), [draft.loads, view.occlusal_points]);

  const change = (next: Scenario) => {
    setDraft(next);
    setStatus('unsaved changes');
    if (pending.current) clearTimeout(pending.current);
    pending.current = setTimeout(async () => {
      setStatus('saving…');
      try {
        const saved = await saveScenario(loaded.id, revision.current, next);
        revision.current = saved.revision;
        setView(saved);
        setDraft(saved.scenario);
        setStatus(`saved, revision ${saved.revision}`);
        queryClient.setQueryData(keys.scenario(loaded.id), saved);
        setSavedRevision(saved.revision);
        if (fullCheck.current) clearTimeout(fullCheck.current);
        fullCheck.current = setTimeout(() => setFullRevision(saved.revision), FULL_CHECK_DELAY_MS);
      } catch (e) {
        if (isStudioError(e, 'conflict')) setConflict(e.message);
        else setStatus(e instanceof Error ? `not saved: ${e.message}` : 'not saved');
      }
    }, SAVE_DELAY_MS);
  };

  const types = blocks.data ?? [];
  const rows = blockTree(draft);
  const selectedBlock = draft.blocks.find((b) => b.instance_id === selected);
  const selectedType = types.find((t) => t.type_id === selectedBlock?.type_id && t.version === selectedBlock?.type_version) ??
    types.find((t) => t.type_id === selectedBlock?.type_id);
  const problemBlocks = useMemo(
    () => new Set((validity?.problems ?? []).map((p) => p.block).filter((b): b is string => Boolean(b))),
    [validity],
  );

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Stack gap={0}>
          <Title order={1} size="h2">
            {view.name}
          </Title>
          <Text size="xs" c="dimmed">
            {status}
            {view.derived_from ? ` · from ${view.derived_from.kind} ${view.derived_from.id}` : ''}
          </Text>
        </Stack>
        <Group gap="xs">
          <Button variant="default" onClick={() => setTemplateOpen(true)}>
            Save as template
          </Button>
          <Button onClick={() => setEstimateOpen(true)} disabled={!validity?.ok}>
            Run in detail…
          </Button>
        </Group>
      </Group>
      {conflict && (
        <Alert color="red" title="Changed elsewhere">
          <Text size="sm">{conflict}</Text>
          <Button size="xs" mt="xs" onClick={() => window.location.reload()}>
            Reload the scenario
          </Button>
        </Alert>
      )}
      {view.upgrades.length > 0 && (
        <Alert color="blue" title="Newer block versions">
          <Text size="sm">
            {view.upgrades.map((u) => `${u.instance_id}: ${u.from_version} → ${u.to_version}`).join('; ')}
          </Text>
          <Button size="xs" mt="xs" onClick={() => change(upgradeBlocks(draft, view.upgrades))}>
            Upgrade
          </Button>
        </Alert>
      )}
      <Grid gap="md">
        <Grid.Col span={{ base: 12, md: 3 }}>
          <Stack gap="sm">
            <Card withBorder padding="xs">
              {rows.map(({ block, depth }) => (
                <NavLink
                  key={block.instance_id}
                  label={block.instance_id}
                  description={types.find((t) => t.type_id === block.type_id)?.label}
                  active={block.instance_id === selected}
                  onClick={() => setSelected(block.instance_id)}
                  pl={8 + depth * 14}
                  rightSection={problemBlocks.has(block.instance_id) ? <Badge color="red" size="xs">!</Badge> : undefined}
                />
              ))}
            </Card>
            <AddBlock scenario={draft} types={types} onAdd={change} />
            {selectedBlock && selectedBlock.attach_to && (
              <Button
                size="xs"
                color="red"
                variant="subtle"
                onClick={() => {
                  change(removeBlock(draft, selectedBlock.instance_id));
                  setSelected(null);
                }}
              >
                Remove {selectedBlock.instance_id} and what is attached to it
              </Button>
            )}
          </Stack>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 5 }}>
          <Stack gap="sm">
            {geometryUrl ? (
              <Scene height={460} fit={false}>
                <BuilderScene
                  url={geometryUrl}
                  selected={selected}
                  movable={Boolean(selectedBlock?.attach_to)}
                  problemBlocks={problemBlocks}
                  problems={validity?.problems ?? []}
                  loads={arrows}
                  onSelect={setSelected}
                  onMove={(id, delta) => change(moveBlock(draft, id, delta))}
                />
              </Scene>
            ) : scene.isError ? null : (
              <Text c="dimmed">Drawing the scene…</Text>
            )}
            {scene.isError && (
              <Alert color="red" title="The scene could not be drawn">
                {scene.error.message}
                <Button size="xs" mt="xs" display="block" onClick={() => void scene.refetch()}>
                  Try again
                </Button>
              </Alert>
            )}
            {geometryUrl && arrows.length > 0 && (
              <Group gap="md">
                {[...new Map(arrows.map((a) => [a.load, a])).values()].map((a) => (
                  <Text key={a.load} size="xs">
                    <span style={{ color: a.colour }}>&#9660;</span> {a.label}
                  </Text>
                ))}
              </Group>
            )}
            <Problems validity={validity} misfits={view.misfits} />
          </Stack>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 4 }}>
          <Tabs defaultValue="settings">
            <Tabs.List>
              <Tabs.Tab value="settings">Settings</Tabs.Tab>
              <Tabs.Tab value="loads">Loads</Tabs.Tab>
              <Tabs.Tab value="preview">Preview</Tabs.Tab>
            </Tabs.List>
            <Tabs.Panel value="settings" pt="sm">
              {selectedBlock && selectedType ? (
                <Stack gap="sm">
                  <Group gap="xs">
                    <Text fw={600}>{selectedType.label}</Text>
                    <Badge variant="light" color={selectedType.status === 'ready' ? 'green' : 'orange'}>
                      {selectedType.status === 'ready' ? 'ready' : 'awaiting benchmark'}
                    </Badge>
                  </Group>
                  <SettingsForm
                    block={selectedType}
                    instanceId={selectedBlock.instance_id}
                    settings={selectedBlock.settings ?? {}}
                    library={view.library}
                    onChange={(id, value) =>
                      change(updateBlock(draft, selectedBlock.instance_id, (b) => ({ ...b, settings: { ...b.settings, [id]: value } })))
                    }
                  />
                  {selectedBlock.attach_to && (
                    <PlacementControls
                      placement={selectedBlock.placement ?? ZERO_PLACEMENT}
                      onChange={(placement) => change(updateBlock(draft, selectedBlock.instance_id, (b) => ({ ...b, placement })))}
                    />
                  )}
                </Stack>
              ) : (
                <Text c="dimmed">Choose a block.</Text>
              )}
            </Tabs.Panel>
            <Tabs.Panel value="loads" pt="sm">
              <LoadEditor loads={draft.loads ?? []} points={view.occlusal_points} onChange={(loads) => change({ ...draft, loads })} />
            </Tabs.Panel>
            <Tabs.Panel value="preview" pt="sm">
              <PreviewPanel scenarioId={view.id} revision={view.revision} loads={view.scenario.loads ?? []} valid={Boolean(validity?.ok)} />
            </Tabs.Panel>
          </Tabs>
        </Grid.Col>
      </Grid>
      <TeachingNotice variant="compact" />
      <EstimateDialog
        scenarioId={view.id}
        opened={estimateOpen}
        onClose={() => setEstimateOpen(false)}
        onStarted={(runId) => void navigate(`/results?run=${runId}`)}
      />
      <SaveAsTemplate scenarioId={view.id} opened={templateOpen} onClose={() => setTemplateOpen(false)} />
    </Stack>
  );
}

/** The builder (FR-004, FR-010, FR-011): a scenario from a template, its block tree and palette, settings and
 * placement, validity on every change, the 3D scene, loads and the automatic preview. Nothing here knows a block
 * type (SC-012). */
export default function Builder() {
  const [params, setParams] = useSearchParams();
  const scenarioId = params.get('scenario');
  const scenario = useScenario(scenarioId);
  if (!scenarioId) return <NewScenario onCreated={(id) => setParams({ scenario: id })} />;
  if (scenario.isPending) return <Text>Loading the scenario…</Text>;
  if (scenario.error) return <Alert color="red">{scenario.error.message}</Alert>;
  return <Editor key={scenario.data.id} view={scenario.data} />;
}
