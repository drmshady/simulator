import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  apiDelete,
  apiGet,
  apiPost,
  apiPut,
  isStudioError,
  setOnUnauthorised,
  StudioError,
  withQuery,
} from '../src/api/client';

const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

async function rejection(promise: Promise<unknown>): Promise<StudioError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof StudioError) return error;
    throw error;
  }
  throw new Error('expected the call to fail');
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  setOnUnauthorised(null);
  vi.unstubAllGlobals();
});

describe('requests', () => {
  it('sends JSON with same-origin credentials and parses the reply', async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, { signed_in: true }));
    const result = await apiPost<{ signed_in: boolean }>('/api/auth/login', { password: 'pw' });
    expect(result).toEqual({ signed_in: true });
    const [path, init] = fetchMock.mock.calls[0]!;
    expect(path).toBe('/api/auth/login');
    expect(init?.method).toBe('POST');
    expect(init?.credentials).toBe('same-origin');
    expect(init?.body).toBe(JSON.stringify({ password: 'pw' }));
    expect((init?.headers as Record<string, string>)['Content-Type']).toBe('application/json');
  });

  it('sends no body and no content type for a GET', async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, []));
    await apiGet('/api/projects');
    const init = fetchMock.mock.calls[0]![1];
    expect(init?.method).toBe('GET');
    expect(init?.body).toBeUndefined();
    expect((init?.headers as Record<string, string>)['Content-Type']).toBeUndefined();
  });

  it('passes FormData through as multipart', async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, { imported: 2 }));
    const form = new FormData();
    form.append('file', new Blob(['a: 1']), 'entries.yaml');
    await apiPost('/api/library/import', form);
    const init = fetchMock.mock.calls[0]![1];
    expect(init?.body).toBe(form);
    expect((init?.headers as Record<string, string>)['Content-Type']).toBeUndefined();
  });

  it('resolves undefined for 204 and for an empty body', async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await expect(apiPost('/api/auth/logout')).resolves.toBeUndefined();
    fetchMock.mockResolvedValueOnce(new Response('', { status: 200 }));
    await expect(apiDelete('/api/projects/p1')).resolves.toBeUndefined();
  });

  it('uses PUT with the body', async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, { revision: 4 }));
    await apiPut('/api/scenarios/s1', { revision: 3, scenario: {} });
    expect(fetchMock.mock.calls[0]![1]?.method).toBe('PUT');
  });
});

describe('error mapping', () => {
  it('maps the studio error shape to a StudioError', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(409, {
        code: 'run_refused',
        message: 'The assembly is not valid.',
        problems: [{ code: 'in_nerve_canal', block: 'implant-1', location_mm: [12, 0.4, -9.1], message: 'Apex in canal.' }],
        missing: [{ item: 'benchmark:bonded_elastic', message: 'Benchmarks have not passed.' }],
      }),
    );
    const error = await rejection(apiPost('/api/scenarios/s1/runs', { detail_level: 'standard' }));
    expect(error).toBeInstanceOf(StudioError);
    expect(error.status).toBe(409);
    expect(error.code).toBe('run_refused');
    expect(error.message).toBe('The assembly is not valid.');
    expect(error.problems).toHaveLength(1);
    expect(error.problems[0]?.block).toBe('implant-1');
    expect(error.missing[0]?.item).toBe('benchmark:bonded_elastic');
    expect(isStudioError(error, 'run_refused')).toBe(true);
    expect(isStudioError(error, 'conflict')).toBe(false);
  });

  it('defaults problems and missing to empty lists', async () => {
    fetchMock.mockResolvedValue(jsonResponse(404, { code: 'not_found', message: 'No such run.' }));
    const error = await rejection(apiGet('/api/runs/r9'));
    expect(error.code).toBe('not_found');
    expect(error.problems).toEqual([]);
    expect(error.missing).toEqual([]);
  });

  it('falls back to a code from the status for JSON without the studio shape', async () => {
    fetchMock.mockResolvedValue(jsonResponse(422, { detail: 'Field required' }));
    const error = await rejection(apiPost('/api/projects', {}));
    expect(error.code).toBe('validation_failed');
    expect(error.message).toBe('Field required');
  });

  it('treats a non-JSON error body as a network error and keeps the status', async () => {
    fetchMock.mockResolvedValue(new Response('<html>Bad gateway</html>', { status: 502 }));
    const error = await rejection(apiGet('/api/system'));
    expect(error.code).toBe('network_error');
    expect(error.isNetworkError).toBe(true);
    expect(error.status).toBe(502);
    expect(error.message).toContain('502');
  });

  it('maps a failed fetch to a network error with status 0', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));
    const error = await rejection(apiGet('/api/system'));
    expect(error.code).toBe('network_error');
    expect(error.status).toBe(0);
    expect(error.message).toMatch(/Cannot reach the studio server/);
  });

  it('rethrows an abort untouched', async () => {
    const abort = new DOMException('The operation was aborted.', 'AbortError');
    fetchMock.mockRejectedValue(abort);
    await expect(apiGet('/api/system')).rejects.toBe(abort);
  });

  it('treats an unreadable JSON success body as a network error', async () => {
    fetchMock.mockResolvedValue(new Response('{oops', { status: 200, headers: { 'Content-Type': 'application/json' } }));
    const error = await rejection(apiGet('/api/system'));
    expect(error.code).toBe('network_error');
  });
});

describe('401 handling', () => {
  it('calls the registered handler on a 401', async () => {
    const handler = vi.fn();
    setOnUnauthorised(handler);
    fetchMock.mockResolvedValue(jsonResponse(401, { code: 'not_signed_in', message: 'Sign in again.' }));
    const error = await rejection(apiGet('/api/system'));
    expect(error.code).toBe('not_signed_in');
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler).toHaveBeenCalledWith(error);
  });

  it('does not call it for the login call itself', async () => {
    const handler = vi.fn();
    setOnUnauthorised(handler);
    fetchMock.mockResolvedValue(jsonResponse(401, { code: 'not_signed_in', message: 'Wrong password.' }));
    await rejection(apiPost('/api/auth/login', { password: 'wrong' }));
    expect(handler).not.toHaveBeenCalled();
  });

  it('does not call it for other errors, including a rate-limited login', async () => {
    const handler = vi.fn();
    setOnUnauthorised(handler);
    fetchMock.mockResolvedValueOnce(jsonResponse(403, { code: 'forbidden', message: 'No.' }));
    await rejection(apiGet('/api/system'));
    fetchMock.mockResolvedValueOnce(jsonResponse(429, { code: 'not_signed_in', message: 'Too many attempts.' }));
    const limited = await rejection(apiPost('/api/auth/login', { password: 'x' }));
    expect(limited.status).toBe(429);
    expect(handler).not.toHaveBeenCalled();
  });

  it('removes the handler only if it is still the registered one', async () => {
    const first = vi.fn();
    const second = vi.fn();
    const removeFirst = setOnUnauthorised(first);
    setOnUnauthorised(second);
    removeFirst();
    fetchMock.mockResolvedValue(jsonResponse(401, { code: 'not_signed_in', message: '' }));
    await rejection(apiGet('/api/system'));
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });
});

describe('withQuery', () => {
  it('appends values, repeats arrays and skips empty ones', () => {
    expect(withQuery('/api/search', { q: 'crown', tag: ['a', 'b'], setting: undefined, page: 2, x: null })).toBe(
      '/api/search?q=crown&tag=a&tag=b&page=2',
    );
    expect(withQuery('/api/search?q=1', { tag: 'x' })).toBe('/api/search?q=1&tag=x');
    expect(withQuery('/api/library/entries', {})).toBe('/api/library/entries');
  });
});
