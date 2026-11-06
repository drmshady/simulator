import { useQuery } from '@tanstack/react-query';

import { apiGet } from './client';
import type { SystemInfo } from './types';

export const SYSTEM_QUERY_KEY = ['system'] as const;
export const SYSTEM_POLL_MS = 10_000;

/** GET /api/system, polled every 10 s while the interface is visible. */
export function useSystemInfo() {
  return useQuery({
    queryKey: SYSTEM_QUERY_KEY,
    queryFn: ({ signal }) => apiGet<SystemInfo>('/api/system', { signal }),
    refetchInterval: SYSTEM_POLL_MS,
  });
}
