import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

export default {
  cacheDir: join(tmpdir(), 'clawweb-repair-runtime-vitest'),
  resolve: { alias: {
    '@avernet/clawweb-shared': fileURLToPath(new URL('../../shared', import.meta.url)),
    '@avernet/clawevolve': fileURLToPath(new URL('../../modules/clawevolve', import.meta.url)),
    '@avernet/workflow': fileURLToPath(new URL('../../modules/workflow', import.meta.url)),
  } },
  test: { environment: 'node', include: ['server/__tests__/repair-workbench-runtime.test.ts'] },
};
