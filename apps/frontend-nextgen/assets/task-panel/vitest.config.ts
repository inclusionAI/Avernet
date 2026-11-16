import { defineConfig } from 'vitest/config';
export default defineConfig({
  esbuild: { jsx: 'automatic' },
  test: { environment: 'jsdom', globals: true, include: ['test/**/*.test.{ts,tsx}'], setupFiles: ['test/setup.ts'] },
});
