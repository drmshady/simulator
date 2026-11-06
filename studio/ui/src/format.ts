import type { LaneCounts } from './api/types';

/** One decimal below 100 GB, whole numbers above. */
function gbValue(gb: number): string {
  if (!Number.isFinite(gb)) return '–';
  return Math.abs(gb) >= 100 ? Math.round(gb).toString() : (Math.round(gb * 10) / 10).toFixed(1);
}

/** "0.4 GB", "23.1 GB", "412 GB" */
export function formatGb(gb: number): string {
  return Number.isFinite(gb) ? `${gbValue(gb)} GB` : '–';
}

/** "23.1 of 64.0 GB" */
export function formatAvailableOf(available: number, total: number): string {
  return `${gbValue(available)} of ${formatGb(total)}`;
}

export function formatLane(lane: LaneCounts): string {
  return `${lane.running} running · ${lane.queued} queued`;
}

/** Compares two content hashes written with or without a "sha256:" prefix, or with one shortened. */
export function sameHash(a: string, b: string): boolean {
  const x = cleanHash(a);
  const y = cleanHash(b);
  if (!x || !y) return false;
  if (x === y) return true;
  const shorter = Math.min(x.length, y.length);
  return shorter >= 8 && x.slice(0, shorter) === y.slice(0, shorter);
}

export function shortHash(hash: string, length = 10): string {
  return cleanHash(hash).slice(0, length);
}

function cleanHash(hash: string): string {
  return hash.trim().toLowerCase().replace(/^sha256:/, '');
}
