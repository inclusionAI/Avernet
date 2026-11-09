// @vitest-environment node
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { withGitOperation } from '@avernet/clawweb-shared/server/services/git-execution';
import { fetchPackVersionRefs, preparePackVersionRepo } from '../pack-version-git-read.js';
import { readPackScriptSnapshot } from '../pack-script-snapshot.js';
import { createLocalRemote, credentialInput, runGit } from './clawflows-git-client-fixture.js';

const hooks = vi.hoisted(() => ({ after: undefined as undefined | ((args: string[]) => void), bridge: '', auth: '', secret: '', failRemote: false, failFetchCode: '', discoveryOverride: undefined as string | undefined, calls: [] as string[][], envs: [] as Array<{ args: string[]; env: NodeJS.ProcessEnv | undefined }> }));
vi.mock('@avernet/clawweb-shared/server/services/git-execution', async original => {
  const actual = await original<typeof import('@avernet/clawweb-shared/server/services/git-execution')>();
  return { ...actual, executeGit: async (args: string[], options: Parameters<typeof actual.executeGit>[1]) => {
    hooks.calls.push(args); hooks.envs.push({ args, env: options?.env });
    if (args[0] === 'ls-remote' && hooks.discoveryOverride !== undefined) return Buffer.from(hooks.discoveryOverride);
    if (hooks.secret && JSON.stringify(args).includes(hooks.secret)) throw new Error('Credential leaked to argv');
    if (hooks.failFetchCode && args[0] === 'fetch') throw Object.assign(new Error('synthetic secret stderr'), { code: hooks.failFetchCode });
    if (hooks.failRemote && args[0] === 'ls-remote') {
      return actual.executeGit(['ls-remote', '--', `/missing/${hooks.secret}`], options);
    }
    let actualArgs = args;
    if (hooks.bridge && (args[0] === 'ls-remote' || args[0] === 'fetch')) {
      const filled = runGit(String(options?.cwd), ['credential', 'fill'], credentialInput(hooks.auth));
      const entries = filled.split('\n').map(line => { const index = line.indexOf('='); return [line.slice(0, index), line.slice(index + 1)]; });
      expect(Object.fromEntries(entries)).toHaveProperty('password', hooks.secret);
      actualArgs = args.map(arg => arg === 'origin' ? hooks.bridge : arg);
    }
    const result = await actual.executeGit(actualArgs, options);
    hooks.after?.(args);
    return result;
  } };
});
let root: string, bare: string, seed: string, repo: string, current: string, draft: string, unrelated: string;
const cfg = () => ({ remoteUrl: bare, username: 'fixture', email: 'fixture@example.invalid' });
const remote = (...args: string[]) => runGit(root, ['--git-dir', bare, ...args]);
const initRead = async () => withGitOperation({}, async () => { await preparePackVersionRepo(repo, cfg(), 'flow'); return fetchPackVersionRefs(repo, cfg(), 'flow'); });

beforeEach(() => {
  root = mkdtempSync(path.join(tmpdir(), 'pack-read-')); bare = createLocalRemote(root); seed = path.join(root, 'seed');
  repo = path.join(root, 'read'); mkdirSync(repo);
  // A unique default-branch commit and blob must not be fetched.
  writeFileSync(path.join(seed, 'unrelated-only'), 'do not fetch');
  runGit(seed, ['add', '.']); runGit(seed, ['commit', '-m', 'unrelated']); unrelated = runGit(seed, ['rev-parse', 'HEAD']);
  runGit(seed, ['push', bare, 'HEAD:refs/heads/other']);
  runGit(seed, ['reset', '--hard', 'HEAD~1']);
  mkdirSync(path.join(seed, 'packs/pack/workflows'), { recursive: true });
  writeFileSync(path.join(seed, 'packs/pack/workflow.pack.yaml'), 'id: pack\nworkflows: [flow]\n');
  writeFileSync(path.join(seed, 'packs/pack/workflows/flow.yaml'), 'id: flow\n');
  writeFileSync(path.join(seed, 'packs/pack/blob.bin'), Buffer.from([0, 255, 12]));
  runGit(seed, ['add', '.']); runGit(seed, ['commit', '-m', 'current']); current = runGit(seed, ['rev-parse', 'HEAD']);
  writeFileSync(path.join(seed, 'packs/pack/blob.bin'), Buffer.from([1, 2, 3]));
  runGit(seed, ['add', '.']); runGit(seed, ['commit', '-m', 'draft']); draft = runGit(seed, ['rev-parse', 'HEAD']);
  runGit(seed, ['push', bare, `${current}:refs/heads/wf/flow`, `${draft}:refs/heads/saved/flow`]);
  remote('symbolic-ref', 'HEAD', 'refs/heads/not-present');
  hooks.calls = []; hooks.envs = []; hooks.after = undefined; hooks.bridge = ''; hooks.auth = ''; hooks.secret = ''; hooks.failRemote = false; hooks.failFetchCode = ''; hooks.discoveryOverride = undefined;
});
afterEach(() => { hooks.after = undefined; vi.unstubAllEnvs(); rmSync(root, { recursive: true, force: true }); });

it('fetches only exact tips with complete blobs, without default branch, other objects, tags or remote writes', async () => {
  remote('update-ref', 'refs/tags/unrelated', unrelated);
  const before = remote('show-ref');
  const verify = await initRead();
  expect(hooks.calls.filter(args => args[0] === 'init')).toEqual([['init', '--quiet']]);
  const a = await readPackScriptSnapshot(repo, 'flow', 'pack', 'draft', { includeVersionFiles: true });
  await verify();
  expect(a).toMatchObject({ baseCommit: current, draftHead: current, baseRef: 'origin/wf/flow' });
  expect(a.versionFiles).toContainEqual({ path: 'blob.bin', mode: '100644', contentBase64: 'AP8M' });
  expect(() => runGit(repo, ['cat-file', '-e', draft])).toThrow();
  expect(runGit(repo, ['for-each-ref', '--format=%(refname)']).split('\n').sort()).toEqual(['refs/remotes/origin/wf/flow']);
  expect(() => runGit(repo, ['cat-file', '-e', unrelated])).toThrow();
  expect(remote('show-ref')).toBe(before);
  const fetch = hooks.calls.find(args => args[0] === 'fetch')!;
  expect(fetch).toContain('--depth=1'); expect(fetch).toContain('--no-tags');
  expect(fetch.filter(arg => arg.startsWith('+refs/heads/'))).toEqual([
    '+refs/heads/wf/flow:refs/remotes/origin/wf/flow',
  ]);
});

it.each(['wf', 'saved', 'both'])('preserves missing %s ref for the existing not-found behavior', async missing => {
  if (missing !== 'saved') remote('update-ref', '-d', 'refs/heads/wf/flow');
  if (missing !== 'wf') remote('update-ref', '-d', 'refs/heads/saved/flow');
  const verify = await initRead(); await verify();
  if (missing !== 'saved') await expect(readPackScriptSnapshot(repo, 'flow', 'pack', 'draft', { includeVersionFiles: true })).rejects.toThrow('Workflow snapshot not found');
  else expect((await readPackScriptSnapshot(repo, 'flow', 'pack', 'draft', { includeVersionFiles: true })).baseCommit).toBe(current);
});

it.each(['--upload-pack=evil', 'flow:refs/heads/main', 'flow*', 'a/b', 'flow..x', 'flow.lock', 'flow\nmain', 'flow\n'])('rejects injected id %s before invoking Git', async id => {
  await expect(preparePackVersionRepo(repo, cfg(), id)).rejects.toThrow('Invalid workflowId');
  expect(hooks.calls).toEqual([]);
});

it('fails when a ref changes between discovery and fetch', async () => {
  let first = true;
  hooks.after = args => { if (args[0] === 'ls-remote' && first) { first = false; remote('update-ref', 'refs/heads/wf/flow', draft); } };
  await expect(initRead()).rejects.toMatchObject({ code: 'PACK_REFS_CHANGED' });
});
it.each(['update', 'delete', 'create'])('ignores saved ref %s after fetching the wf tip', async action => {
  if (action === 'create') remote('update-ref', '-d', 'refs/heads/saved/flow');
  const verify = await initRead();
  await readPackScriptSnapshot(repo, 'flow', 'pack', 'draft', { includeVersionFiles: true });
  if (action === 'delete') remote('update-ref', '-d', 'refs/heads/saved/flow');
  else remote('update-ref', 'refs/heads/saved/flow', action === 'create' ? draft : current);
  await verify();
});

it.each(['explicit token', 'embedded password'])('reuses scoped HTTPS credential helpers with %s without exposing credentials', async source => {
  hooks.bridge = bare; hooks.auth = 'https://git.example.invalid/team/repo.git'; hooks.secret = 'test-token';
  const url = new URL(hooks.auth);
  if (source === 'embedded password') { url.username = 'fixture'; url.password = hooks.secret; }
  const config = { ...cfg(), remoteUrl: url.href, token: source === 'explicit token' ? hooks.secret : undefined };
  const verify = await withGitOperation({}, async () => { await preparePackVersionRepo(repo, config, 'flow'); return fetchPackVersionRefs(repo, config, 'flow'); });
  await verify();
  expect(hooks.calls.filter(args => ['ls-remote', 'fetch'].includes(args[0]))).toHaveLength(3);
  expect(readFileSync(path.join(repo, '.git/config'), 'utf8')).not.toContain(hooks.secret);
  expect(JSON.stringify(hooks.calls)).not.toContain(hooks.secret);
});

it('saved changes during a draft read do not invalidate wf', async () => {
  const { readPackVersionFromRemote } = await import('../pack-version-remote.js');
  let changed = false;
  hooks.after = args => {
    if (!changed && args[0] === 'cat-file' && args[1] === 'blob') {
      changed = true; remote('update-ref', 'refs/heads/saved/flow', current);
    }
  };
  const db = { query: async () => [] } as never;
  await expect(readPackVersionFromRemote({ db, gitConfig: cfg(), gitAuthor: { name: 'fixture', email: 'fixture@example.invalid' },
    workflowId: 'flow', packId: 'pack', version: 'draft' })).resolves.toMatchObject({ baseCommit: current, baseRef: 'origin/wf/flow' });
  expect(changed).toBe(true);
});

it('the remote service verifies a missing snapshot before reporting not found', async () => {
  const { readPackVersionFromRemote } = await import('../pack-version-remote.js');
  remote('update-ref', '-d', 'refs/heads/wf/flow');
  let discoveries = 0;
  hooks.after = args => {
    if (args[0] === 'ls-remote' && ++discoveries === 1) remote('update-ref', 'refs/heads/wf/flow', current);
  };
  const db = { query: async () => [] } as never;
  await expect(readPackVersionFromRemote({ db, gitConfig: cfg(), gitAuthor: { name: 'fixture', email: 'fixture@example.invalid' },
    workflowId: 'flow', packId: 'pack', version: 'draft' })).rejects.toMatchObject({ code: 'PACK_REFS_CHANGED' });
});

it('GET returns the existing 404 for absent selected refs', async () => {
  const express = (await import('express')).default;
  const { once } = await import('node:events');
  const { createPackVersionRouter } = await import('../../routes/pack-version.js');
  const { readPackVersionFromRemote } = await import('../pack-version-remote.js');
  remote('update-ref', '-d', 'refs/heads/wf/flow');
  remote('update-ref', '-d', 'refs/heads/saved/flow');
  const db = { query: async () => [] } as never;
  const app = express();
  app.use(createPackVersionRouter({ resolvePrincipal: async () => ({ actorId: 'editor' }),
    botPermRepo: { hasEditPermission: async () => true },
    workflowSpecRepo: { findByWorkflowId: async () => ({ workflow_id: 'flow', pack_id: 'pack' }) },
    readSnapshot: (workflowId, packId, version) => readPackVersionFromRemote({ db, gitConfig: cfg(),
      gitAuthor: { name: 'fixture', email: 'fixture@example.invalid' }, workflowId, packId, version }),
  }));
  const server = app.listen(0, '127.0.0.1');
  try {
    await once(server, 'listening');
    const address = server.address(); if (!address || typeof address === 'string') throw new Error('No address');
    for (const version of ['current', 'draft']) {
      const response = await fetch(`http://127.0.0.1:${address.port}/flow/pack-version?version=${version}`);
      expect(response.status).toBe(404);
      expect(await response.json()).toMatchObject({ code: 'SNAPSHOT_NOT_FOUND' });
    }
  } finally { await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve())); }
});


it('sanitizes Git failures and removes temporary HTTPS credentials', async () => {
  const { readPackVersionFromRemote } = await import('../pack-version-remote.js');
  const { credentialFiles } = await import('./clawflows-git-client-fixture.js');
  hooks.secret = 'test-token'; hooks.failRemote = true;
  vi.stubEnv('CLAWWEB_TEMP_DIR', root);
  let caught: unknown;
  try {
    await readPackVersionFromRemote({ db: { query: async () => [] } as never,
      gitConfig: { ...cfg(), remoteUrl: 'https://git.example.invalid/team/repo.git', token: hooks.secret },
      gitAuthor: { name: 'fixture', email: 'fixture@example.invalid' }, workflowId: 'flow', packId: 'pack', version: 'draft' });
  } catch (error) { caught = error; }
  expect(caught).toBeInstanceOf(Error);
  expect(String(caught)).not.toContain(hooks.secret);
  expect(JSON.stringify(caught)).not.toContain(hooks.secret);
  expect(credentialFiles(root)).toEqual([]);
});

it('emits fixed phase categories without logging provider error text', async () => {
  const { packVersionPhase } = await import('../pack-version-observability.js');
  const log = vi.spyOn(console, 'info').mockImplementation(() => {});
  try {
    for (const phase of ['git_config', 'git_prepare', 'git_fetch', 'snapshot', 'git_verify'] as const) {
      await packVersionPhase(phase, async () => 'ok');
    }
    await expect(packVersionPhase('git_fetch', async () => { throw new Error('provider secret details'); })).rejects.toThrow();
    const text = JSON.stringify(log.mock.calls);
    for (const phase of ['git_config', 'git_prepare', 'git_fetch', 'snapshot', 'git_verify']) expect(text).toContain(phase);
    expect(text).toContain('PACK_VERSION_STAGE_FAILED');
    expect(text).not.toContain('provider secret details');
  } finally { log.mockRestore(); }
});

it('reads a SHA-256 remote using a matching object database', async () => {
  const remote256 = path.join(root, 'sha256.git'), work = path.join(root, 'sha256-work'), dest = path.join(root, 'sha256-read');
  mkdirSync(work); mkdirSync(dest);
  runGit(root, ['init', '--bare', '--object-format=sha256', remote256]);
  runGit(work, ['init', '--object-format=sha256']);
  runGit(work, ['config', 'user.name', 'fixture']); runGit(work, ['config', 'user.email', 'fixture@example.invalid']);
  mkdirSync(path.join(work, 'workflows'));
  writeFileSync(path.join(work, 'workflow.pack.yaml'), 'id: pack\nworkflows: [flow]\n');
  writeFileSync(path.join(work, 'workflows/flow.yaml'), 'id: flow\n');
  runGit(work, ['add', '.']); runGit(work, ['commit', '-m', 'sha256']);
  runGit(work, ['push', remote256, 'HEAD:refs/heads/wf/flow']);
  const config = { ...cfg(), remoteUrl: remote256 };
  await withGitOperation({}, async () => {
    await preparePackVersionRepo(dest, config, 'flow');
    const verify = await fetchPackVersionRefs(dest, config, 'flow');
    const snapshot = await readPackScriptSnapshot(dest, 'flow', 'pack', 'draft', { includeVersionFiles: true });
    expect(snapshot.baseCommit).toMatch(/^[a-f0-9]{64}$/);
    await verify();
  });
  expect(hooks.calls.filter(args => args[0] === 'init')).toEqual([
    ['init', '--quiet'], ['init', '--quiet', '--object-format=sha256'],
  ]);
});


it('preserves trusted SSH and TLS transport settings while isolating dangerous Git overrides', async () => {
  vi.stubEnv('GIT_SSH_COMMAND', 'ssh -F /trusted/ssh_config');
  vi.stubEnv('GIT_SSH', '/trusted/ssh');
  vi.stubEnv('GIT_SSL_CAINFO', '/trusted/ca.pem');
  vi.stubEnv('GIT_DIR', '/untrusted/repo');
  vi.stubEnv('GIT_OBJECT_DIRECTORY', '/untrusted/objects');
  vi.stubEnv('GIT_CONFIG_COUNT', '1');
  vi.stubEnv('GIT_CONFIG_KEY_0', 'core.hooksPath');
  vi.stubEnv('GIT_CONFIG_VALUE_0', '/untrusted/hooks');
  const verify = await initRead(); await verify();
  for (const { args, env } of hooks.envs.filter(x => ['init', 'ls-remote', 'fetch'].includes(x.args[0]))) {
    expect(env?.GIT_SSH_COMMAND, args[0]).toBe('ssh -F /trusted/ssh_config');
    expect(env?.GIT_SSH).toBe('/trusted/ssh');
    expect(env?.GIT_SSL_CAINFO).toBe('/trusted/ca.pem');
    expect(env?.GIT_DIR).toBeUndefined(); expect(env?.GIT_OBJECT_DIRECTORY).toBeUndefined();
    expect(env?.GIT_CONFIG_COUNT).toBeUndefined(); expect(env?.GIT_CONFIG_KEY_0).toBeUndefined();
    expect(env?.GIT_CONFIG_NOSYSTEM).toBe('1'); expect(env?.GIT_CONFIG_GLOBAL).toBe('/dev/null');
  }
});

function assertProtocolEnvironment(allow: string, expectedCommands: string[]): void {
  for (const command of expectedCommands) {
    const matches = hooks.envs.filter(item => item.args[0] === command);
    expect(matches.length, command).toBeGreaterThan(0);
    for (const { env } of matches) {
      expect(env?.GIT_ALLOW_PROTOCOL, command).toBe(allow);
      expect(env?.GIT_DIR).toBeUndefined();
      expect(env?.GIT_OBJECT_DIRECTORY).toBeUndefined();
      expect(env?.GIT_CONFIG_COUNT).toBeUndefined();
      expect(env?.GIT_CONFIG_KEY_0).toBeUndefined();
      expect(env?.GIT_CONFIG_VALUE_0).toBeUndefined();
      expect(env?.GIT_CONFIG_PARAMETERS).toBeUndefined();
      expect(env?.GIT_CONFIG_SYSTEM).toBeUndefined();
      expect(env?.GIT_CONFIG_NOSYSTEM).toBe('1');
      expect(env?.GIT_CONFIG_GLOBAL).toBe('/dev/null');
    }
  }
}
function stubUnsafeGitEnvironment(): void {
  vi.stubEnv('GIT_DIR', '/untrusted/repo');
  vi.stubEnv('GIT_OBJECT_DIRECTORY', '/untrusted/objects');
  vi.stubEnv('GIT_CONFIG_COUNT', '1');
  vi.stubEnv('GIT_CONFIG_KEY_0', 'core.hooksPath');
  vi.stubEnv('GIT_CONFIG_VALUE_0', '/untrusted/hooks');
  vi.stubEnv('GIT_CONFIG_PARAMETERS', 'untrusted');
  vi.stubEnv('GIT_CONFIG_SYSTEM', '/untrusted/gitconfig');
}

it('honors an allowed file protocol across real init, discovery, fetch, and verification', async () => {
  vi.stubEnv('GIT_ALLOW_PROTOCOL', 'file');
  stubUnsafeGitEnvironment();
  const verify = await initRead(); await verify();
  assertProtocolEnvironment('file', ['init', 'remote', 'config', 'ls-remote', 'fetch']);
});

it('blocks real local-remote discovery when the allowed protocol is HTTPS only', async () => {
  vi.stubEnv('GIT_ALLOW_PROTOCOL', 'https');
  stubUnsafeGitEnvironment();
  await expect(initRead()).rejects.toMatchObject({ code: 'GIT_COMMAND_FAILED' });
  expect(hooks.calls.filter(args => args[0] === 'fetch')).toHaveLength(0);
  assertProtocolEnvironment('https', ['init', 'remote', 'config', 'ls-remote']);
});

it('blocks a real local fetch when discovery succeeds but HTTPS is the only allowed protocol', async () => {
  // Supply a real advertisement obtained before restricting the environment;
  // only discovery is stubbed, so the attempted fetch still runs real Git.
  hooks.discoveryOverride = runGit(root, ['ls-remote', bare, 'refs/heads/wf/flow']);
  vi.stubEnv('GIT_ALLOW_PROTOCOL', 'https');
  stubUnsafeGitEnvironment();
  await expect(initRead()).rejects.toMatchObject({ code: 'GIT_COMMAND_FAILED' });
  const fetches = hooks.calls.filter(args => args[0] === 'fetch');
  expect(fetches).toHaveLength(1);
  expect(fetches[0]).toContain('--depth=1');
  assertProtocolEnvironment('https', ['init', 'remote', 'config', 'ls-remote', 'fetch']);
});

it('classifies deletion during fetch as PACK_REFS_CHANGED', async () => {
  let first = true;
  hooks.after = args => { if (args[0] === 'ls-remote' && first) { first = false; remote('update-ref', '-d', 'refs/heads/wf/flow'); } };
  await expect(initRead()).rejects.toMatchObject({ code: 'PACK_REFS_CHANGED' });
});

it('fails closed on dumb HTTP without retrying or fetching unrelated refs', async () => {
  const express = (await import('express')).default;
  const { once } = await import('node:events');
  remote('update-ref', 'refs/tags/unrelated', unrelated);
  remote('update-server-info');
  const before = remote('show-ref');
  const app = express(); app.use(express.static(bare));
  const server = app.listen(0, '127.0.0.1');
  try {
    await once(server, 'listening');
    const address = server.address(); if (!address || typeof address === 'string') throw new Error('No address');
    const config = { ...cfg(), remoteUrl: `http://127.0.0.1:${address.port}` };
    await withGitOperation({}, async () => {
      await preparePackVersionRepo(repo, config, 'flow');
      await expect(fetchPackVersionRefs(repo, config, 'flow')).rejects.toMatchObject({ code: 'GIT_COMMAND_FAILED' });
    });
    expect(runGit(repo, ['for-each-ref', '--format=%(refname)'])).toBe('');
    expect(() => runGit(repo, ['cat-file', '-e', unrelated])).toThrow();
    expect(remote('show-ref')).toBe(before);
    const fetches = hooks.calls.filter(args => args[0] === 'fetch');
    expect(fetches).toHaveLength(1);
    expect(fetches[0]).toContain('--depth=1'); expect(fetches[0]).toContain('--no-tags');
    expect(fetches[0].filter(arg => arg.startsWith('+refs/heads/'))).toEqual([
      '+refs/heads/wf/flow:refs/remotes/origin/wf/flow',
    ]);
  } finally { await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve())); }
});
it.each(['GIT_TIMEOUT', 'GIT_OPERATION_TIMEOUT', 'GIT_OUTPUT_LIMIT', 'GIT_QUEUE_FULL'])('does not retry fetch after %s', async code => {
  hooks.failFetchCode = code;
  await expect(initRead()).rejects.toMatchObject({ code });
  expect(hooks.calls.filter(args => args[0] === 'fetch')).toHaveLength(1);
  expect(hooks.calls.filter(args => args[0] === 'ls-remote')).toHaveLength(1);
});

it('does not retry a command failure or log its error text', async () => {
  hooks.failFetchCode = 'GIT_COMMAND_FAILED';
  const log = vi.spyOn(console, 'info').mockImplementation(() => {});
  try {
    await expect(initRead()).rejects.toMatchObject({ code: 'GIT_COMMAND_FAILED' });
    const fetches = hooks.calls.filter(args => args[0] === 'fetch');
    expect(fetches).toHaveLength(1);
    expect(fetches[0]).toContain('--depth=1');
    expect(JSON.stringify(log.mock.calls)).not.toContain('synthetic secret stderr');
  } finally { log.mockRestore(); }
});
