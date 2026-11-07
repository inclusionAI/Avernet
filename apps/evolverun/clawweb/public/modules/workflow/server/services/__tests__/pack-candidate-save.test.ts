// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync, symlinkSync, statSync, chmodSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { createLocalRemote, runGit } from './clawflows-git-client-fixture.js';
import { PackCandidateConflictError, PackCandidateOutcomeUnknownError, savePackCandidate, withPreparedPackCandidate, pushPreparedPackCandidate, resumePreparedPackCandidate, type SavePackCandidateInput } from '../pack-candidate-save.js';
import { cloneClawflows, createTempRepo, removeTempRepo } from '../clawflows-git-client.js';
import { readPackScriptSnapshot } from '../pack-script-snapshot.js';
import { restorePreparedGitBundle } from '../pack-git-prepared.js';
const injection = vi.hoisted(() => ({ push: undefined as undefined | ((args: string[], run: () => Promise<Buffer>) => Promise<Buffer>), afterPush: false, readsAfterPush: 0, failReads: false }));
vi.mock('@avernet/clawweb-shared/server/services/git-execution', async importOriginal => {
  const actual = await importOriginal<typeof import('@avernet/clawweb-shared/server/services/git-execution')>();
  return { ...actual, executeGit: async (...params: Parameters<typeof actual.executeGit>) => {
    const [args] = params;
    if (args[0] === 'ls-remote' && injection.afterPush) { injection.readsAfterPush++; if (injection.failReads) throw new Error('unavailable'); }
    if (args[0] === 'push' && injection.push) return injection.push(args, () => actual.executeGit(...params));
    return actual.executeGit(...params);
  } };
});
vi.setConfig({ testTimeout: 30_000, hookTimeout: 30_000 });
let root: string, bare: string, seed: string, current: string;
const b64 = (s: string) => Buffer.from(s).toString('base64');
function seedPack(prefix = ''): string {
  const pack = path.join(seed, prefix);
  mkdirSync(path.join(pack, 'workflows'), { recursive: true });
  mkdirSync(path.join(pack, 'scripts'), { recursive: true });
  writeFileSync(path.join(pack, 'workflow.pack.yaml'), 'id: pack\nversion: 1\nworkflows:\n  - id: flow\n    file: workflows/flow.yaml\n');
  writeFileSync(path.join(pack, 'workflows/flow.yaml'), 'id: flow\n');
  writeFileSync(path.join(pack, 'scripts/shared.txt'), 'shared legacy\n');
  writeFileSync(path.join(seed, 'outside.txt'), 'outside\n');
  return publish();
}
function seedSharedPack(): string {
  seedPack('packs/pack');
  const dir = path.join(seed, 'packs/pack');
  writeFileSync(path.join(dir, 'workflows/other.yaml'), 'id: other\n');
  writeFileSync(path.join(dir, 'workflow.pack.yaml'), 'id: pack\nworkflows: [flow, other]\n');
  return publish();
}
function publish(): string {
  runGit(seed, ['add', '.']); runGit(seed, ['commit', '-m', 'pack']);
  const commit = runGit(seed, ['rev-parse', 'HEAD']);
  runGit(seed, ['push', bare, `${commit}:refs/heads/wf/flow`]);
  return commit;
}
function input(extra: Partial<SavePackCandidateInput> = {}): SavePackCandidateInput {
  return { workflowId: 'flow', sourceVersion: 'current', sourceCommit: current, expectedSavedHead: current,
    config: { remoteUrl: bare, username: 'test', email: 'test@example.invalid' }, changes: [], ...extra };
}
function show(commit: string, file: string): string { return runGit(root, ['--git-dir', bare, 'show', `${commit}:${file}`]); }
// The mutable saved snapshot now lives on wf; legacy saved assertions below stay explicit.
function head(): string { return runGit(root, ['--git-dir', bare, 'rev-parse', 'refs/heads/wf/flow']); }
beforeEach(() => { root = mkdtempSync(path.join(tmpdir(), 'candidate-test-')); bare = createLocalRemote(root); seed = path.join(root, 'seed'); });
afterEach(() => { injection.push = undefined; injection.afterPush = false; injection.readsAfterPush = 0; injection.failReads = false; rmSync(root, { recursive: true, force: true }); });
describe('savePackCandidate', () => {
  it.each(['', 'packs/pack'])('saves a full snapshot in layout %s including binary and shared files', async prefix => {
    current = seedPack(prefix);
    const result = await savePackCandidate(input({ changes: [
      { path: 'scripts/shared.txt', operation: 'replace', contentBase64: b64('changed shared'), mode: '100755' },
      { path: 'asset.bin', operation: 'create', contentBase64: Buffer.from([0, 255, 1]).toString('base64'), mode: '100644' },
      { path: 'workflow.pack.yaml', operation: 'replace', contentBase64: b64('id: pack\nversion: 2\nworkflows: [flow]\n'), mode: '100644' },
    ] }));
    expect(result.branch).toBe('wf/flow'); expect(head()).toBe(result.commit);
    const base = prefix ? `${prefix}/` : '';
    expect(show(result.commit, `${base}scripts/shared.txt`)).toBe('changed shared');
    expect(show(result.commit, 'outside.txt')).toBe('outside');
    expect(show(result.commit, `${base}workflows/flow.yaml`)).toBe('id: flow');
    expect(execFileSync('git', ['--git-dir', bare, 'show', `${result.commit}:${base}asset.bin`])).toEqual(Buffer.from([0, 255, 1]));
    expect(runGit(root, ['--git-dir', bare, 'ls-tree', result.commit, `${base}scripts/shared.txt`])).toMatch(/^100755 blob/);
    expect(runGit(root, ['ls-remote', bare, 'refs/heads/saved/flow'])).toBe('');
  });
  it('edits draft and deletes a file without affecting other files', async () => {
    current = seedPack('packs/pack');
    const first = await savePackCandidate(input({ changes: [{ path: 'new.txt', operation: 'create', contentBase64: b64('draft'), mode: '100644' }] }));
    const second = await savePackCandidate(input({ sourceVersion: 'draft', sourceCommit: first.commit, expectedSavedHead: first.commit,
      changes: [{ path: 'scripts/shared.txt', operation: 'delete' }] }));
    expect(show(second.commit, 'packs/pack/new.txt')).toBe('draft');
    expect(show(second.commit, 'outside.txt')).toBe('outside');
    expect(runGit(root, ['--git-dir', bare, 'ls-tree', '-r', '--name-only', second.commit])).not.toContain('scripts/shared.txt');
  });
  it('rejects current over a different saved commit even with its exact expected head', async () => {
    current = seedPack();
    const draft = await savePackCandidate(input({ changes: [
      { path: 'draft.txt', operation: 'create', contentBase64: b64('draft work'), mode: '100644' },
    ] }));
    await expect(savePackCandidate(input())).rejects.toBeInstanceOf(PackCandidateConflictError);
    expect(head()).toBe(draft.commit);
    const attempted = input({ expectedSavedHead: draft.commit, changes: [
      { path: 'current-edit.txt', operation: 'create', contentBase64: b64('current work'), mode: '100644' },
    ] });
    await expect(savePackCandidate(attempted)).rejects.toBeInstanceOf(PackCandidateConflictError);
    await expect(withPreparedPackCandidate(attempted, async (_prepared, push) => push()))
      .rejects.toThrow(/saved/i);
    expect(head()).toBe(draft.commit);
    expect(show(head(), 'draft.txt')).toBe('draft work');
    expect(runGit(root, ['--git-dir', bare, 'ls-tree', '--name-only', head()])).not.toContain('current-edit.txt');
  });
  it('ignores a distinct saved ref, even when it has the same tree as wf', async () => {
    current = seedPack();
    const tree = runGit(seed, ['rev-parse', `${current}^{tree}`]);
    const saved = runGit(seed, ['commit-tree', tree, '-p', current, '-m', 'distinct saved']);
    runGit(seed, ['push', bare, `${saved}:refs/heads/saved/flow`]);
    const result = await savePackCandidate(input({ expectedSavedHead: current }));
    expect(head()).toBe(result.commit);
    expect(runGit(root, ['--git-dir', bare, 'rev-parse', 'refs/heads/saved/flow'])).toBe(saved);
    expect(show(saved, 'scripts/shared.txt')).toBe('shared legacy');
  });
  it.each(['push', 'resume'])('rejects a legacy current candidate through %s without replacing saved', async entry => {
    current = seedPack();
    const prepared = await withPreparedPackCandidate(input({ changes: [
      { path: 'current-edit.txt', operation: 'create', contentBase64: b64('current work'), mode: '100644' },
    ] }), async candidate => candidate);
    const draft = await savePackCandidate(input({ changes: [
      { path: 'draft.txt', operation: 'create', contentBase64: b64('draft work'), mode: '100644' },
    ] }));
    // Simulate an older, valid journal that explicitly confirmed the saved head.
    const legacy = { ...prepared, expectedSavedHead: draft.commit };
    if (entry === 'resume') {
      await expect(resumePreparedPackCandidate(legacy, input().config)).rejects.toBeInstanceOf(PackCandidateConflictError);
    } else {
      const dir = await createTempRepo();
      try {
        await cloneClawflows(dir, input().config);
        await restorePreparedGitBundle(dir, legacy.bundleBase64);
        await expect(pushPreparedPackCandidate(dir, legacy)).rejects.toBeInstanceOf(PackCandidateConflictError);
      } finally { await removeTempRepo(dir); }
    }
    expect(head()).toBe(draft.commit);
    expect(show(head(), 'draft.txt')).toBe('draft work');
    expect(runGit(root, ['--git-dir', bare, 'ls-tree', '--name-only', head()])).not.toContain('current-edit.txt');
  });
  it('refuses to replay a retired saved journal, leaving both refs intact', async () => {
    current = seedPack();
    const prepared = await withPreparedPackCandidate(input(), async candidate => candidate);
    const draft = await savePackCandidate(input({ changes: [
      { path: 'draft.txt', operation: 'create', contentBase64: b64('draft work'), mode: '100644' },
    ] }));
    const legacy = { ...prepared, branch: 'saved/flow', expectedSavedHead: draft.commit };
    const dir = await createTempRepo();
    try {
      await cloneClawflows(dir, input().config);
      await restorePreparedGitBundle(dir, legacy.bundleBase64);
      runGit(dir, ['push', '--force', 'origin', `${legacy.commit}:refs/heads/saved/flow`]);
    } finally { await removeTempRepo(dir); }
    await expect(resumePreparedPackCandidate(legacy, input().config)).rejects.toThrow(/Invalid prepared candidate/);
    expect(head()).toBe(draft.commit);
    expect(runGit(root, ['--git-dir', bare, 'rev-parse', 'refs/heads/saved/flow'])).toBe(legacy.commit);
  });
  it('allows current save when saved exactly equals the source commit', async () => {
    current = seedPack();
    runGit(seed, ['push', bare, `${current}:refs/heads/saved/flow`]);
    const result = await savePackCandidate(input({ expectedSavedHead: current, changes: [
      { path: 'edit.txt', operation: 'create', contentBase64: b64('edit'), mode: '100644' },
    ] }));
    expect(head()).toBe(result.commit);
    expect(show(result.commit, 'edit.txt')).toBe('edit');
    await expect(savePackCandidate(input({ sourceVersion: 'draft', sourceCommit: current, expectedSavedHead: result.commit }))).rejects.toBeInstanceOf(PackCandidateConflictError);
  });
  it('enforces create and replace against the immutable selected snapshot', async () => {
    current = seedPack();
    await expect(savePackCandidate(input({ changes: [
      { path: 'scripts/shared.txt', operation: 'create', contentBase64: b64('clobber'), mode: '100644' },
    ] }))).rejects.toThrow('Create target already exists');
    await expect(savePackCandidate(input({ changes: [
      { path: 'absent.txt', operation: 'replace', contentBase64: b64('unexpected'), mode: '100644' },
    ] }))).rejects.toThrow('Replace target does not exist');
    expect(runGit(root, ['ls-remote', bare, 'refs/heads/saved/flow'])).toBe('');
    expect(show(current, 'scripts/shared.txt')).toBe('shared legacy');
  });
  it('rejects the former ambiguous upsert operation at runtime', async () => {
    current = seedPack();
    const oldChange = { path: 'scripts/shared.txt', operation: 'upsert', contentBase64: b64('unsafe'), mode: '100644' };
    await expect(savePackCandidate(input({ changes: [oldChange as unknown as SavePackCandidateInput['changes'][number]] }))).rejects.toThrow('Invalid candidate content or mode');
    expect(runGit(root, ['ls-remote', bare, 'refs/heads/saved/flow'])).toBe('');
  });
  it.each(['../outside', '/etc/passwd', '.git/config', 'scripts/../../escape', 'scripts\\x', 'C:/evil', 'C:evil', 'scripts/D:evil'])('rejects unsafe path %s', async file => {
    current = seedPack();
    await expect(savePackCandidate(input({ changes: [{ path: file, operation: 'create', contentBase64: '', mode: '100644' }] }))).rejects.toThrow();
  });
  it('rejects invalid manifest identity and malformed base64 without creating saved', async () => {
    current = seedPack();
    await expect(savePackCandidate(input({ changes: [{ path: 'workflow.pack.yaml', operation: 'replace', contentBase64: b64('id: bad/id\nworkflows: [flow]\n'), mode: '100644' }] }))).rejects.toThrow();
    await expect(savePackCandidate(input({ changes: [{ path: 'x', operation: 'create', contentBase64: '???', mode: '100644' }] }))).rejects.toThrow();
    expect(runGit(root, ['ls-remote', bare, 'refs/heads/saved/flow'])).toBe('');
  });
  it('rejects a symlink within the pack', async () => {
    seedPack('packs/pack');
    symlinkSync('shared.txt', path.join(seed, 'packs/pack/scripts/link'));
    current = publish();
    await expect(savePackCandidate(input())).rejects.toThrow('Unsafe Pack entry');
  });
  it('uses the remote lease to prevent two concurrent first saves from both succeeding', async () => {
    current = seedPack();
    // Ensure both candidates pass preflight before either push tests the remote lease.
    let arrived = 0; let release!: () => void;
    const ready = new Promise<void>(resolve => { release = resolve; });
    injection.push = async (_args, run) => { if (++arrived === 2) release(); await ready; return run(); };
    const make = (value: string) => savePackCandidate(input({ changes: [
      { path: 'new.txt', operation: 'create', contentBase64: b64(value), mode: '100644' },
    ] }));
    const results = await Promise.allSettled([make('one'), make('two')]);
    expect(results.filter(result => result.status === 'fulfilled')).toHaveLength(1);
    const rejected = results.find(result => result.status === 'rejected');
    expect(arrived).toBe(2);
    expect(rejected && rejected.status === 'rejected' && rejected.reason).toBeInstanceOf(PackCandidateOutcomeUnknownError);
    if (rejected?.status === 'rejected' && rejected.reason instanceof PackCandidateOutcomeUnknownError) {
      expect(rejected.reason.branch).toBe('wf/flow');
      expect(rejected.reason.commit).not.toBe(head());
    }
    expect(['one', 'two']).toContain(show(head(), 'new.txt'));
  });
  it.each(['moved after success', 'remote query unavailable', 'authentication or rejection'])(
    'keeps push outcome unknown for %s without querying or retrying', async scenario => {
      current = seedPack();
      injection.failReads = scenario === 'remote query unavailable';
      let calls = 0;
      injection.push = async (_args, run) => {
        calls++;
        if (scenario !== 'authentication or rejection') {
          await run();
          if (scenario === 'moved after success') {
            runGit(root, ['--git-dir', bare, 'update-ref', 'refs/heads/wf/flow', current]);
          }
        }
        injection.afterPush = true;
        // Even a hypothetical follow-up ls-remote failure must not mask this outcome.
        throw new Error('secret remote URL or authentication error');
      };
      let error: unknown;
      try { await savePackCandidate(input({ changes: [{ path: 'new.txt', operation: 'create', contentBase64: b64('candidate'), mode: '100644' }] })); }
      catch (e) { error = e; }
      expect(error).toBeInstanceOf(PackCandidateOutcomeUnknownError);
      const unknown = error as PackCandidateOutcomeUnknownError;
      expect(unknown.branch).toBe('wf/flow'); expect(unknown.commit).toMatch(/^[a-f0-9]{40,64}$/);
      expect(unknown.message).not.toContain('secret');
      expect(calls).toBe(1); expect(injection.readsAfterPush).toBe(0);
      if (scenario === 'moved after success') { expect(head()).toBe(current); expect(show(unknown.commit, 'new.txt')).toBe('candidate'); }
      if (scenario === 'remote query unavailable') expect(head()).toBe(unknown.commit);
      if (scenario === 'authentication or rejection') expect(runGit(root, ['ls-remote', bare, 'refs/heads/saved/flow'])).toBe('');
    },
  );
  it('reports a real server-side push rejection as unknown without leaking hook output', async () => {
    current = seedPack();
    writeFileSync(path.join(bare, 'hooks', 'pre-receive'), '#!/bin/sh\necho secret-hook-output >&2\nexit 1\n', { mode: 0o755 });
    let error: unknown;
    try { await savePackCandidate(input()); } catch (e) { error = e; }
    expect(error).toBeInstanceOf(PackCandidateOutcomeUnknownError);
    const unknown = error as PackCandidateOutcomeUnknownError;
    expect(unknown.branch).toBe('wf/flow'); expect(unknown.commit).toMatch(/^[a-f0-9]{40,64}$/);
    expect(unknown.message).not.toContain('secret');
    expect(runGit(root, ['ls-remote', bare, 'refs/heads/saved/flow'])).toBe('');
  });
  it('rejects a gitlink within the pack', async () => {
    const commit = seedPack('packs/pack');
    runGit(seed, ['update-index', '--add', '--cacheinfo', '160000', commit, 'packs/pack/dependency']);
    runGit(seed, ['commit', '-m', 'gitlink']);
    current = runGit(seed, ['rev-parse', 'HEAD']);
    runGit(seed, ['push', bare, `${current}:refs/heads/wf/flow`]);
    await expect(savePackCandidate(input())).rejects.toThrow('Unsafe Pack entry');
  });

  it('wf CAS rejects even a server-side advance after the client preflight', async () => {
    current = seedPack();
    writeFileSync(path.join(seed, 'new-current.txt'), 'new current');
    runGit(seed, ['add', '.']);
    runGit(seed, ['commit', '-m', 'new current']);
    const next = runGit(seed, ['rev-parse', 'HEAD']);
    runGit(seed, ['push', bare, `${next}:refs/heads/next`]);
    const hook = path.join(bare, 'hooks', 'pre-receive');
    writeFileSync(hook, `#!/bin/sh\nunset GIT_QUARANTINE_PATH GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES\ngit update-ref refs/heads/wf/flow ${next}\n`, { mode: 0o755 });
    await expect(savePackCandidate(input({ changes: [
      { path: 'saved.txt', operation: 'create', contentBase64: b64('candidate'), mode: '100644' },
    ] }))).rejects.toBeInstanceOf(PackCandidateOutcomeUnknownError);
    expect(runGit(root, ['--git-dir', bare, 'rev-parse', 'refs/heads/wf/flow'])).toBe(next);
    expect(head()).toBe(next);
    expect(runGit(root, ['ls-remote', bare, 'refs/heads/saved/flow'])).toBe('');
  });

  it.each([
    ['missing referenced file', 'id: pack\nworkflows: [flow, missing]\n'],
    ['duplicate workflow ID', 'id: pack\nworkflows: [flow, other, other]\n'],
    ['duplicate workflow path', 'id: pack\nworkflows:\n  - flow\n  - id: other\n    file: workflows/flow.yaml\n'],
    ['unsafe workflow path', 'id: pack\nworkflows:\n  - flow\n  - id: other\n    file: ../outside.txt\n'],
    ['drive workflow path', 'id: pack\nworkflows:\n  - flow\n  - id: other\n    file: C:/other.yaml\n'],
    ['nested drive workflow path', 'id: pack\nworkflows:\n  - flow\n  - id: other\n    file: nested/D:other.yaml\n'],
    ['invalid workflow identity', 'id: pack\nworkflows: [flow, "../other"]\n'],
  ])('rejects manifest with %s', async (_label, manifest) => {
    current = seedSharedPack();
    await expect(savePackCandidate(input({ changes: [{ path: 'workflow.pack.yaml', operation: 'replace',
      contentBase64: b64(manifest), mode: '100644' }] }))).rejects.toThrow();
    expect(runGit(root, ['ls-remote', bare, 'refs/heads/saved/flow'])).toBe('');
  });
  it.each(['delete', 'identity'])('rejects %s of another referenced workflow even without a manifest change', async kind => {
    current = seedSharedPack();
    const change: SavePackCandidateInput['changes'][number] = kind === 'delete'
      ? { path: 'workflows/other.yaml', operation: 'delete' }
      : { path: 'workflows/other.yaml', operation: 'replace', contentBase64: b64('id: wrong\n'), mode: '100644' };
    await expect(savePackCandidate(input({ changes: [change] }))).rejects.toThrow();
    expect(runGit(root, ['ls-remote', bare, 'refs/heads/saved/flow'])).toBe('');
  });
  it('allows moving a shared workflow when all final references remain valid', async () => {
    current = seedSharedPack();
    const result = await savePackCandidate(input({ changes: [
      { path: 'workflows/other.yaml', operation: 'delete' },
      { path: 'workflows/moved.yaml', operation: 'create', contentBase64: b64('id: other\n'), mode: '100644' },
      { path: 'workflow.pack.yaml', operation: 'replace', contentBase64: b64('id: pack\nworkflows:\n  - flow\n  - id: other\n    file: workflows/moved.yaml\n'), mode: '100644' },
    ] }));
    expect(show(result.commit, 'packs/pack/workflows/moved.yaml')).toBe('id: other');
    expect(show(result.commit, 'packs/pack/workflows/flow.yaml')).toBe('id: flow');
    expect(head()).toBe(result.commit);
  });

  it('allows editing ordinary packs/ resources for the selected root workflow', async () => {
    seedPack();
    const other = path.join(seed, 'packs/other');
    mkdirSync(path.join(other, 'workflows'), { recursive: true });
    writeFileSync(path.join(other, 'workflow.pack.yaml'), 'id: other\nworkflows: [other]\n');
    writeFileSync(path.join(other, 'workflows/other.yaml'), 'id: other\n');
    current = publish();
    runGit(seed, ['push', bare, `${current}:refs/heads/wf/other`]);
    const result = await savePackCandidate(input({ changes: [{ path: 'packs/other/workflows/other.yaml', operation: 'replace',
      contentBase64: b64('id: other\nmodified: true\n'), mode: '100644' }] }));
    expect(show(result.commit, 'packs/other/workflows/other.yaml')).toBe('id: other\nmodified: true');
    expect(show(result.commit, 'workflow.pack.yaml').trimEnd()).toBe('id: pack\nversion: 1\nworkflows:\n  - id: flow\n    file: workflows/flow.yaml');
    await expect(savePackCandidate(input({ workflowId: 'other' }))).rejects.toThrow('Mixed root and nested Pack layout');
    expect(runGit(bare, ['ls-tree', '-r', '--name-only', result.commit])).toContain('packs/other/workflow.pack.yaml');
  });
  it('rejects introducing a nested manifest into a root Pack', async () => {
    current = seedPack();
    await expect(savePackCandidate(input({ changes: [{ path: 'nested/workflow.pack.yaml', operation: 'create',
      contentBase64: b64('id: nested\nworkflows: []\n'), mode: '100644' }] }))).rejects.toThrow('Mixed root and nested Pack layout');
    expect(runGit(root, ['ls-remote', bare, 'refs/heads/saved/flow'])).toBe('');
  });
  it('rejects a nested Pack within the selected nested Pack', async () => {
    seedPack('packs/pack');
    const child = path.join(seed, 'packs/pack/child');
    mkdirSync(child);
    writeFileSync(path.join(child, 'workflow.pack.yaml'), 'id: child\nworkflows: []\n');
    current = publish();
    await expect(savePackCandidate(input())).rejects.toThrow('Overlapping Pack layout');
    expect(runGit(root, ['ls-remote', bare, 'refs/heads/saved/flow'])).toBe('');
  });
  it('rejects overlap between the packs directory Pack and packs/<id> in either direction', async () => {
    seedPack('packs/pack');
    const parent = path.join(seed, 'packs');
    mkdirSync(path.join(parent, 'workflows'), { recursive: true });
    writeFileSync(path.join(parent, 'workflow.pack.yaml'), 'id: packs\nworkflows: [other]\n');
    writeFileSync(path.join(parent, 'workflows/other.yaml'), 'id: other\n');
    current = publish();
    runGit(seed, ['push', bare, `${current}:refs/heads/wf/other`]);
    await expect(savePackCandidate(input())).rejects.toThrow('Overlapping Pack layout');
    await expect(savePackCandidate(input({ workflowId: 'other' }))).rejects.toThrow('Overlapping Pack layout');
    expect(runGit(root, ['ls-remote', bare, 'refs/heads/saved/*'])).toBe('');
  });
  it('rejects creating a nested Pack inside the selected Pack', async () => {
    current = seedPack('packs/pack');
    await expect(savePackCandidate(input({ changes: [{ path: 'child/workflow.pack.yaml', operation: 'create',
      contentBase64: b64('id: child\nworkflows: []\n'), mode: '100644' }] }))).rejects.toThrow('Overlapping Pack layout');
  });
  it('keeps a separate nested Pack unchanged when editing another nested Pack', async () => {
    seedPack('packs/pack');
    const other = path.join(seed, 'packs/other');
    mkdirSync(path.join(other, 'workflows'), { recursive: true });
    writeFileSync(path.join(other, 'workflow.pack.yaml'), 'id: other\nworkflows: [other]\n');
    writeFileSync(path.join(other, 'workflows/other.yaml'), 'id: other\n');
    current = publish();
    const result = await savePackCandidate(input({ changes: [
      { path: 'scripts/shared.txt', operation: 'replace', contentBase64: b64('edited'), mode: '100644' },
    ] }));
    expect(show(result.commit, 'packs/other/workflows/other.yaml')).toBe('id: other');
    expect(show(result.commit, 'packs/pack/scripts/shared.txt')).toBe('edited');
  });
  it('enforces the one MiB limit against unchanged blobs while allowing a repair', async () => {
    seedPack('packs/pack');
    writeFileSync(path.join(seed, 'packs/pack/big.bin'), Buffer.alloc(1024 * 1024 + 1, 7));
    current = publish();
    await expect(savePackCandidate(input())).rejects.toThrow('Pack size limit exceeded');
    const result = await savePackCandidate(input({ changes: [{ path: 'big.bin', operation: 'replace',
      contentBase64: Buffer.alloc(1024 * 1024, 7).toString('base64'), mode: '100644' }] }));
    expect(head()).toBe(result.commit);
    expect(runGit(root, ['--git-dir', bare, 'cat-file', '-s', `${result.commit}:packs/pack/big.bin`])).toBe('1048576');
    const clone = await createTempRepo();
    try {
      await cloneClawflows(clone, input().config);
      const snapshot = await readPackScriptSnapshot(clone, 'flow', 'pack', 'draft', { includeVersionFiles: true });
      expect(snapshot.versionFiles?.find(file => file.path === 'big.bin')?.contentBase64).toBe(Buffer.alloc(1024 * 1024, 7).toString('base64'));
    } finally { await removeTempRepo(clone); }
  }, 30000);
  it('enforces the total eight MiB boundary across unchanged and changed files', async () => {
    seedPack('packs/pack');
    const pack = path.join(seed, 'packs/pack');
    const base = ['workflow.pack.yaml', 'workflows/flow.yaml', 'scripts/shared.txt']
      .reduce((sum, file) => sum + statSync(path.join(pack, file)).size, 0);
    for (let i = 0; i < 7; i++) writeFileSync(path.join(pack, `chunk${i}`), Buffer.alloc(1024 * 1024, i));
    writeFileSync(path.join(pack, 'last'), Buffer.alloc(1024 * 1024 - base, 1));
    current = publish();
    const atLimit = await savePackCandidate(input());
    const clone = await createTempRepo();
    try {
      await cloneClawflows(clone, input().config);
      const snapshot = await readPackScriptSnapshot(clone, 'flow', 'pack', 'draft', { includeVersionFiles: true });
      expect(snapshot.versionFiles?.reduce((sum, file) => sum + Buffer.from(file.contentBase64, 'base64').length, 0)).toBe(8 * 1024 * 1024);
    } finally { await removeTempRepo(clone); }
    await expect(savePackCandidate(input({ sourceVersion: 'draft', sourceCommit: atLimit.commit, expectedSavedHead: atLimit.commit,
      changes: [{ path: 'extra', operation: 'create', contentBase64: b64('x'), mode: '100644' }] }))).rejects.toThrow('Pack size limit exceeded');
    expect(head()).toBe(atLimit.commit);
  }, 30000);
  it('enforces the 500 file boundary against the final tree', async () => {
    seedPack('packs/pack');
    const dir = path.join(seed, 'packs/pack/many');
    mkdirSync(dir);
    for (let i = 0; i < 496; i++) writeFileSync(path.join(dir, `f${i}`), 'x');
    current = publish();
    const atLimit = await savePackCandidate(input({ changes: [{ path: 'final', operation: 'create', contentBase64: '', mode: '100644' }] }));
    const clone = await createTempRepo();
    try {
      await cloneClawflows(clone, input().config);
      const snapshot = await readPackScriptSnapshot(clone, 'flow', 'pack', 'draft', { includeVersionFiles: true });
      expect(snapshot.versionFiles).toHaveLength(500);
    } finally { await removeTempRepo(clone); }
    await expect(savePackCandidate(input({ sourceVersion: 'draft', sourceCommit: atLimit.commit, expectedSavedHead: atLimit.commit,
      changes: [{ path: 'extra', operation: 'create', contentBase64: '', mode: '100644' }] }))).rejects.toThrow('Pack file count limit exceeded');
    const repaired = await savePackCandidate(input({ sourceVersion: 'draft', sourceCommit: atLimit.commit, expectedSavedHead: atLimit.commit,
      changes: [{ path: 'final', operation: 'delete' }, { path: 'extra', operation: 'create', contentBase64: '', mode: '100644' }] }));
    expect(head()).toBe(repaired.commit);
  }, 60000);

  it.each(['', 'pack', 'packs/pack'])('allows a Pack ID edit in layout %s while preserving all resource bytes and modes', async prefix => {
    seedPack(prefix);
    const base = prefix ? `${prefix}/` : '';
    writeFileSync(path.join(seed, base, 'asset.bin'), Buffer.from([0, 255, 128, 1]));
    chmodSync(path.join(seed, base, 'scripts/shared.txt'), 0o755);
    current = publish();
    const manifest = '# renamed pack\nid: renamed\nversion: 2\nworkflows: [flow]\n';
    const saved = await withPreparedPackCandidate(input({ changes: [
      { path: 'workflow.pack.yaml', operation: 'replace', contentBase64: b64(manifest), mode: '100644' },
    ] }), async (prepared, push) => {
      expect(prepared.packId).toBe('renamed');
      expect(prepared.manifestContent).toBe(manifest);
      await push(); return prepared;
    });
    const destination = prefix === '' ? '' : prefix === 'pack' ? 'renamed/' : 'packs/renamed/';
    expect(saved.packPath).toBe(destination);
    expect(saved.workflowFile).toBe('workflows/flow.yaml');
    for (const file of ['asset.bin', 'scripts/shared.txt', 'workflows/flow.yaml']) {
      expect(runGit(root, ['--git-dir', bare, 'rev-parse', `${saved.commit}:${destination}${file}`]))
        .toBe(runGit(root, ['--git-dir', bare, 'rev-parse', `${current}:${base}${file}`]));
    }
    expect(runGit(root, ['--git-dir', bare, 'ls-tree', saved.commit, `${destination}scripts/shared.txt`])).toMatch(/^100755 blob/);
    expect(execFileSync('git', ['--git-dir', bare, 'show', `${saved.commit}:${destination}asset.bin`])).toEqual(Buffer.from([0, 255, 128, 1]));
    if (base) expect(runGit(root, ['--git-dir', bare, 'ls-tree', '-r', '--name-only', saved.commit, base])).toBe('');
    expect(show(saved.commit, 'outside.txt')).toBe('outside');
    expect(runGit(root, ['ls-remote', bare, 'refs/heads/saved/flow'])).toBe('');
    expect(runGit(root, ['--git-dir', bare, 'tag', '-l'])).toBe('');
  });

  it.each(['file', 'directory', 'pack'])('rejects a nested Pack rename onto an occupied %s without overwriting', async kind => {
    seedPack('packs/pack');
    const target = path.join(seed, 'packs/taken');
    if (kind === 'file') writeFileSync(target, 'owned file');
    else {
      mkdirSync(target);
      writeFileSync(path.join(target, 'resource'), 'owned resource');
      if (kind === 'pack') {
        mkdirSync(path.join(target, 'workflows'));
        writeFileSync(path.join(target, 'workflows/other.yaml'), 'id: other\n');
        writeFileSync(path.join(target, 'workflow.pack.yaml'), 'id: taken\nworkflows: [other]\n');
      }
    }
    current = publish();
    const refs = runGit(root, ['--git-dir', bare, 'show-ref']);
    await expect(savePackCandidate(input({ changes: [
      { path: 'workflow.pack.yaml', operation: 'replace', contentBase64: b64('id: taken\nworkflows: [flow]\n'), mode: '100644' },
    ] }))).rejects.toThrow('Pack rename destination');
    expect(runGit(root, ['--git-dir', bare, 'show-ref'])).toBe(refs);
  });

  it('never permits a workflow identity change as part of a Pack rename', async () => {
    current = seedPack('packs/pack');
    await expect(savePackCandidate(input({ changes: [
      { path: 'workflow.pack.yaml', operation: 'replace', contentBase64: b64('id: renamed\nworkflows: [other]\n'), mode: '100644' },
      { path: 'workflows/flow.yaml', operation: 'replace', contentBase64: b64('id: other\n'), mode: '100644' },
    ] }))).rejects.toThrow();
    expect(runGit(root, ['ls-remote', bare, 'refs/heads/saved/flow'])).toBe('');
  });

  it('keeps a direct Pack named packs in the direct layout when renamed', async () => {
    seedPack('packs');
    writeFileSync(path.join(seed, 'packs/workflow.pack.yaml'), 'id: packs\nworkflows: [flow]\n');
    current = publish();
    const saved = await withPreparedPackCandidate(input({ changes: [
      { path: 'workflow.pack.yaml', operation: 'replace', contentBase64: b64('id: renamed\nworkflows: [flow]\n'), mode: '100644' },
    ] }), async (p, push) => { await push(); return p; });
    expect(saved.packPath).toBe('renamed/');
    expect(show(saved.commit, 'renamed/scripts/shared.txt')).toBe('shared legacy');
  });

  it('rejects a Pack rename when that ID already exists in a different layout', async () => {
    seedPack('packs/pack');
    mkdirSync(path.join(seed, 'taken/workflows'), { recursive: true });
    writeFileSync(path.join(seed, 'taken/workflow.pack.yaml'), 'id: taken\nworkflows: [other]\n');
    writeFileSync(path.join(seed, 'taken/workflows/other.yaml'), 'id: other\n');
    current = publish();
    await expect(savePackCandidate(input({ changes: [
      { path: 'workflow.pack.yaml', operation: 'replace', contentBase64: b64('id: taken\nworkflows: [flow]\n'), mode: '100644' },
    ] }))).rejects.toThrow('Pack rename destination identity is occupied');
    expect(runGit(root, ['ls-remote', bare, 'refs/heads/saved/flow'])).toBe('');
  });

});
