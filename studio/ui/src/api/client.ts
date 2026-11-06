import type { ErrorBody, KnownErrorCode, MissingItem, Problem } from './types';

const LOGIN_PATH = '/api/auth/login';

const NETWORK_MESSAGE =
  'Cannot reach the studio server. Check that the main PC is switched on and connected to the home network.';

/** Every failed API call rejects with this: the server's error shape, or `network_error` made here. */
export class StudioError extends Error {
  /** HTTP status; 0 when no response arrived. */
  readonly status: number;
  readonly code: string;
  readonly problems: Problem[];
  readonly missing: MissingItem[];

  constructor(status: number, body: ErrorBody) {
    super(body.message);
    this.name = 'StudioError';
    this.status = status;
    this.code = body.code;
    this.problems = body.problems;
    this.missing = body.missing;
  }

  get isNetworkError(): boolean {
    return this.code === 'network_error';
  }
}

export function isStudioError(error: unknown, code?: KnownErrorCode): error is StudioError {
  return error instanceof StudioError && (code === undefined || error.code === code);
}

type UnauthorisedHandler = (error: StudioError) => void;

let unauthorisedHandler: UnauthorisedHandler | null = null;

/** The app registers what a 401 does (go to sign-in). Returns a function that removes this handler. */
export function setOnUnauthorised(handler: UnauthorisedHandler | null): () => void {
  unauthorisedHandler = handler;
  return () => {
    if (unauthorisedHandler === handler) unauthorisedHandler = null;
  };
}

export type QueryValue = string | number | boolean | null | undefined;

/** Appends query parameters, skipping null and undefined; arrays become repeated keys. */
export function withQuery(path: string, params: Record<string, QueryValue | readonly QueryValue[]>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    const values: readonly QueryValue[] = Array.isArray(value) ? value : [value as QueryValue];
    for (const v of values) {
      if (v !== null && v !== undefined) search.append(key, String(v));
    }
  }
  const query = search.toString();
  if (!query) return path;
  return `${path}${path.includes('?') ? '&' : '?'}${query}`;
}

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export interface RequestOptions {
  signal?: AbortSignal;
  headers?: Record<string, string>;
}

/**
 * Calls the studio API. `path` includes the `/api` prefix. A plain value body is sent as JSON; a FormData body
 * (file uploads) is sent as multipart. Resolves with the parsed JSON, or undefined for an empty reply.
 */
export async function apiRequest<T>(
  method: HttpMethod,
  path: string,
  body?: unknown,
  options: RequestOptions = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json', ...options.headers };
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }

  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body: payload,
      credentials: 'same-origin',
      signal: options.signal,
    });
  } catch (error) {
    // Cancellation is not a failure: let TanStack Query and callers see the AbortError itself.
    if (isAbortError(error)) throw error;
    throw networkError();
  }

  if (!response.ok) {
    const error = await errorFromResponse(response);
    if (response.status === 401 && !isLoginPath(path)) unauthorisedHandler?.(error);
    throw error;
  }
  return (await readBody(response)) as T;
}

export function apiGet<T>(path: string, options?: RequestOptions): Promise<T> {
  return apiRequest<T>('GET', path, undefined, options);
}

export function apiPost<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> {
  return apiRequest<T>('POST', path, body, options);
}

export function apiPut<T>(path: string, body: unknown, options?: RequestOptions): Promise<T> {
  return apiRequest<T>('PUT', path, body, options);
}

export function apiPatch<T>(path: string, body: unknown, options?: RequestOptions): Promise<T> {
  return apiRequest<T>('PATCH', path, body, options);
}

export function apiDelete<T = void>(path: string, options?: RequestOptions): Promise<T> {
  return apiRequest<T>('DELETE', path, undefined, options);
}

function isLoginPath(path: string): boolean {
  return path.split('?')[0] === LOGIN_PATH;
}

function isAbortError(error: unknown): boolean {
  return typeof error === 'object' && error !== null && (error as { name?: unknown }).name === 'AbortError';
}

function networkError(message = NETWORK_MESSAGE, status = 0): StudioError {
  return new StudioError(status, { code: 'network_error', message, problems: [], missing: [] });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

const CODE_FOR_STATUS: Record<number, KnownErrorCode> = {
  401: 'not_signed_in',
  404: 'not_found',
  409: 'conflict',
  422: 'validation_failed',
  429: 'not_signed_in',
};

async function errorFromResponse(response: Response): Promise<StudioError> {
  let text = '';
  try {
    text = await response.text();
  } catch {
    // An unreadable body is treated like a non-JSON one.
  }
  const parsed = parseJson(text);
  if (!isRecord(parsed)) {
    // Not the studio's error shape: a proxy or a server that is not running answered.
    return networkError(`The studio server did not answer properly (HTTP ${response.status}).`, response.status);
  }
  const code =
    typeof parsed.code === 'string' ? parsed.code : (CODE_FOR_STATUS[response.status] ?? 'server_error');
  const message =
    typeof parsed.message === 'string'
      ? parsed.message
      : typeof parsed.detail === 'string'
        ? parsed.detail
        : `The studio server refused the request (HTTP ${response.status}).`;
  return new StudioError(response.status, {
    code,
    message,
    problems: Array.isArray(parsed.problems) ? (parsed.problems.filter(isRecord) as unknown as Problem[]) : [],
    missing: Array.isArray(parsed.missing) ? (parsed.missing.filter(isRecord) as unknown as MissingItem[]) : [],
  });
}

async function readBody(response: Response): Promise<unknown> {
  if (response.status === 204 || response.status === 205) return undefined;
  const text = await response.text();
  if (!text) return undefined;
  const type = response.headers.get('Content-Type') ?? '';
  if (!type.includes('json')) return text;
  try {
    return JSON.parse(text);
  } catch {
    throw networkError(`The studio server sent an unreadable reply (HTTP ${response.status}).`, response.status);
  }
}
