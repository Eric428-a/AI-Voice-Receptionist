import { defineConfig } from 'vitest/config';

// Node global available when Vite loads this file (avoids pulling Node types into browser code).
declare const process: { env: Record<string, string | undefined> };

// The backend runs on 127.0.0.1:8000. 127.0.0.1 (not "localhost") avoids the
// Windows/Node IPv6 resolution issue where localhost -> ::1 while Uvicorn
// listens on IPv4 only.
const BACKEND_HTTP = process.env.VITE_BACKEND_URL ?? 'http://127.0.0.1:8000';
const BACKEND_WS = BACKEND_HTTP.replace(/^http/, 'ws');

export default defineConfig({
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // WebSocket route must be listed before the generic /api route.
      '/api/ws': { target: BACKEND_WS, ws: true },
      '/api': { target: BACKEND_HTTP },
    },
  },
  preview: {
    port: 4173,
    proxy: {
      '/api/ws': { target: BACKEND_WS, ws: true },
      '/api': { target: BACKEND_HTTP },
    },
  },
  build: {
    target: 'es2022',
    sourcemap: true,
  },
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.ts'],
    setupFiles: ['tests/setup.ts'],
    restoreMocks: true,
  },
});
