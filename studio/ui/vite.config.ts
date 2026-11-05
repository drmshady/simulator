import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // The studio server on this PC. Its certificate comes from the studio's own local CA, which Node does not
      // trust, so certificate checks are off for this development proxy only.
      '/api': { target: 'https://localhost:8443', changeOrigin: true, secure: false },
    },
  },
  test: {
    include: ['tests/**/*.test.{ts,tsx}'],
    passWithNoTests: true,
  },
});
