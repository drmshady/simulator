import { apiGet, apiPost, isStudioError } from './client';
import type { LoginResponse, SessionInfo } from './types';

export const SESSION_QUERY_KEY = ['auth', 'session'] as const;

export const HOME_PATH = '/builder';
export const LOGIN_ROUTE = '/login';

export function login(password: string): Promise<LoginResponse> {
  return apiPost<LoginResponse>('/api/auth/login', { password });
}

export function logout(): Promise<void> {
  return apiPost<void>('/api/auth/logout');
}

export function getSession(signal?: AbortSignal): Promise<SessionInfo> {
  return apiGet<SessionInfo>('/api/auth/session', { signal });
}

/** The sign-in page's address, remembering where the owner was. */
export function loginPath(from: string): string {
  const next = safeNextPath(from);
  return next === HOME_PATH ? LOGIN_ROUTE : `${LOGIN_ROUTE}?next=${encodeURIComponent(next)}`;
}

/** Where to go after signing in: only a path inside the studio, never another site or the sign-in page. */
export function safeNextPath(next: string | null | undefined): string {
  if (!next || !next.startsWith('/') || next.startsWith('//') || next.includes('\\')) return HOME_PATH;
  if (next === '/' || next === LOGIN_ROUTE || next.startsWith(`${LOGIN_ROUTE}?`) || next.startsWith(`${LOGIN_ROUTE}/`)) {
    return HOME_PATH;
  }
  return next;
}

export function loginErrorMessage(error: unknown): string {
  if (!isStudioError(error)) return 'Sign-in failed. Try again.';
  if (error.status === 429) return error.message || 'Too many sign-in attempts. Wait a minute, then try again.';
  if (error.status === 401) return 'Wrong password. Try again.';
  return error.message;
}
