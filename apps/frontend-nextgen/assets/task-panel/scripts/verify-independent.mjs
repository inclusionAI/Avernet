/** Prove this package builds without either frontend host or the backend checkout. */
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
const root = fileURLToPath(new URL('..', import.meta.url));
const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'task-panel-independent-'));
const target = path.join(temporary, 'task-panel');
try {
  await fs.cp(root, target, {
    recursive: true,
    filter: source => !['node_modules', 'dist', '.git'].includes(path.basename(source)),
  });
  for (const args of [['ci'], ['run', 'verify']]) {
    const result = spawnSync(process.platform === 'win32' ? 'npm.cmd' : 'npm', args, {
      cwd: target, stdio: 'inherit', env: { ...process.env, NODE_PATH: '' },
    });
    if (result.error) throw result.error;
    if (result.status !== 0) throw new Error(`isolated npm ${args.join(' ')} failed (${result.status})`);
  }
  console.log('PASS: isolated npm ci + verify, without host source, dependencies or generated outputs');
} finally {
  await fs.rm(temporary, { recursive: true, force: true });
}
