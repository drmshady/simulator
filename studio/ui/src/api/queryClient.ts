import { QueryClient } from '@tanstack/react-query';

import { StudioError } from './client';

export const MAX_RETRIES = 2;

/** Retry only what may succeed a moment later: no reply from the server, or a server fault. Never a 4xx. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (!(error instanceof StudioError)) return false;
  if (error.isNetworkError || error.status >= 500) return failureCount < MAX_RETRIES;
  return false;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: shouldRetry,
        refetchOnWindowFocus: true,
        staleTime: 5_000,
      },
      mutations: {
        retry: false,
      },
    },
  });
}
