import { defineConfig } from '@playwright/test';

// Smoke tests of key flows against a running studio (research R20).
export default defineConfig({
  testDir: 'tests/e2e',
  use: {
    baseURL: process.env.STUDIO_URL ?? 'https://localhost:8443',
    ignoreHTTPSErrors: true,
  },
});
