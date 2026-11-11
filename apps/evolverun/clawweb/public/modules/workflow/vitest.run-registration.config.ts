import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

// These HTTP/SQLite contract tests do not need the optional browser toolchain.
export default {
  cacheDir: join(tmpdir(), 'avernet-workflow-run-registration-vitest'),
  resolve: { alias: {
    '@avernet/clawweb-shared': fileURLToPath(new URL('../../shared', import.meta.url)),
    '@avernet/clawweb': fileURLToPath(new URL('../../app/clawweb', import.meta.url)),
    '@avernet/workflow': fileURLToPath(new URL('.', import.meta.url)),
    '@avernet/clawevolve': fileURLToPath(new URL('../clawevolve', import.meta.url)),
    '@avernet/clawinsight': fileURLToPath(new URL('../clawinsight', import.meta.url)),
  } },
  test: { environment: 'node', include: ['server/**/__tests__/*.test.ts'], testTimeout: 15_000 },
};
