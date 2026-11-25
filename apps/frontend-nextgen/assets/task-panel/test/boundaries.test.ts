// @vitest-environment node
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { expect, it } from 'vitest';
const root = fileURLToPath(new URL('../', import.meta.url));

it('rejects relative escape, host aliases, SDK and undeclared runtime packages', () => {
  const probe = new URL('../scripts/.boundary-probe.mjs', import.meta.url);
  fs.writeFileSync(probe, [
    "import outside from '../../../src/stores/workspaceStore';",
    "import host from '@/services/tasks/taskConfig';",
    "const sdk = require('@tc-chat/ui');",
    "const missing = import('undeclared-task-runtime');",
  ].join('\n'), { flag: 'wx' });
  try {
    const result = spawnSync(process.execPath, ['scripts/check-boundaries.mjs'], { cwd: root, encoding: 'utf8' });
    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain('import escapes independent task package');
    expect(result.stderr).toContain('host/SDK dependency');
    expect(result.stderr).toContain('undeclared package');
  } finally {
    fs.unlinkSync(probe);
  }
});
