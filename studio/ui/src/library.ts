// Pure helpers for the literature library pages (Story 2, tasks T110-T112): the owner's entry form turned into a
// request, why a version cannot be approved yet, and how sources and their checks read. The server applies every
// rule again; these only give the owner the reasons before a round trip.

import type {
  EntryDetail,
  EntryStatus,
  LibraryCategory,
  LibrarySource,
  LiteratureEntryVersion,
  OwnerEntry,
  ProposalSource,
  SourceCheck,
  VersionState,
} from './api/types';

export const CATEGORIES: LibraryCategory[] = ['material', 'bone', 'load', 'strength', 'frost_window'];
export const STATUSES: EntryStatus[] = ['proposed', 'approved', 'rejected', 'superseded'];
export const STUDY_TYPES: ProposalSource['study_type'][] = [
  'experimental',
  'clinical',
  'fea',
  'review',
  'standard',
  'textbook',
];

export const STATUS_COLOUR: Record<EntryStatus, string> = {
  proposed: 'yellow',
  approved: 'green',
  rejected: 'red',
  superseded: 'gray',
};

export const CHECK_COLOUR: Record<SourceCheck['status'], string> = {
  not_checked: 'gray',
  resolved_match: 'green',
  resolved_mismatch: 'red',
  unresolved: 'red',
};

export const CHECK_LABEL: Record<SourceCheck['status'], string> = {
  not_checked: 'not checked',
  resolved_match: 'matches',
  resolved_mismatch: 'does not match',
  unresolved: 'does not resolve',
};

/** One paper in the owner's entry form, with the value it reports. Numbers are '' until typed. */
export interface SourceDraft {
  authors: string;
  year: number | '';
  title: string;
  journal: string;
  doi: string;
  pmid: string;
  study_type: ProposalSource['study_type'];
  value: number | '';
  note: string;
}

export interface EntryDraft {
  parameter_key: string;
  category: LibraryCategory;
  label: string;
  units: string;
  typical: number | '';
  range_min: number | '';
  range_max: number | '';
  notes: string;
  sources: SourceDraft[];
}

export function emptySource(): SourceDraft {
  return { authors: '', year: '', title: '', journal: '', doi: '', pmid: '', study_type: 'experimental', value: '', note: '' };
}

export function emptyDraft(): EntryDraft {
  return {
    parameter_key: '',
    category: 'bone',
    label: '',
    units: '',
    typical: '',
    range_min: '',
    range_max: '',
    notes: '',
    sources: [emptySource()],
  };
}

/** "Surname AB; Other CD" or one author per line → ["Surname AB", "Other CD"]. */
export function splitAuthors(text: string): string[] {
  return text
    .split(/[;\n]/)
    .map((a) => a.trim())
    .filter(Boolean);
}

/** "10.1000/x" from a DOI typed as a link or with a "doi:" prefix. */
export function cleanDoi(text: string): string {
  return text
    .trim()
    .replace(/^https?:\/\/(dx\.)?doi\.org\//i, '')
    .replace(/^doi:\s*/i, '');
}

const KEY = /^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$/;

function blank(text: string): string | null {
  const t = text.trim();
  return t ? t : null;
}

/** The request for POST /api/library/entries, or every reason it cannot be sent yet. */
export function ownerEntryBody(draft: EntryDraft): { body: OwnerEntry | null; problems: string[] } {
  const problems: string[] = [];
  const key = draft.parameter_key.trim();
  if (!KEY.test(key)) problems.push('The parameter key is dot-separated, such as bone.cancellous.E.D4.');
  if (!draft.units.trim()) problems.push('Units are required ("1" for a value without units).');
  const { typical, range_min: min, range_max: max } = draft;
  if (typical === '' || min === '' || max === '') {
    problems.push('The typical value and both ends of the range are required.');
  } else {
    if (min > max) problems.push(`The range minimum ${min} is greater than the maximum ${max}.`);
    else if (typical < min || typical > max) problems.push(`The typical value ${typical} lies outside the range ${min}–${max}.`);
  }
  if (!draft.sources.length) problems.push('At least one source is required.');
  draft.sources.forEach((source, index) => {
    const n = `Source ${index + 1}`;
    if (!splitAuthors(source.authors).length) problems.push(`${n}: the first author is required.`);
    if (source.year === '' || source.year < 1800 || source.year > 2100) problems.push(`${n}: the year is required.`);
    if (!source.title.trim()) problems.push(`${n}: the title is required.`);
    if (!blank(source.doi) && !blank(source.pmid)) problems.push(`${n}: needs a DOI or a PMID.`);
    if (source.value === '') problems.push(`${n}: the value it reports is required.`);
    else if (min !== '' && max !== '' && (source.value < min || source.value > max))
      problems.push(`${n}: the value ${source.value} lies outside the range ${min}–${max}.`);
  });
  if (problems.length || typical === '' || min === '' || max === '') return { body: null, problems };

  const sources: ProposalSource[] = draft.sources.map((source, index) => ({
    id: `owner-${index + 1}`,
    authors: splitAuthors(source.authors),
    year: Number(source.year),
    title: source.title.trim(),
    journal: blank(source.journal),
    doi: blank(cleanDoi(source.doi)),
    pmid: blank(source.pmid),
    study_type: source.study_type,
  }));
  return {
    body: {
      entry: {
        parameter_key: key,
        category: draft.category,
        label: blank(draft.label),
        units: draft.units.trim(),
        typical,
        range: { min, max },
        values_by_source: draft.sources.map((source, index) => ({
          source: `owner-${index + 1}`,
          value: Number(source.value),
          note: blank(source.note),
        })),
        notes: blank(draft.notes),
      },
      sources,
    },
    problems: [],
  };
}

/** "Author et al. 2000" */
export function shortCitation(source: Pick<LibrarySource, 'authors' | 'year'>): string {
  const first = source.authors[0] ?? '';
  return `${first}${source.authors.length > 1 ? ' et al.' : ''} ${source.year}`;
}

/** "first author agrees · year differs (1990) · title 0.42", or the check's detail when nothing resolved. */
export function checkSummary(check: SourceCheck): string {
  const match = check.matched;
  if (!match) return check.detail ?? '';
  const parts = [
    `first author ${match.first_author ? 'agrees' : `differs (${match.resolved_first_author ?? '?'})`}`,
    `year ${match.year ? 'agrees' : `differs (${match.resolved_year ?? '?'})`}`,
    `title similarity ${match.title_similarity.toFixed(2)}`,
  ];
  return parts.join(' · ');
}

/** Why the Approve button is disabled for this version, or null when it can be approved. */
export function approvalBlocker(version: LiteratureEntryVersion, state: VersionState | undefined): string | null {
  if (version.status !== 'proposed') return `Version ${version.version} is ${version.status}; only a proposed version can be approved.`;
  if (!state) return 'Loading…';
  if (state.approvable) return null;
  return state.problems.map((p) => p.message).join(' ') || 'This version cannot be approved yet.';
}

/** Newest version first. */
export function versionsNewestFirst(detail: EntryDetail): LiteratureEntryVersion[] {
  return [...detail.versions].sort((a, b) => b.version - a.version);
}

export function sourceById(detail: EntryDetail, id: string): LibrarySource | undefined {
  return detail.sources.find((s) => s.id === id);
}

/** "690 MPa"; a dimensionless value (units "1") without units. */
export function quantity(value: number, units: string | null | undefined): string {
  const text = value.toLocaleString(undefined, { maximumFractionDigits: 6 });
  return !units || units === '1' ? text : `${text} ${units}`;
}
