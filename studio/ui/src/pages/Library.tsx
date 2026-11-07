import {
  Alert,
  Badge,
  Button,
  Card,
  FileButton,
  Grid,
  Group,
  List,
  Modal,
  NumberInput,
  Select,
  Stack,
  Table,
  Text,
  Textarea,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { useState } from 'react';
import { useSearchParams } from 'react-router';

import { isStudioError } from '../api/client';
import {
  approveVersion,
  checkSources,
  editEntry,
  exportLibrary,
  importProposals,
  rejectVersion,
  useLibraryEntries,
  useLibraryEntry,
  useRefreshLibrary,
} from '../api/studio';
import type {
  EntryChanges,
  EntryDetail,
  EntryStatus,
  LibraryCategory,
  LiteratureEntryVersion,
  VersionState,
} from '../api/types';
import { AddEntryForm } from '../components/AddEntryForm';
import {
  approvalBlocker,
  CATEGORIES,
  CHECK_COLOUR,
  CHECK_LABEL,
  checkSummary,
  quantity,
  shortCitation,
  sourceById,
  STATUS_COLOUR,
  STATUSES,
  versionsNewestFirst,
} from '../library';

interface Notice {
  color: string;
  title: string;
  lines: string[];
}

function noticeFromError(title: string, e: unknown): Notice {
  if (isStudioError(e)) return { color: 'red', title, lines: [e.message, ...e.problems.map((p) => p.message)] };
  return { color: 'red', title, lines: [String(e)] };
}

function NoticeAlert({ notice, onClose }: { notice: Notice; onClose: () => void }) {
  return (
    <Alert color={notice.color} title={notice.title} withCloseButton onClose={onClose}>
      {notice.lines.length === 1 ? (
        <Text size="sm">{notice.lines[0]}</Text>
      ) : (
        <List size="sm">
          {notice.lines.map((line, i) => (
            <List.Item key={i}>{line}</List.Item>
          ))}
        </List>
      )}
    </Alert>
  );
}

// --- one version -----------------------------------------------------------------------------------------------------

function SourcesTable({ detail, version }: { detail: EntryDetail; version: LiteratureEntryVersion }) {
  return (
    <Table withTableBorder fz="xs" verticalSpacing={4}>
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Source</Table.Th>
          <Table.Th ta="right">Value</Table.Th>
          <Table.Th>Check</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {version.values_by_source.map((item) => {
          const source = sourceById(detail, item.source_id);
          return (
            <Table.Tr key={item.source_id}>
              <Table.Td>
                {source ? (
                  <Stack gap={0}>
                    <Text size="xs" fw={500}>
                      {source.authors.join(', ')} ({source.year})
                    </Text>
                    <Text size="xs">
                      {source.title}
                      {source.journal ? `. ${source.journal}` : ''}
                    </Text>
                    <Text size="xs" c="dimmed">
                      {[source.doi && `doi ${source.doi}`, source.pmid && `pmid ${source.pmid}`, source.study_type].filter(Boolean).join(' · ')}
                    </Text>
                    {item.note && (
                      <Text size="xs" c="dimmed" fs="italic">
                        {item.note}
                      </Text>
                    )}
                  </Stack>
                ) : (
                  item.source_id
                )}
              </Table.Td>
              <Table.Td ta="right" style={{ whiteSpace: 'nowrap' }}>
                {quantity(item.value, version.units)}
              </Table.Td>
              <Table.Td>
                {source && (
                  <Stack gap={2}>
                    <Badge size="xs" variant="light" color={CHECK_COLOUR[source.check.status]}>
                      {CHECK_LABEL[source.check.status]}
                    </Badge>
                    <Text size="xs" c="dimmed">
                      {checkSummary(source.check)}
                    </Text>
                    {source.check.matched?.resolved_title && source.check.status !== 'resolved_match' && (
                      <Text size="xs" c="dimmed">
                        resolved: “{source.check.matched.resolved_title}”
                      </Text>
                    )}
                  </Stack>
                )}
              </Table.Td>
            </Table.Tr>
          );
        })}
      </Table.Tbody>
    </Table>
  );
}

function VersionCard({
  detail,
  version,
  state,
  newest,
  onCheck,
  onApprove,
  onReject,
  onEdit,
  busy,
}: {
  detail: EntryDetail;
  version: LiteratureEntryVersion;
  state: VersionState | undefined;
  newest: boolean;
  onCheck: () => void;
  onApprove: () => void;
  onReject: () => void;
  onEdit: () => void;
  busy: boolean;
}) {
  const blocker = approvalBlocker(version, state);
  const canReject = version.status === 'proposed' || version.status === 'approved';
  return (
    <Card withBorder padding="sm">
      <Stack gap="xs">
        <Group justify="space-between">
          <Group gap="xs">
            <Text fw={600}>Version {version.version}</Text>
            <Badge color={STATUS_COLOUR[version.status]} variant="light">
              {version.status}
            </Badge>
            <Badge color="gray" variant="outline" size="sm">
              {version.origin === 'owner' ? 'owner entry' : 'Claude proposal'}
            </Badge>
            {state && state.runs_using > 0 && (
              <Badge color="blue" variant="outline" size="sm">
                used by {state.runs_using} run{state.runs_using === 1 ? '' : 's'}
              </Badge>
            )}
          </Group>
          <Text size="xs" c="dimmed">
            proposed {version.proposed_at.slice(0, 10)}
            {version.approved_at && ` · approved ${version.approved_at.slice(0, 10)}`}
            {version.superseded_at && ` · superseded ${version.superseded_at.slice(0, 10)}`}
            {version.rejected_at && ` · rejected ${version.rejected_at.slice(0, 10)}`}
          </Text>
        </Group>
        <Text size="sm">
          Typical <b>{quantity(version.typical, version.units)}</b> · range {quantity(version.range_min, version.units)} to{' '}
          {quantity(version.range_max, version.units)}
        </Text>
        {version.notes && (
          <Text size="xs" c="dimmed">
            {version.notes}
          </Text>
        )}
        {version.rejected_reason && (
          <Alert color="red" p="xs">
            <Text size="xs">Rejected: {version.rejected_reason}</Text>
          </Alert>
        )}
        <SourcesTable detail={detail} version={version} />
        <Group gap="xs">
          {version.status === 'proposed' && (
            <Button size="xs" variant="light" onClick={onCheck} loading={busy}>
              Check sources
            </Button>
          )}
          {version.status === 'proposed' && (
            <Tooltip label={blocker ?? ''} disabled={!blocker} multiline w={360}>
              <Button size="xs" color="green" onClick={onApprove} disabled={!!blocker || busy}>
                Approve
              </Button>
            </Tooltip>
          )}
          {canReject && (
            <Button size="xs" color="red" variant="light" onClick={onReject} disabled={busy}>
              Reject with reason
            </Button>
          )}
          {newest && (
            <Button size="xs" variant="default" onClick={onEdit} disabled={busy}>
              Edit (new version)
            </Button>
          )}
        </Group>
        {version.status === 'proposed' && blocker && (
          <Text size="xs" c="dimmed">
            Approve is not available: {blocker}
          </Text>
        )}
      </Stack>
    </Card>
  );
}

// --- dialogs ---------------------------------------------------------------------------------------------------------

function RejectDialog({
  version,
  runs,
  onClose,
  onReject,
}: {
  version: LiteratureEntryVersion;
  runs: number;
  onClose: () => void;
  onReject: (reason: string) => Promise<void>;
}) {
  const [reason, setReason] = useState('');
  const [saving, setSaving] = useState(false);
  return (
    <Stack gap="sm">
      <Textarea
        label="Reason"
        description="Recorded with the version and shown wherever it was used."
        autosize
        minRows={3}
        value={reason}
        onChange={(e) => setReason(e.currentTarget.value)}
        data-autofocus
      />
      {runs > 0 && (
        <Alert color="orange" p="xs">
          <Text size="sm">
            {runs} run{runs === 1 ? '' : 's'} used version {version.version}. They will be flagged “uses a rejected entry” and
            cannot be published until they are re-run with approved entries.
          </Text>
        </Alert>
      )}
      {version.status === 'approved' && (
        <Text size="xs" c="dimmed">
          This is the approved version: the entry has no approved version afterwards, so scenarios cannot choose it until
          another version is approved.
        </Text>
      )}
      <Group justify="flex-end">
        <Button variant="default" onClick={onClose}>
          Cancel
        </Button>
        <Button
          color="red"
          disabled={!reason.trim()}
          loading={saving}
          onClick={() => {
            setSaving(true);
            void onReject(reason.trim()).finally(() => setSaving(false));
          }}
        >
          Reject version {version.version}
        </Button>
      </Group>
    </Stack>
  );
}

function EditDialog({
  detail,
  base,
  onClose,
  onSave,
}: {
  detail: EntryDetail;
  base: LiteratureEntryVersion;
  onClose: () => void;
  onSave: (changes: EntryChanges) => Promise<void>;
}) {
  const [units, setUnits] = useState(base.units);
  const [typical, setTypical] = useState<number | ''>(base.typical);
  const [min, setMin] = useState<number | ''>(base.range_min);
  const [max, setMax] = useState<number | ''>(base.range_max);
  const [notes, setNotes] = useState(base.notes ?? '');
  const [values, setValues] = useState(base.values_by_source.map((v) => ({ ...v })));
  const [saving, setSaving] = useState(false);

  const changes: EntryChanges = {};
  if (units.trim() !== base.units) changes.units = units.trim();
  if (typical !== '' && typical !== base.typical) changes.typical = typical;
  if (min !== '' && min !== base.range_min) changes.range_min = min;
  if (max !== '' && max !== base.range_max) changes.range_max = max;
  if ((notes.trim() || null) !== (base.notes ?? null)) changes.notes = notes.trim() || null;
  if (values.some((v, i) => v.value !== base.values_by_source[i]?.value || (v.note ?? null) !== (base.values_by_source[i]?.note ?? null)))
    changes.values_by_source = values.map((v) => ({ source_id: v.source_id, value: v.value, note: v.note || null }));
  const changed = Object.keys(changes).length > 0;

  return (
    <Stack gap="sm">
      <Text size="sm">
        Saving creates version {Math.max(...detail.versions.map((v) => v.version)) + 1} as proposed. Version {base.version} stays as it
        is until the new one is approved, and runs keep the version they used. To cite a new paper, add an owner entry for
        the same parameter key.
      </Text>
      <Group grow>
        <TextInput label="Units" value={units} onChange={(e) => setUnits(e.currentTarget.value)} />
        <NumberInput label="Typical" value={typical} onChange={(v) => setTypical(typeof v === 'number' ? v : '')} />
        <NumberInput label="Range minimum" value={min} onChange={(v) => setMin(typeof v === 'number' ? v : '')} />
        <NumberInput label="Range maximum" value={max} onChange={(v) => setMax(typeof v === 'number' ? v : '')} />
      </Group>
      <Textarea label="Notes" autosize minRows={2} value={notes} onChange={(e) => setNotes(e.currentTarget.value)} />
      <Stack gap={4}>
        <Text size="sm" fw={500}>
          Values by source
        </Text>
        {values.map((item, index) => {
          const source = sourceById(detail, item.source_id);
          return (
            <Group key={item.source_id} grow align="flex-end">
              <NumberInput
                label={source ? shortCitation(source) : item.source_id}
                value={item.value}
                onChange={(v) => typeof v === 'number' && setValues((all) => all.map((x, i) => (i === index ? { ...x, value: v } : x)))}
              />
              <TextInput
                label="Note"
                value={item.note ?? ''}
                onChange={(e) => {
                  const note = e.currentTarget.value;
                  setValues((all) => all.map((x, i) => (i === index ? { ...x, note } : x)));
                }}
              />
            </Group>
          );
        })}
      </Stack>
      <Group justify="flex-end">
        <Button variant="default" onClick={onClose}>
          Cancel
        </Button>
        <Button
          disabled={!changed}
          loading={saving}
          onClick={() => {
            setSaving(true);
            void onSave(changes).finally(() => setSaving(false));
          }}
        >
          Save as a new version
        </Button>
      </Group>
    </Stack>
  );
}

// --- one entry -------------------------------------------------------------------------------------------------------

type Dialog = { kind: 'reject'; version: LiteratureEntryVersion } | { kind: 'edit'; version: LiteratureEntryVersion } | null;

function EntryPanel({ entryId, onNotice }: { entryId: string; onNotice: (notice: Notice | null) => void }) {
  const entry = useLibraryEntry(entryId);
  const refresh = useRefreshLibrary();
  const [busy, setBusy] = useState<number | null>(null);
  const [dialog, setDialog] = useState<Dialog>(null);

  if (entry.isPending) return <Text>Loading the entry…</Text>;
  if (entry.error) return <Alert color="red">{entry.error.message}</Alert>;
  const detail = entry.data;
  const versions = versionsNewestFirst(detail);
  const stateOf = (v: number) => detail.states.find((s) => s.version === v);

  const act = async (version: number, title: string, action: () => Promise<Notice>) => {
    setBusy(version);
    try {
      onNotice(await action());
    } catch (e) {
      onNotice(noticeFromError(title, e));
    } finally {
      setBusy(null);
      refresh();
    }
  };

  const check = (v: LiteratureEntryVersion) =>
    act(v.version, 'The source check failed', async () => {
      const result = await checkSources(detail.entry.id, v.version);
      return {
        color: result.approvable ? 'green' : 'orange',
        title: result.approvable ? `Every source matches; version ${v.version} can be approved` : `Version ${v.version} cannot be approved yet`,
        lines: result.sources.map((s) => `${shortCitation(s.source)}: ${CHECK_LABEL[s.check.status]}. ${checkSummary(s.check)}`),
      };
    });

  const approve = (v: LiteratureEntryVersion) =>
    act(v.version, `Version ${v.version} was not approved`, async () => {
      const result = await approveVersion(detail.entry.id, v.version);
      const superseded = result.superseded.length ? ` Version ${result.superseded.join(', ')} is superseded.` : '';
      return { color: 'green', title: 'Approved', lines: [`${detail.entry.parameter_key} version ${v.version} is approved.${superseded}`] };
    });

  return (
    <Stack gap="sm">
      <Stack gap={2}>
        <Title order={2} size="h3">
          {detail.entry.label}
        </Title>
        <Group gap="xs">
          <Text size="sm" ff="monospace">
            {detail.entry.parameter_key}
          </Text>
          <Badge variant="outline" size="sm">
            {detail.entry.category.replace('_', ' ')}
          </Badge>
          <Text size="sm" c={detail.entry.current_version ? undefined : 'red'}>
            {detail.entry.current_version ? `approved version: ${detail.entry.current_version}` : 'no approved version: scenarios cannot choose it'}
          </Text>
        </Group>
      </Stack>
      {versions.map((v, index) => (
        <VersionCard
          key={v.version}
          detail={detail}
          version={v}
          state={stateOf(v.version)}
          newest={index === 0}
          busy={busy === v.version}
          onCheck={() => void check(v)}
          onApprove={() => void approve(v)}
          onReject={() => setDialog({ kind: 'reject', version: v })}
          onEdit={() => setDialog({ kind: 'edit', version: v })}
        />
      ))}
      <Modal opened={dialog?.kind === 'reject'} onClose={() => setDialog(null)} title={`Reject version ${dialog?.version.version ?? ''}`}>
        {dialog?.kind === 'reject' && (
          <RejectDialog
            version={dialog.version}
            runs={stateOf(dialog.version.version)?.runs_using ?? 0}
            onClose={() => setDialog(null)}
            onReject={(reason) =>
              act(dialog.version.version, 'The version was not rejected', async () => {
                const result = await rejectVersion(detail.entry.id, dialog.version.version, reason);
                setDialog(null);
                const lines = [`${detail.entry.parameter_key} version ${dialog.version.version} is rejected.`];
                if (result.flagged_runs.length)
                  lines.push(`${result.flagged_runs.length} run(s) are flagged “uses a rejected entry” and cannot be published: ${result.flagged_runs.join(', ')}.`);
                if (result.was_current) lines.push('The entry has no approved version now.');
                return { color: 'orange', title: 'Rejected', lines };
              })
            }
          />
        )}
      </Modal>
      <Modal opened={dialog?.kind === 'edit'} onClose={() => setDialog(null)} title={`Edit ${detail.entry.parameter_key}`} size="xl">
        {dialog?.kind === 'edit' && (
          <EditDialog
            detail={detail}
            base={dialog.version}
            onClose={() => setDialog(null)}
            onSave={(changes) =>
              act(dialog.version.version, 'The edit was not saved', async () => {
                const created = await editEntry(detail.entry.id, changes);
                setDialog(null);
                return {
                  color: 'blue',
                  title: `Version ${created.version} created`,
                  lines: [`Version ${created.version} is proposed. Check its sources and approve it to make it the one scenarios use.`],
                };
              })
            }
          />
        )}
      </Modal>
    </Stack>
  );
}

// --- the page --------------------------------------------------------------------------------------------------------

/** The literature library (Story 2): browse and filter, add an entry by hand or import proposals in bulk, check
 * sources, approve or reject with a reason, and edit an entry into a new version. Only approved versions can be chosen
 * in a scenario (FR-014). */
export default function Library() {
  const [params, setParams] = useSearchParams();
  const selected = params.get('entry');
  const [status, setStatus] = useState<EntryStatus | null>(null);
  const [category, setCategory] = useState<LibraryCategory | null>(null);
  const [q, setQ] = useState('');
  const [adding, setAdding] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const entries = useLibraryEntries({ status, category, q });
  const refresh = useRefreshLibrary();

  const select = (id: string) => {
    setNotice(null);
    setParams((p) => {
      p.set('entry', id);
      return p;
    });
  };

  const upload = async (file: File | null) => {
    if (!file) return;
    try {
      const result = await importProposals(file);
      const lines = [
        `${result.imported.length} proposed version(s) added: ${result.imported.map((i) => `${i.parameter_key} v${i.version}`).join(', ') || 'none'}.`,
      ];
      if (result.unchanged.length) lines.push(`${result.unchanged.length} entr${result.unchanged.length === 1 ? 'y equals' : 'ies equal'} a stored version and were skipped.`);
      lines.push(...result.warnings);
      setNotice({ color: 'green', title: `Imported ${file.name}`, lines });
      setStatus('proposed');
    } catch (e) {
      setNotice(noticeFromError(`${file.name} was not imported`, e));
    } finally {
      refresh();
    }
  };

  const exportSnapshot = async () => {
    try {
      const written = await exportLibrary();
      setNotice({ color: 'green', title: 'Snapshot written', lines: [`${written.entries} approved entries written to ${written.path}.`] });
    } catch (e) {
      setNotice(noticeFromError('The snapshot was not written', e));
    }
  };

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={1} size="h2">
          Library
        </Title>
        <Group gap="xs">
          <Button size="xs" onClick={() => setAdding(true)}>
            Add entry
          </Button>
          <FileButton onChange={(file) => void upload(file)} accept=".yaml,.yml,application/yaml,text/yaml">
            {(props) => (
              <Button size="xs" variant="light" {...props}>
                Import proposals (YAML)
              </Button>
            )}
          </FileButton>
          <Button size="xs" variant="default" onClick={() => void exportSnapshot()}>
            Export snapshot
          </Button>
        </Group>
      </Group>
      {notice && <NoticeAlert notice={notice} onClose={() => setNotice(null)} />}
      <Grid gap="md">
        <Grid.Col span={{ base: 12, md: 5 }}>
          <Stack gap="xs">
            <Group gap="xs" grow>
              <Select
                size="xs"
                placeholder="Any status"
                data={STATUSES}
                value={status}
                onChange={(v) => setStatus(v as EntryStatus | null)}
                clearable
                aria-label="Status"
              />
              <Select
                size="xs"
                placeholder="Any category"
                data={CATEGORIES.map((c) => ({ value: c, label: c.replace('_', ' ') }))}
                value={category}
                onChange={(v) => setCategory(v as LibraryCategory | null)}
                clearable
                aria-label="Category"
              />
            </Group>
            <TextInput size="xs" placeholder="Search key or label" value={q} onChange={(e) => setQ(e.currentTarget.value)} aria-label="Search" />
            {entries.error && <Alert color="red">{entries.error.message}</Alert>}
            <Table highlightOnHover striped withTableBorder fz="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Parameter</Table.Th>
                  <Table.Th>Category</Table.Th>
                  <Table.Th>Approved</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {(entries.data ?? []).map((e) => (
                  <Table.Tr
                    key={e.id}
                    onClick={() => select(e.id)}
                    style={{ cursor: 'pointer' }}
                    bg={e.id === selected ? 'var(--mantine-primary-color-light)' : undefined}
                  >
                    <Table.Td>
                      <Text size="xs" ff="monospace">
                        {e.parameter_key}
                      </Text>
                      <Text size="xs" c="dimmed">
                        {e.label}
                      </Text>
                    </Table.Td>
                    <Table.Td>{e.category.replace('_', ' ')}</Table.Td>
                    <Table.Td>
                      {e.current_version ? (
                        <Badge size="xs" color="green" variant="light">
                          v{e.current_version}
                        </Badge>
                      ) : (
                        <Badge size="xs" color="gray" variant="light">
                          none
                        </Badge>
                      )}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            {entries.data?.length === 0 && (
              <Text size="sm" c="dimmed">
                No entries match.
              </Text>
            )}
          </Stack>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 7 }}>
          {selected ? (
            <EntryPanel entryId={selected} onNotice={setNotice} />
          ) : (
            <Text c="dimmed">Choose an entry to see its versions, values and sources.</Text>
          )}
        </Grid.Col>
      </Grid>
      <Modal opened={adding} onClose={() => setAdding(false)} title="Add an entry from a paper" size="xl">
        <AddEntryForm
          onCancel={() => setAdding(false)}
          onSaved={(result) => {
            setAdding(false);
            refresh();
            select(result.entry_id);
            setNotice({
              color: 'blue',
              title: `${result.parameter_key} version ${result.version} saved as proposed`,
              lines: ['Check its sources, then approve it.'],
            });
          }}
        />
      </Modal>
    </Stack>
  );
}
