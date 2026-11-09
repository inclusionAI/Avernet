// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, readdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import * as gitExecution from '@avernet/clawweb-shared/server/services/git-execution';
import { writePackScripts } from '../pack-script-write.js';

let root: string, repo: string, remote: string, base: string;
const git = (cwd: string, ...args: string[]) => {
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('GIT_')));
  Object.assign(env, { GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null', GIT_TERMINAL_PROMPT: '0' });
  return execFileSync('git', args, { cwd, env, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
};
const author = { name: 'Tester', email: 'tester@example.invalid' };
const input = () => ({ repoDir: repo, workflowId: 'flow', packId: 'pack', baseCommit: base, targetBranch: 'wf/flow', basePackPath: 'packs/pack', author });
beforeEach(() => {
  root = mkdtempSync(path.join(tmpdir(), 'pack-write-test-')); remote = path.join(root, 'remote.git'); repo = path.join(root, 'repo');
  git(root, 'init', '--bare', remote); git(root, 'clone', remote, repo);
  git(repo, 'config', 'user.name', author.name); git(repo, 'config', 'user.email', author.email);
  mkdirSync(path.join(repo, 'packs/pack/scripts'), { recursive: true });
  writeFileSync(path.join(repo, 'packs/pack/workflow.pack.yaml'), 'id: pack\nworkflows: [flow]\n');
  writeFileSync(path.join(repo, 'packs/pack/extra.txt'), 'keep');
  mkdirSync(path.join(repo, 'packs/pack/workflows'));
  writeFileSync(path.join(repo, 'packs/pack/workflows/flow.yaml'), 'id: flow\n');
  writeFileSync(path.join(repo, 'packs/pack/scripts/edit.sh'), 'old\n', { mode: 0o755 });
  writeFileSync(path.join(repo, 'packs/pack/scripts/delete.sh'), 'delete');
  git(repo, 'add', '.'); git(repo, 'commit', '-m', 'base'); base = git(repo, 'rev-parse', 'HEAD');
  git(repo, 'push', 'origin', `HEAD:refs/heads/wf/flow`);
});
afterEach(() => { vi.unstubAllEnvs(); rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); });
it('changes only requested scripts, preserving files and executable mode', async () => {
  const { commit } = await writePackScripts({ ...input(), changes: [
    { path: 'scripts/edit.sh', content: '你好\n' }, { path: 'scripts/new.py', content: 'print(1)\n' }, { path: 'scripts/delete.sh', content: null },
  ] });
  expect(commit).toMatch(/^[a-f0-9]{40}$/); expect(git(remote, 'rev-parse', 'refs/heads/wf/flow')).toBe(commit);
  expect(git(repo, 'show', `${commit}:packs/pack/scripts/edit.sh`)).toBe('你好');
  expect(git(repo, 'show', `${commit}:packs/pack/workflow.pack.yaml`)).toBe('id: pack\nworkflows: [flow]');
  expect(git(repo, 'show', `${commit}:packs/pack/extra.txt`)).toBe('keep');
  expect(git(repo, 'show', `${commit}:packs/pack/workflows/flow.yaml`)).toBe('id: flow');
  expect(git(repo, 'ls-tree', commit, 'packs/pack/scripts/edit.sh')).toMatch(/^100755 /);
  expect(git(repo, 'ls-tree', commit, 'packs/pack/scripts/delete.sh')).toBe('');
});
it.each(['scripts/../escape', '../scripts/x', 'scripts//x', 'scripts/x\\y', 'scripts/.git/config'])('rejects unsafe path %s', async (p) => {
  await expect(writePackScripts({ ...input(), changes: [{ path: p, content: 'x' }] })).rejects.toThrow();
});
it('rejects missing deletion, duplicates, NUL and invalid branch', async () => {
  for (const changes of [[{ path: 'scripts/missing.py', content: null }], [{ path: 'scripts/x.py', content: 'x' }, { path: 'scripts/x.py', content: 'y' }], [{ path: 'scripts/x.py', content: 'a\0b' }]]) {
    await expect(writePackScripts({ ...input(), changes })).rejects.toThrow();
  }
  await expect(writePackScripts({ ...input(), targetBranch: 'wf/other', changes: [{ path: 'scripts/x.py', content: 'x' }] })).rejects.toThrow();
});
it('rejects a target head that moved away from base', async () => {
  writeFileSync(path.join(repo, 'other'), 'change'); git(repo, 'add', 'other'); git(repo, 'commit', '-m', 'other'); git(repo, 'push', 'origin', 'HEAD:refs/heads/wf/flow');
  await expect(writePackScripts({ ...input(), changes: [{ path: 'scripts/x.py', content: 'x' }] })).rejects.toThrow(/conflict/i);
});
it('rejects symlinks and binary existing scripts', async () => {
  const { symlinkSync } = await import('node:fs');
  symlinkSync('/tmp', path.join(repo, 'packs/pack/scripts/link'));
  git(repo, 'add', '.'); git(repo, 'commit', '-m', 'symlink'); base = git(repo, 'rev-parse', 'HEAD'); git(repo, 'push', 'origin', 'HEAD:refs/heads/wf/flow');
  await expect(writePackScripts({ ...input(), changes: [{ path: 'scripts/link/file.py', content: 'x' }] })).rejects.toThrow(/symlink/i);
  (await import('node:fs')).unlinkSync(path.join(repo, 'packs/pack/scripts/link'));
  writeFileSync(path.join(repo, 'packs/pack/scripts/edit.sh'), Buffer.from([0xff, 0]));
  git(repo, 'add', '-A'); git(repo, 'commit', '-m', 'binary'); base = git(repo, 'rev-parse', 'HEAD'); git(repo, 'push', 'origin', 'HEAD:refs/heads/wf/flow');
  await expect(writePackScripts({ ...input(), changes: [{ path: 'scripts/edit.sh', content: 'x' }] })).rejects.toThrow(/binary/i);
});
it('rejects a push endpoint different from the observed endpoint', async () => {
  git(repo, 'remote', 'set-url', '--push', 'origin', path.join(root, 'other.git'));
  await expect(writePackScripts({ ...input(), changes: [{ path: 'scripts/x.py', content: 'x' }] })).rejects.toThrow(/endpoints differ/i);
});

it('rejects a concurrent remote update after observing the head', async () => {
  writeFileSync(path.join(repo, 'other'), 'concurrent');
  git(repo, 'add', 'other'); git(repo, 'commit', '-m', 'concurrent');
  const concurrent = git(repo, 'rev-parse', 'HEAD');
  const original = gitExecution.executeGit;
  const spy = vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
    if (args.includes('push')) git(repo, 'push', 'origin', `${concurrent}:refs/heads/wf/flow`);
    return original(args, options);
  });
  try {
    await expect(writePackScripts({ ...input(), changes: [{ path: 'scripts/x.py', content: 'x' }] })).rejects.toThrow();
    expect(git(remote, 'rev-parse', 'refs/heads/wf/flow')).toBe(concurrent);
  } finally { spy.mockRestore(); }
});

it('rejects a gitlink within the pack', async () => {
  git(repo, 'update-index', '--add', '--cacheinfo', `160000,${base},packs/pack/scripts/submodule`);
  git(repo, 'commit', '-m', 'gitlink'); base = git(repo, 'rev-parse', 'HEAD'); git(repo, 'push', 'origin', 'HEAD:refs/heads/wf/flow');
  await expect(writePackScripts({ ...input(), changes: [{ path: 'scripts/x.py', content: 'x' }] })).rejects.toThrow(/gitlink/i);
});
it('rejects oversized and invalid input', async () => {
  await expect(writePackScripts({ ...input(), changes: [{ path: 'scripts/x.py', content: 'x'.repeat(1024 * 1024 + 1) }] })).rejects.toThrow();
  await expect(writePackScripts({ ...input(), changes: Array.from({ length: 101 }, (_, i) => ({ path: `scripts/${i}`, content: 'x' })) })).rejects.toThrow();
  await expect(writePackScripts({ ...input(), basePackPath: '../pack', changes: [{ path: 'scripts/x.py', content: 'x' }] })).rejects.toThrow();
  await expect(writePackScripts({ ...input(), baseCommit: base.slice(0, 10), changes: [{ path: 'scripts/x.py', content: 'x' }] })).rejects.toThrow();
});

const commitBase = () => { git(repo, 'add', '-A'); git(repo, 'commit', '-m', 'change baseline'); base = git(repo, 'rev-parse', 'HEAD'); git(repo, 'push', 'origin', 'HEAD:refs/heads/wf/flow'); };
it('rejects a missing target branch rather than creating one', async () => {
  await expect(writePackScripts({ ...input(), targetBranch: 'saved/flow', changes: [{ path: 'scripts/new.py', content: 'x' }] })).rejects.toThrow();
  expect(git(remote, 'ls-tree', base)).not.toBe('');
  expect(git(repo, 'ls-remote', '--heads', 'origin', 'refs/heads/saved/flow')).toBe('');
});
it('rejects invalid pack and workflow bindings and duplicate layouts', async () => {
  writeFileSync(path.join(repo, 'packs/pack/workflow.pack.yaml'), 'id: other\nworkflows: [flow]\n'); commitBase();
  await expect(writePackScripts({ ...input(), changes: [{ path: 'scripts/new.py', content: 'x' }] })).rejects.toThrow();
  writeFileSync(path.join(repo, 'packs/pack/workflow.pack.yaml'), 'id: pack\nworkflows: [flow]\n');
  writeFileSync(path.join(repo, 'packs/pack/workflows/flow.yaml'), 'id: wrong\n'); commitBase();
  await expect(writePackScripts({ ...input(), changes: [{ path: 'scripts/new.py', content: 'x' }] })).rejects.toThrow();
  writeFileSync(path.join(repo, 'packs/pack/workflows/flow.yaml'), 'id: flow\n');
  mkdirSync(path.join(repo, 'pack')); writeFileSync(path.join(repo, 'pack/workflow.pack.yaml'), 'id: pack\nworkflows: [flow]\n'); commitBase();
  await expect(writePackScripts({ ...input(), changes: [{ path: 'scripts/new.py', content: 'x' }] })).rejects.toThrow();
});
it('rejects unsupported extension and final aggregate size and count', async () => {
  await expect(writePackScripts({ ...input(), changes: [{ path: 'scripts/new.txt', content: 'x' }] })).rejects.toThrow();
  for (let i = 0; i < 8; i++) writeFileSync(path.join(repo, `packs/pack/scripts/large${i}.py`), Buffer.alloc(1024 * 1024 - (i === 7 ? 10 : 0), 65));
  commitBase();
  await expect(writePackScripts({ ...input(), changes: [{ path: 'scripts/new.py', content: 'x' }] })).rejects.toThrow();
  for (let i = 0; i < 8; i++) rmSync(path.join(repo, `packs/pack/scripts/large${i}.py`));
  for (let i = 0; i < 498; i++) writeFileSync(path.join(repo, `packs/pack/scripts/n${i}.py`), '');
  commitBase();
  await expect(writePackScripts({ ...input(), changes: [{ path: 'scripts/new.py', content: 'x' }] })).rejects.toThrow();
});
it('does not push unrelated annotated tags when push.followTags is enabled', async () => {
  git(repo, 'tag', '-a', 'existing', '-m', 'existing', base);
  git(repo, 'push', 'origin', 'refs/tags/existing:refs/tags/existing');
  const existingTag = git(remote, 'rev-parse', 'refs/tags/existing');
  git(repo, 'tag', '-a', 'unrelated', '-m', 'tag', base);
  git(repo, 'config', 'push.followTags', 'true');
  expect(git(remote, 'for-each-ref', '--format=%(refname)', 'refs/tags')).toBe('refs/tags/existing');

  const { commit } = await writePackScripts({ ...input(), changes: [{ path: 'scripts/new.py', content: 'x' }] });
  expect(git(remote, 'rev-parse', 'refs/heads/wf/flow')).toBe(commit);
  expect(git(remote, 'rev-parse', 'refs/tags/existing')).toBe(existingTag);
  expect(git(remote, 'for-each-ref', '--format=%(refname)', 'refs/tags')).toBe('refs/tags/existing');

  // Positive control: the same local config and reachable tag really do cause
  // an ordinary push to publish the tag, even when the branch is up to date.
  git(repo, 'push', 'origin', `${commit}:refs/heads/wf/flow`);
  expect(git(remote, 'rev-parse', 'refs/tags/unrelated')).toBe(git(repo, 'rev-parse', 'refs/tags/unrelated'));
});
it('ignores inherited Git config injection during a script write', async () => {
  git(repo, 'tag', '-a', 'unrelated', '-m', 'tag', base);
  vi.stubEnv('GIT_CONFIG_COUNT', '1');
  vi.stubEnv('GIT_CONFIG_KEY_0', 'push.followTags');
  vi.stubEnv('GIT_CONFIG_VALUE_0', 'true');
  try {
    const { commit } = await writePackScripts({ ...input(), changes: [{ path: 'scripts/new.py', content: 'x' }] });
    expect(git(remote, 'rev-parse', 'refs/heads/wf/flow')).toBe(commit);
    expect(git(remote, 'for-each-ref', '--format=%(refname)', 'refs/tags')).toBe('');
  } finally { vi.unstubAllEnvs(); }
});
it('validates custom workflow paths and selected pack layout', async () => {
  writeFileSync(path.join(repo, 'packs/pack/workflow.pack.yaml'), 'id: pack\nworkflows:\n  - id: flow\n    file: workflows/flow.yaml\n');
  commitBase();
  await expect(writePackScripts({ ...input(), basePackPath: 'pack', changes: [{ path: 'scripts/new.py', content: 'x' }] })).rejects.toThrow();
  const result = await writePackScripts({ ...input(), changes: [{ path: 'scripts/new.py', content: 'x' }] });
  expect(git(repo, 'show', `${result.commit}:packs/pack/scripts/new.py`)).toBe('x');
});
it('rejects missing manifest and unsafe or unbound workflow paths', async () => {
  rmSync(path.join(repo, 'packs/pack/workflow.pack.yaml')); commitBase();
  await expect(writePackScripts({ ...input(), changes: [{ path: 'scripts/new.py', content: 'x' }] })).rejects.toThrow();
  for (const manifest of [
    'id: pack\nworkflows: [other]\n',
    'id: pack\nworkflows: [flow, flow]\n',
    'id: pack\nworkflows:\n  - id: flow\n    file: ../other.yaml\n',
  ]) {
    writeFileSync(path.join(repo, 'packs/pack/workflow.pack.yaml'), manifest); commitBase();
    await expect(writePackScripts({ ...input(), changes: [{ path: 'scripts/new.py', content: 'x' }] })).rejects.toThrow();
  }
});

describe('configured temporary parent for pack script writes', () => {
  const configuredParent = () => {
    const parent = path.join(root, 'configured-temp'); mkdirSync(parent);
    return parent;
  };

  it('stages writes under CLAWWEB_TEMP_DIR', async () => {
    const parent = configuredParent(); vi.stubEnv('CLAWWEB_TEMP_DIR', parent);
    const spy = vi.spyOn(gitExecution, 'executeGit');
    const { commit } = await writePackScripts({ ...input(), changes: [{ path: 'scripts/new.py', content: 'x' }] });
    const indexFile = spy.mock.calls.map(([, options]) => options?.env?.GIT_INDEX_FILE).find(Boolean);
    expect(String(indexFile).startsWith(path.join(parent, 'pack-write-'))).toBe(true);
    expect(git(remote, 'rev-parse', 'refs/heads/wf/flow')).toBe(commit);
    expect(readdirSync(parent)).toEqual([]);
  });

  it('fails without staging or writing when the configured parent is missing', async () => {
    const parent = configuredParent();
    vi.stubEnv('CLAWWEB_TEMP_DIR', path.join(parent, 'missing'));
    const before = git(remote, 'rev-parse', 'refs/heads/wf/flow');
    await expect(writePackScripts({ ...input(), changes: [{ path: 'scripts/new.py', content: 'x' }] }))
      .rejects.toThrow('临时目录不可用或配置无效');
    expect(git(remote, 'rev-parse', 'refs/heads/wf/flow')).toBe(before);
    expect(readdirSync(parent)).toEqual([]);
  });
});

it('ignores an independent saved ref appearing during the wf push', async () => {
  const execute = gitExecution.executeGit;
  const spy = vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
    const result = await execute(args, options);
    if (args.includes('push')) git(repo, 'push', 'origin', `${base}:refs/heads/saved/flow`);
    return result;
  });
  try {
    await expect(writePackScripts({ ...input(), changes: [{ path: 'scripts/edit.sh', content: 'changed' }] })).resolves.toMatchObject({ commit: expect.any(String) });
    expect(git(remote, 'rev-parse', 'refs/heads/saved/flow')).toBe(base);
    // Never undo a possibly successful wf push to disguise a cross-ref race.
    expect(git(remote, 'rev-parse', 'refs/heads/wf/flow')).not.toBe(base);
  } finally { spy.mockRestore(); }
});
