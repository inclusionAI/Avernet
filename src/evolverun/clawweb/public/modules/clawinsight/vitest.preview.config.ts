import { defineConfig } from 'vitest/config';
/** Preview HTTP acceptance using the shared better-sqlite3 adapter, compatible with Node 20 CI. */
export default defineConfig({ test: { environment: 'node', include: ['preview/__tests__/*.test.ts'], fileParallelism: false } });
