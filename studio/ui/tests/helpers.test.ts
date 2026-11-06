import { describe, expect, it } from 'vitest';

import { HOME_PATH, loginErrorMessage, loginPath, safeNextPath } from '../src/api/auth';
import { StudioError } from '../src/api/client';
import { MAX_RETRIES, shouldRetry } from '../src/api/queryClient';
import { formatAvailableOf, formatGb, formatLane, sameHash, shortHash } from '../src/format';

function studioError(status: number, code: string, message = 'message'): StudioError {
  return new StudioError(status, { code, message, problems: [], missing: [] });
}

describe('formatGb', () => {
  it('uses one decimal below 100 GB and whole numbers above', () => {
    expect(formatGb(0.44)).toBe('0.4 GB');
    expect(formatGb(23.14)).toBe('23.1 GB');
    expect(formatGb(64)).toBe('64.0 GB');
    expect(formatGb(99.96)).toBe('100.0 GB');
    expect(formatGb(412.4)).toBe('412 GB');
    expect(formatGb(Number.NaN)).toBe('–');
  });

  it('formats available of total and lane counts', () => {
    expect(formatAvailableOf(23.14, 64)).toBe('23.1 of 64.0 GB');
    expect(formatLane({ running: 1, queued: 3 })).toBe('1 running · 3 queued');
  });
});

describe('hashes', () => {
  it('compares with or without a prefix, and a shortened hash', () => {
    expect(sameHash('sha256:ABCDEF0123456789', 'abcdef0123456789')).toBe(true);
    expect(sameHash('abcdef0123456789', 'abcdef01')).toBe(true);
    expect(sameHash('abcdef0123456789', 'abcd')).toBe(false);
    expect(sameHash('abcdef0123456789', 'abcdef0123456780')).toBe(false);
    expect(sameHash('', '')).toBe(false);
    expect(shortHash('sha256:abcdef0123456789', 6)).toBe('abcdef');
  });
});

describe('sign-in paths', () => {
  it('keeps only paths inside the studio', () => {
    expect(safeNextPath('/results?run=r1')).toBe('/results?run=r1');
    expect(safeNextPath(null)).toBe(HOME_PATH);
    expect(safeNextPath('https://example.com')).toBe(HOME_PATH);
    expect(safeNextPath('//example.com')).toBe(HOME_PATH);
    expect(safeNextPath('/\\example.com')).toBe(HOME_PATH);
    expect(safeNextPath('/login')).toBe(HOME_PATH);
    expect(safeNextPath('/login?next=/x')).toBe(HOME_PATH);
    expect(safeNextPath('/')).toBe(HOME_PATH);
  });

  it('builds the sign-in address with the page to return to', () => {
    expect(loginPath('/queue')).toBe('/login?next=%2Fqueue');
    expect(loginPath('/builder')).toBe('/login');
    expect(loginPath('/login')).toBe('/login');
  });

  it('explains sign-in failures', () => {
    expect(loginErrorMessage(studioError(401, 'not_signed_in'))).toBe('Wrong password. Try again.');
    expect(loginErrorMessage(studioError(429, 'not_signed_in', 'Wait 60 s.'))).toBe('Wait 60 s.');
    expect(loginErrorMessage(studioError(429, 'not_signed_in', ''))).toMatch(/Too many sign-in attempts/);
    expect(loginErrorMessage(studioError(0, 'network_error', 'Cannot reach the studio server.'))).toBe(
      'Cannot reach the studio server.',
    );
    expect(loginErrorMessage(new Error('x'))).toBe('Sign-in failed. Try again.');
  });
});

describe('shouldRetry', () => {
  it('retries network errors and server faults twice', () => {
    for (const error of [studioError(0, 'network_error'), studioError(502, 'network_error'), studioError(500, 'server_error')]) {
      expect(shouldRetry(0, error)).toBe(true);
      expect(shouldRetry(MAX_RETRIES - 1, error)).toBe(true);
      expect(shouldRetry(MAX_RETRIES, error)).toBe(false);
    }
  });

  it('never retries a 4xx or an error from outside the client', () => {
    for (const status of [401, 404, 409, 422, 429]) {
      expect(shouldRetry(0, studioError(status, 'x'))).toBe(false);
    }
    expect(shouldRetry(0, new TypeError('bug'))).toBe(false);
  });
});
