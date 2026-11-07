import { describe, expect, it } from 'vitest';

import type { LiteratureEntryVersion, SourceCheck, VersionState } from '../src/api/types';
import {
  approvalBlocker,
  checkSummary,
  cleanDoi,
  emptyDraft,
  ownerEntryBody,
  quantity,
  shortCitation,
  splitAuthors,
  type EntryDraft,
} from '../src/library';

// Story 2 (tasks T110, T111): the owner's entry form and the library page's helpers.

function draft(changes: Partial<EntryDraft> = {}): EntryDraft {
  return {
    ...emptyDraft(),
    parameter_key: 'bone.cancellous.E.D4',
    category: 'bone',
    units: 'MPa',
    typical: 690,
    range_min: 200,
    range_max: 1400,
    sources: [
      {
        authors: 'Author AB; Second CD',
        year: 2000,
        title: 'Elastic modulus of cancellous bone',
        journal: '',
        doi: 'https://doi.org/10.1000/example.a',
        pmid: '',
        study_type: 'experimental',
        value: 690,
        note: ' Table 2 ',
      },
    ],
    ...changes,
  };
}

function version(status: LiteratureEntryVersion['status']): LiteratureEntryVersion {
  return {
    entry_id: 'E1',
    version: 2,
    units: 'MPa',
    typical: 690,
    range_min: 200,
    range_max: 1400,
    values_by_source: [{ source_id: 'S1', value: 690 }],
    status,
    origin: 'owner',
    proposed_at: '2026-11-07T00:00:00Z',
    content_hash: 'x',
  };
}

describe('ownerEntryBody', () => {
  it('builds the request, cleaning the DOI and splitting authors', () => {
    const { body, problems } = ownerEntryBody(draft());
    expect(problems).toEqual([]);
    expect(body?.entry).toMatchObject({
      parameter_key: 'bone.cancellous.E.D4',
      units: 'MPa',
      typical: 690,
      range: { min: 200, max: 1400 },
      values_by_source: [{ source: 'owner-1', value: 690, note: 'Table 2' }],
      label: null,
      notes: null,
    });
    expect(body?.sources[0]).toMatchObject({ id: 'owner-1', authors: ['Author AB', 'Second CD'], doi: '10.1000/example.a', pmid: null });
  });

  it('lists every reason at once', () => {
    const { body, problems } = ownerEntryBody(
      draft({
        parameter_key: 'bone..E',
        units: ' ',
        typical: 1500,
        sources: [{ ...draft().sources[0]!, doi: '', pmid: '', value: 100, authors: '', title: '' }],
      }),
    );
    expect(body).toBeNull();
    expect(problems).toEqual([
      'The parameter key is dot-separated, such as bone.cancellous.E.D4.',
      'Units are required ("1" for a value without units).',
      'The typical value 1500 lies outside the range 200–1400.',
      'Source 1: the first author is required.',
      'Source 1: the title is required.',
      'Source 1: needs a DOI or a PMID.',
      'Source 1: the value 100 lies outside the range 200–1400.',
    ]);
  });

  it('accepts a PMID instead of a DOI and refuses a reversed range', () => {
    expect(ownerEntryBody(draft({ sources: [{ ...draft().sources[0]!, doi: '', pmid: '1234' }] })).problems).toEqual([]);
    expect(ownerEntryBody(draft({ range_min: 1400, range_max: 200 })).problems).toContain(
      'The range minimum 1400 is greater than the maximum 200.',
    );
  });
});

describe('approvalBlocker', () => {
  const state = (approvable: boolean, messages: string[] = []): VersionState => ({
    version: 2,
    approvable,
    problems: messages.map((message) => ({ message })),
    runs_using: 0,
  });

  it('gives the server reasons until the version can be approved', () => {
    expect(approvalBlocker(version('proposed'), state(false, ['source X has not been checked']))).toBe('source X has not been checked');
    expect(approvalBlocker(version('proposed'), state(true))).toBeNull();
    expect(approvalBlocker(version('approved'), state(false))).toMatch(/only a proposed version/);
  });
});

describe('formatting', () => {
  it('cites, summarises checks and shows quantities', () => {
    expect(shortCitation({ authors: ['Author AB', 'Second CD'], year: 2000 })).toBe('Author AB et al. 2000');
    expect(shortCitation({ authors: ['Solo EF'], year: 2004 })).toBe('Solo EF 2004');
    const check: SourceCheck = {
      status: 'resolved_mismatch',
      matched: { first_author: true, year: false, title_similarity: 0.42, resolved_year: 1990 },
    };
    expect(checkSummary(check)).toBe('first author agrees · year differs (1990) · title similarity 0.42');
    expect(checkSummary({ status: 'unresolved', detail: 'DOI not found' })).toBe('DOI not found');
    expect(quantity(0.3, '1')).toBe('0.3');
    expect(quantity(690, 'MPa')).toBe('690 MPa');
    expect(cleanDoi('doi: 10.1/x')).toBe('10.1/x');
    expect(splitAuthors('A AB\nB CD; ')).toEqual(['A AB', 'B CD']);
  });
});
