import { tmpdir } from 'node:os';
import { join } from 'node:path';

// Storage checkpoint runs without browser dependencies or writing shared dependencies.
export default {
  cacheDir: join(tmpdir(), 'avernet-workflow-repair-v2-vitest'),
  resolve: { alias: [{ find: /^@avernet\/clawweb-shared\/server\/(.*)$/, replacement: new URL('../../shared/server/', import.meta.url).pathname + '$1.ts' }] },
  test: {
    environment: 'node',
    include: ['server/**/__tests__/repair-batch*.test.ts', '../clawevolve/server/routes/__tests__/evolve-repair-isolation.test.ts'],
  },
};
