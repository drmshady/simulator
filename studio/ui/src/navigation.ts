import type { ComponentType } from 'react';

export interface PageEntry {
  /** Route path below "/", also the navigation link. */
  path: string;
  label: string;
  /** Each page is its own chunk, so the 3D viewer code loads only with the pages that use it. */
  load: () => Promise<{ default: ComponentType }>;
}

export const PAGES: readonly PageEntry[] = [
  { path: 'builder', label: 'Builder', load: () => import('./pages/Builder') },
  { path: 'results', label: 'Results', load: () => import('./pages/Results') },
  { path: 'compare', label: 'Compare', load: () => import('./pages/Compare') },
  { path: 'projects', label: 'Projects', load: () => import('./pages/Projects') },
  { path: 'queue', label: 'Queue', load: () => import('./pages/Queue') },
  { path: 'library', label: 'Library', load: () => import('./pages/Library') },
  { path: 'verification', label: 'Verification', load: () => import('./pages/Verification') },
  { path: 'exports', label: 'Exports', load: () => import('./pages/Exports') },
  { path: 'publish', label: 'Publish', load: () => import('./pages/Publish') },
  { path: 'physics', label: 'Physics', load: () => import('./pages/Physics') },
];
