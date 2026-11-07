// Query hooks and calls for slice 1a: catalogue, projects, scenarios, previews, estimates, runs and the queue, and
// for Story 2: the literature library (contracts/studio-api.md). Pages use these; none of them knows a block type.

import { useQuery, useQueryClient } from '@tanstack/react-query';

import { apiGet, apiPost, apiPut, withQuery } from './client';
import { useStudioEvents } from './events';
import type {
  ApprovalResult,
  BlockTypeEntry,
  DetailLevel,
  EntryChanges,
  EntryDetail,
  EntryStatus,
  ImportedVersion,
  ImportResult,
  LibraryCategory,
  LiteratureEntry,
  LiteratureEntryVersion,
  OwnerEntry,
  PhysicsItem,
  PreviewQueued,
  PreviewView,
  ProjectRecord,
  ProjectView,
  QueueView,
  RejectResult,
  ResultsView,
  RunEstimate,
  RunQueued,
  RunView,
  Scenario,
  ScenarioCreated,
  ScenarioView,
  SettingValue,
  SnapshotWritten,
  SourceCheckView,
  TemplateSummary,
  ValidityResult,
} from './types';

export const keys = {
  blocks: ['blocks'] as const,
  physics: ['physics'] as const,
  templates: ['templates'] as const,
  projects: ['projects'] as const,
  project: (id: string) => ['project', id] as const,
  scenario: (id: string) => ['scenario', id] as const,
  preview: (id: string) => ['preview', id] as const,
  run: (id: string) => ['run', id] as const,
  results: (id: string) => ['results', id] as const,
  queue: ['queue'] as const,
  entry: (ref: string) => ['library-entry', ref] as const,
  entries: ['library-entries'] as const,
};

export function useBlocks() {
  return useQuery({
    queryKey: keys.blocks,
    queryFn: ({ signal }) => apiGet<BlockTypeEntry[]>('/api/blocks', { signal }),
    staleTime: 60_000,
  });
}

export function usePhysics() {
  const queryClient = useQueryClient();
  useStudioEvents({ 'capability.changed': () => void queryClient.invalidateQueries({ queryKey: keys.physics }) });
  return useQuery({
    queryKey: keys.physics,
    queryFn: ({ signal }) => apiGet<PhysicsItem[]>('/api/physics', { signal }),
  });
}

export function useTemplates() {
  return useQuery({
    queryKey: keys.templates,
    queryFn: ({ signal }) => apiGet<TemplateSummary[]>('/api/templates', { signal }),
  });
}

export function useProjects() {
  return useQuery({
    queryKey: keys.projects,
    queryFn: ({ signal }) => apiGet<ProjectRecord[]>('/api/projects', { signal }),
  });
}

export function useProject(id: string | null) {
  return useQuery({
    queryKey: keys.project(id ?? ''),
    queryFn: ({ signal }) => apiGet<ProjectView>(`/api/projects/${id}`, { signal }),
    enabled: id !== null,
  });
}

export function useScenario(id: string | null) {
  return useQuery({
    queryKey: keys.scenario(id ?? ''),
    queryFn: ({ signal }) => apiGet<ScenarioView>(`/api/scenarios/${id}`, { signal }),
    enabled: id !== null,
  });
}

export function useLibraryEntry(ref: string | null) {
  return useQuery({
    queryKey: keys.entry(ref ?? ''),
    queryFn: ({ signal }) => apiGet<EntryDetail>(`/api/library/entries/${encodeURIComponent(ref ?? '')}`, { signal }),
    enabled: ref !== null,
    staleTime: 60_000,
  });
}

export interface EntryFilters {
  status?: EntryStatus | null;
  category?: LibraryCategory | null;
  q?: string | null;
}

export function useLibraryEntries(filters: EntryFilters) {
  return useQuery({
    queryKey: [...keys.entries, filters],
    queryFn: ({ signal }) =>
      apiGet<LiteratureEntry[]>(
        withQuery('/api/library/entries', { status: filters.status, category: filters.category, q: filters.q || null }),
        { signal },
      ),
  });
}

/** Refetch the entry list and every entry detail after a change to the library. */
export function useRefreshLibrary() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: keys.entries });
    void queryClient.invalidateQueries({ queryKey: ['library-entry'] });
  };
}

function entryPath(entryId: string, version?: number): string {
  const base = `/api/library/entries/${encodeURIComponent(entryId)}`;
  return version === undefined ? base : `${base}/versions/${version}`;
}

export function addOwnerEntry(body: OwnerEntry): Promise<ImportedVersion> {
  return apiPost<ImportedVersion>('/api/library/entries', body);
}

export function importProposals(file: File): Promise<ImportResult> {
  const form = new FormData();
  form.append('file', file);
  return apiPost<ImportResult>('/api/library/import', form);
}

export function checkSources(entryId: string, version: number): Promise<SourceCheckView> {
  return apiPost<SourceCheckView>(`${entryPath(entryId, version)}/check-sources`);
}

export function approveVersion(entryId: string, version: number): Promise<ApprovalResult> {
  return apiPost<ApprovalResult>(`${entryPath(entryId, version)}/approve`);
}

export function rejectVersion(entryId: string, version: number, reason: string): Promise<RejectResult> {
  return apiPost<RejectResult>(`${entryPath(entryId, version)}/reject`, { reason });
}

export function editEntry(entryId: string, changes: EntryChanges): Promise<LiteratureEntryVersion> {
  return apiPut<LiteratureEntryVersion>(entryPath(entryId), { changes });
}

export function exportLibrary(): Promise<SnapshotWritten> {
  return apiPost<SnapshotWritten>('/api/library/export');
}

export function createProject(name: string): Promise<ProjectRecord> {
  return apiPost<ProjectRecord>('/api/projects', { name });
}

export function createScenario(
  projectId: string,
  templateId: string,
  options: { name?: string; jawSettings?: Record<string, SettingValue> } = {},
): Promise<ScenarioCreated> {
  return apiPost<ScenarioCreated>('/api/scenarios', {
    project_id: projectId,
    name: options.name,
    from: { template_id: templateId, jaw_settings: options.jawSettings },
  });
}

export function saveScenario(id: string, revision: number, scenario: Scenario): Promise<ScenarioView> {
  return apiPut<ScenarioView>(`/api/scenarios/${id}`, { revision, scenario });
}

export function validateScenario(id: string, mode: 'fast' | 'full'): Promise<ValidityResult> {
  return apiPost<ValidityResult>(`/api/scenarios/${id}/validate?mode=${mode}`);
}

/** The builder's scene as binary glTF; resolves with an object URL the viewer loads. */
export async function scenarioGeometryUrl(id: string, signal?: AbortSignal): Promise<string> {
  const response = await fetch(`/api/scenarios/${id}/geometry`, {
    method: 'POST',
    credentials: 'same-origin',
    signal,
  });
  if (!response.ok) throw new Error(`The scene could not be drawn (HTTP ${response.status}).`);
  return URL.createObjectURL(await response.blob());
}

export function requestPreview(id: string, loadIds: string[] | null): Promise<PreviewQueued> {
  return apiPost<PreviewQueued>(`/api/scenarios/${id}/preview`, { load_ids: loadIds });
}

export function getPreview(id: string, signal?: AbortSignal): Promise<PreviewView> {
  return apiGet<PreviewView>(`/api/previews/${id}`, { signal });
}

export function estimateRun(id: string, level: DetailLevel): Promise<RunEstimate> {
  return apiPost<RunEstimate>(`/api/scenarios/${id}/estimate`, { detail_level: level });
}

export function startRun(id: string, level: DetailLevel): Promise<RunQueued> {
  return apiPost<RunQueued>(`/api/scenarios/${id}/runs`, { detail_level: level });
}

export function cancelRun(id: string): Promise<unknown> {
  return apiPost(`/api/runs/${id}/cancel`);
}

export function useRun(id: string | null) {
  const queryClient = useQueryClient();
  useStudioEvents({
    'run.finished': (data) => {
      if (data.run_id === id) {
        void queryClient.invalidateQueries({ queryKey: keys.run(id ?? '') });
        void queryClient.invalidateQueries({ queryKey: keys.results(id ?? '') });
      }
    },
  });
  return useQuery({
    queryKey: keys.run(id ?? ''),
    queryFn: ({ signal }) => apiGet<RunView>(`/api/runs/${id}`, { signal }),
    enabled: id !== null,
  });
}

export function useResults(id: string | null, enabled: boolean) {
  return useQuery({
    queryKey: keys.results(id ?? ''),
    queryFn: ({ signal }) => apiGet<ResultsView>(`/api/runs/${id}/results`, { signal }),
    enabled: id !== null && enabled,
  });
}

export function useQueue() {
  const queryClient = useQueryClient();
  const refresh = () => void queryClient.invalidateQueries({ queryKey: keys.queue });
  useStudioEvents({ 'run.finished': refresh, 'preview.ready': refresh, 'job.failed': refresh });
  return useQuery({
    queryKey: keys.queue,
    queryFn: ({ signal }) => apiGet<QueueView>('/api/queue', { signal }),
    refetchInterval: 5_000,
  });
}
