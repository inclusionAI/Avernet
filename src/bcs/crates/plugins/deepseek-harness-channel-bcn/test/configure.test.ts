import assert from 'node:assert/strict';
import { chmod, mkdtemp, mkdir, readFile, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { test } from 'node:test';
import { configureProfile } from '../src/configure.js';

test('configures a profile idempotently while preserving other patches and !!js expressions', async () => {
  const dshHome = await createProfile('configure-preserve', `
- id: existing-plugin
  config:
    disabled: !!js process.platform === 'win32'
- id: deepseek-harness-channel-bcn
  config:
    enabled: false
    summary: Custom summary
    domains:
      - coding
`);
  let validations = 0;
  const configure = () => configureProfile({
    profile: 'configure-preserve',
    endpoint: 'http://127.0.0.1:21000/api',
    botName: 'Local DSH Bot',
    dshHome,
  }, { validate: () => { validations += 1; } });

  const first = configure();
  const firstContent = await readFile(first.patchPath, 'utf8');
  configure();
  const secondContent = await readFile(first.patchPath, 'utf8');

  assert.equal(validations, 2);
  assert.equal(first.endpoint, 'http://127.0.0.1:21000/api/');
  assert.equal(secondContent, firstContent);
  assert.match(firstContent, /!!js process\.platform === 'win32'/);
  assert.match(firstContent, /summary: Custom summary/);
  assert.match(firstContent, /endpoint: http:\/\/127\.0\.0\.1:21000\/api\//);
  assert.match(firstContent, /botName: Local DSH Bot/);
  assert.equal((firstContent.match(/id: deepseek-harness-channel-bcn/g) ?? []).length, 1);
});

test('restores the original patch when DSH validation fails', async () => {
  const original = '- id: existing-plugin\n  config:\n    enabled: true\n';
  const dshHome = await createProfile('configure-rollback', original);
  const patchPath = join(dshHome, 'profiles', 'configure-rollback', 'cordis.patch.yml');
  assert.throws(() => configureProfile({
    profile: 'configure-rollback',
    endpoint: 'https://bcn.example.com/',
    botName: 'Rollback Bot',
    dshHome,
  }, { validate: () => { throw new Error('invalid composed config'); } }), /restored the previous patch/);
  assert.equal(await readFile(patchPath, 'utf8'), original);
});

test('rejects invalid profile names and symlinked profile files', async () => {
  const dshHome = await createProfile('configure-safe', '[]\n');
  assert.throws(() => configureProfile({
    profile: '../escape',
    endpoint: 'http://127.0.0.1:21000/',
    botName: 'Safe Bot',
    dshHome,
  }), /profile must match/);

  const profileDir = join(dshHome, 'profiles', 'configure-safe');
  const target = join(profileDir, 'real-patch.yml');
  await writeFile(target, '[]\n');
  const patchPath = join(profileDir, 'cordis.patch.yml');
  await writeFile(patchPath, 'remove-me');
  const { unlink } = await import('node:fs/promises');
  await unlink(patchPath);
  await symlink(target, patchPath);
  assert.throws(() => configureProfile({
    profile: 'configure-safe',
    endpoint: 'http://127.0.0.1:21000/',
    botName: 'Safe Bot',
    dshHome,
  }), /regular file, not a symlink/);
});

test('install script keeps the onboarding Token away from install and configure subprocesses', async () => {
  const root = await mkdtemp(join(tmpdir(), 'dsh-bcn-installer-'));
  const binDir = join(root, 'bin');
  await mkdir(binDir);
  const logPath = join(root, 'calls.jsonl');
  const fakeDsh = join(binDir, 'dsh');
  await writeFile(fakeDsh, `#!/usr/bin/env node
const fs = require('node:fs');
fs.appendFileSync(process.env.CALL_LOG, JSON.stringify({ args: process.argv.slice(2), token: process.env.BCN_ONBOARDING_TOKEN ?? null }) + '\\n');
`);
  await chmod(fakeDsh, 0o755);
  const installer = new URL('../install-dsh.sh', import.meta.url);
  const result = spawnSync('bash', [installer.pathname,
    '--endpoint', 'http://127.0.0.1:21000/',
    '--profile', 'installer-test',
    '--bot-name', 'Installer Bot',
    '--package', join(root, 'package with spaces.tgz'),
  ], {
    encoding: 'utf8',
    env: {
      ...process.env,
      PATH: `${binDir}:${process.env.PATH ?? ''}`,
      CALL_LOG: logPath,
      BCN_ONBOARDING_TOKEN: 'registration-secret',
    },
  });
  assert.equal(result.status, 0, result.stderr);
  assert.doesNotMatch(`${result.stdout}${result.stderr}`, /registration-secret/);
  const calls = (await readFile(logPath, 'utf8')).trim().split('\n').map(line => JSON.parse(line) as {
    args: string[];
    token: string | null;
  });
  assert.equal(calls.length, 3);
  assert.equal(calls[0]?.token, null);
  assert.equal(calls[1]?.token, null);
  assert.equal(calls[2]?.token, 'registration-secret');
  assert.deepEqual(calls[2]?.args, ['--profile', 'installer-test']);
  assert.ok(calls[0]?.args.includes(join(root, 'package with spaces.tgz')));
});

test('install script is valid Bash', () => {
  const installer = new URL('../install-dsh.sh', import.meta.url);
  const result = spawnSync('bash', ['-n', installer.pathname], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
});

async function createProfile(profile: string, patch: string): Promise<string> {
  const dshHome = await mkdtemp(join(tmpdir(), 'dsh-bcn-configure-'));
  const profileDir = join(dshHome, 'profiles', profile);
  await mkdir(profileDir, { recursive: true });
  await writeFile(join(profileDir, 'cordis.patch.yml'), patch);
  return dshHome;
}

test('install script explains how to install DSH when the CLI is missing', async () => {
  const root = await mkdtemp(join(tmpdir(), 'dsh-bcn-missing-cli-'));
  const installer = new URL('../install-dsh.sh', import.meta.url);
  const result = spawnSync('/bin/bash', [installer.pathname,
    '--endpoint', 'http://127.0.0.1:21000/',
    '--profile', 'missing-cli',
  ], {
    encoding: 'utf8',
    env: {
      ...process.env,
      PATH: `${root}:/usr/bin:/bin`,
      BCN_ONBOARDING_TOKEN: 'registration-secret',
    },
  });
  assert.equal(result.status, 1);
  assert.match(`${result.stdout}${result.stderr}`, /npm install --global @deepseek-ai\/dsh@0\.1\.1-rc\.2/);
  assert.doesNotMatch(`${result.stdout}${result.stderr}`, /registration-secret/);
});
