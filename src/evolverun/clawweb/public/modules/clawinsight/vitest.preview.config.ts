import { defineConfig } from 'vitest/config';
/** Explicit node:sqlite preview acceptance (Node >=22.13); not a production-driver test. */
export default defineConfig({ test: { environment: 'node', include: ['preview/__tests__/*.test.ts'], fileParallelism: false } });
