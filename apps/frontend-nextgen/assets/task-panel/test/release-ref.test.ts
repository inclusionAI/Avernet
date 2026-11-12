// @vitest-environment node
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync, spawnSync } from 'node:child_process';
import { expect, it } from 'vitest';
const script = fileURLToPath(new URL('../scripts/check-release-ref.mjs', import.meta.url));

it('allows dev and iteration-only commits without requiring a merge into dev', () => {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'task-panel-release-ref-'));
  const git = (...args: string[]) => execFileSync('git', args, { cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
  const check = () => spawnSync(process.execPath, [script], { cwd, encoding: 'utf8' });
  try {
    git('init');
    git('config', 'maintenance.auto', 'false');
    git('config', 'gc.auto', '0');
    git('config', 'user.name', 'Release Contract Test');
    git('config', 'user.email', 'release-test@example.invalid');
    git('commit', '--allow-empty', '-m', 'base');
    git('update-ref', 'refs/remotes/origin/dev', 'HEAD');
    expect(check().status).toBe(0);
    git('commit', '--allow-empty', '-m', 'iteration-only');
    expect(check().status).not.toBe(0);
    git('update-ref', 'refs/remotes/origin/feature/not-release', 'HEAD');
    expect(check().status).not.toBe(0);
    for (const rejected of ['REL20261112_task', 'REL20261112-fix', 'REL20261029_task_3', 'REL2026111', 'REL202611120', 'RELnotadate']) {
      git('update-ref', `refs/remotes/origin/${rejected}`, 'HEAD');
    }
    expect(check().status).not.toBe(0);
    git('update-ref', 'refs/remotes/origin/REL20261112', 'HEAD');
    const validIteration = check();
    expect(validIteration.status).toBe(0);
    expect(validIteration.stdout).toContain('origin/REL20261112');
    // A release tag may resolve an approved iteration commit even after that branch advances.
    const iteration = git('rev-parse', 'HEAD');
    git('commit', '--allow-empty', '-m', 'iteration-next');
    git('update-ref', 'refs/remotes/origin/REL20261112', 'HEAD');
    git('checkout', '--detach', iteration);
    expect(check().status).toBe(0);
  } finally {
    fs.rmSync(cwd, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
}, 15_000);
