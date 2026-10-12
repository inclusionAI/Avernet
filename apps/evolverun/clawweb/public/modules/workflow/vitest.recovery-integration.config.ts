import { defineConfig } from 'vitest/config';
import { fileURLToPath } from 'node:url';

// Explicitly permit the opt-in peer checkout; ordinary unit tests need no client checkout.
export default defineConfig({
  server: { fs: { allow: [fileURLToPath(new URL('../../', import.meta.url)), ...(process.env.CLAWMIND_ROOT ? [process.env.CLAWMIND_ROOT] : [])] } },
  test: {
    environment: 'node',
    include: ['server/routes/internal/__tests__/recovery-runtime.integration.test.ts'],
  },
});
