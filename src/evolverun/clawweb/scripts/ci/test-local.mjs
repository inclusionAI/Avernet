#!/usr/bin/env node
// Dependency-free entry: can run before npm install on a fresh workspace.
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join, resolve } from 'node:path';
const root = process.cwd();
const npm = process.env.npm_execpath;
if (!npm) throw new Error('Use npm run test:local from the ClawWeb workspace');
const isPublic = resolve(root, 'scripts/ci/test-local.mjs') === fileURLToPath(import.meta.url);
if (!isPublic && !existsSync(join(root, '.build/avernet/src/evolverun/clawweb/package.json'))) {
  throw new Error('Set up the existing Avernet workspace association first; this command does not checkout repositories');
}
const phases = [
  ['install', isPublic ? ['ci', '--include=dev', '--include=optional', '--no-audit', '--no-fund']
    : ['install', '--include=dev', '--include=optional', '--no-audit', '--no-fund']],
  ['build', ['run', 'ci:build']],
  ['check', ['run', 'ci:check']],
  ['test', ['run', 'test:ci']],
];
for (const [phase, args] of phases) {
  console.log(`\n[ClawWeb local tests] ${phase}`);
  // 以下为安全注释COSEC：固定 npm 参数数组，不拼接或执行用户传入的 shell 文本。
  const result = spawnSync(process.execPath, [npm, ...args], { cwd: root, stdio: 'inherit' });
  if (result.error || result.signal || result.status !== 0) {
    console.error(`[ClawWeb local tests] ${phase} failed; ${phase === 'test' ? 'inspect test-results/summary.md' : 'tests NOT_RUN'}`);
    process.exitCode = result.status || 1;
    break;
  }
}
