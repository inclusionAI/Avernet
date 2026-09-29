import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

// Browser checks use source package boundaries without requiring built dist artifacts.
export default {
  cacheDir: join(tmpdir(), 'avernet-workflow-repair-web-vitest'),
  esbuild: { jsx: 'automatic' },
  resolve: { alias: { '@avernet/clawweb-shared': fileURLToPath(new URL('../../shared', import.meta.url)) } },
  test: {
    environment: 'jsdom', globals: true, setupFiles: ['./web/test/setup.ts'],
    include: ['web/components/workflow-workspace/repair-batch/__tests__/*.test.tsx', 'web/components/workflow-workspace/__tests__/EvolutionIssueFlow.test.tsx'],
  },
}
