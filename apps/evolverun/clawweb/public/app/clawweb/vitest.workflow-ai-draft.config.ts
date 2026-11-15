import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
export default {
  cacheDir: join(tmpdir(), 'clawweb-workflow-ai-draft-vitest'),
  resolve: { alias: {
    '@avernet/clawweb-shared': fileURLToPath(new URL('../../shared', import.meta.url)),
    '@avernet/workflow': fileURLToPath(new URL('../../modules/workflow', import.meta.url)),
  } },
  test: { environment: 'node', include: ['server/__tests__/workflow-ai-draft*.test.ts', 'server/__tests__/workflow-ai-browser-contract.test.ts', 'server/__tests__/public-workflow-ai-draft.test.ts'], testTimeout: 20_000 },
};
