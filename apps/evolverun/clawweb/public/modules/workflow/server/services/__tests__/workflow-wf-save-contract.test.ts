// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createHash } from 'node:crypto';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createLocalRemote, runGit } from './clawflows-git-client-fixture.js';
import * as execution from '@avernet/clawweb-shared/server/services/git-execution';
import { withPreparedPackCandidate, resumePreparedPackCandidate, type SavePackCandidateInput } from '../pack-candidate-save.js';
import { withPreparedWorkflowSpec } from '../pack-spec-save.js';
import { writePackScripts } from '../pack-script-write.js';
import { cloneClawflows, commitAndPushSaved } from '../clawflows-git-client.js';
import { resolveSavedWorkflowRef } from '../workflow-saved-ref.js';
import type { WorkflowSpec } from '../../workflow.js';

vi.setConfig({ testTimeout: 30_000 });
let root: string, remote: string, seed: string;
const git = (dir: string, ...args: string[]) => runGit(dir, args);
const rg = (...args: string[]) => git(root, '--git-dir', remote, ...args);
const head = (branch = 'wf') => git(root, 'ls-remote', remote, `refs/heads/${branch}/flow`).split('\t')[0] || null;
const config = () => ({ remoteUrl: remote, username: 'test', email: 'test@example.invalid' });
const author = { name: 'test', email: 'test@example.invalid' };
const spec = { id: 'flow', title: 'edited', nodes: [] } as WorkflowSpec;
function pack(prefix = '') {
  const dir = path.join(seed, prefix); mkdirSync(path.join(dir, 'workflows'), { recursive: true });
  mkdirSync(path.join(dir, 'scripts'), { recursive: true });
  writeFileSync(path.join(dir, 'workflow.pack.yaml'), 'id: pack\nversion: 1\nworkflows: [flow]\n');
  writeFileSync(path.join(dir, 'workflows/flow.yaml'), 'id: flow\ntitle: before\nnodes: []\n');
  writeFileSync(path.join(dir, 'scripts/run.sh'), '#!/bin/sh\necho original\n');
  writeFileSync(path.join(dir, 'asset.bin'), Buffer.from([0, 128, 255]));
  git(seed, 'add', '.'); git(seed, 'update-index', '--chmod=+x', `${prefix ? prefix + '/' : ''}scripts/run.sh`);
  git(seed, 'commit', '-m', 'full pack'); return git(seed, 'rev-parse', 'HEAD');
}
function input(source: string, wf: string | null = source): SavePackCandidateInput {
  return { workflowId: 'flow', sourceVersion: 'draft', sourceCommit: source, expectedSavedHead: wf, config: config(), changes: [] };
}
function publish(commit: string, branch: string) { git(seed, 'push', remote, `${commit}:refs/heads/${branch}/flow`); }
function assertRootMetadata(commit: string) {
  const message = rg('show', '-s', '--format=%B', commit);
  expect(message).toMatch(/^ClawMind saved candidate v1\n/);
  const metadata = JSON.parse(message.slice('ClawMind saved candidate v1\n'.length));
  const yaml = rg('show', `${commit}:workflows/flow.yaml`) + '\n';
  expect(metadata).toMatchObject({ workflowId: 'flow', packId: 'pack', specPath: 'workflows/flow.yaml',
    specDigest: createHash('sha256').update(yaml).digest('hex'), packDigest: rg('rev-parse', `${commit}^{tree}`), baseDeployNumber: null });
  expect(metadata.createdAt).toEqual(expect.any(String)); expect(metadata).not.toHaveProperty('packPath');
}
beforeEach(() => { root = mkdtempSync(path.join(tmpdir(), 'wf-contract-')); remote = createLocalRemote(root); seed = path.join(root, 'seed'); });
afterEach(() => { vi.restoreAllMocks(); rmSync(root, { recursive: true, force: true }); });

describe('wf full Pack save contract', () => {
  it('writes YAML-only to wf, keeping untouched blob ids and executable bits, with real root metadata', async () => {
    const base = pack(); publish(base, 'wf');
    const result = await withPreparedWorkflowSpec({ workflowId: 'flow', packId: 'pack', spec, config: config(), author, expectedSavedHead: base }, async (p, push) => { await push(); return p; });
    expect(head()).toBe(result.commit); expect(head('saved')).toBeNull(); assertRootMetadata(result.commit);
    for (const file of ['scripts/run.sh', 'asset.bin', 'workflow.pack.yaml']) expect(rg('ls-tree', result.commit, file)).toBe(rg('ls-tree', base, file));
    expect(rg('ls-tree', result.commit, 'scripts/run.sh')).toMatch(/^100755/);
  });
  it('ignores an identical old saved tree and saves from wf', async () => {
    const base = pack(); publish(base, 'wf');
    git(seed, 'commit', '--allow-empty', '-m', 'legacy draft');
    const legacy = git(seed, 'rev-parse', 'HEAD'); publish(legacy, 'saved');
    const result = await withPreparedPackCandidate(input(base), async (p, push) => { await push(); return p; });
    expect(head()).toBe(result.commit); expect(head('saved')).toBe(legacy); expect(rg('ls-tree', '-r', result.commit)).toBe(rg('ls-tree', '-r', base));
    assertRootMetadata(result.commit);
  });
  it('rejects a sourceCommit other than wf even if advertised on a legacy ref', async () => {
    const base = pack(); publish(base, 'wf');
    git(seed, 'rm', '-f', 'scripts/run.sh'); git(seed, 'commit', '-m', 'old saved writer lost scripts');
    const legacy = git(seed, 'rev-parse', 'HEAD'); publish(legacy, 'saved');
    const callback = vi.fn();
    await expect(withPreparedPackCandidate(input(legacy, base), callback)).rejects.toThrow(/source|wf/i);
    expect(callback).not.toHaveBeenCalled(); expect(head()).toBe(base); expect(head('saved')).toBe(legacy);
    expect(rg('show', `${base}:scripts/run.sh`)).toContain('original');
  });
  it('continues a metadata root wf despite an unrelated divergent legacy saved tree', async () => {
    const base = pack(); publish(base, 'wf');
    const first = await withPreparedPackCandidate(input(base), async (_p, push) => push());
    writeFileSync(path.join(seed, 'legacy-only.txt'), 'old branch'); git(seed, 'add', '.'); git(seed, 'commit', '-m', 'legacy divergent');
    const legacy = git(seed, 'rev-parse', 'HEAD'); publish(legacy, 'saved');
    const result = await withPreparedPackCandidate(input(first.commit), async (_p, push) => push());
    expect(head()).toBe(result.commit); expect(head('saved')).toBe(legacy); assertRootMetadata(result.commit);
    const reader = path.join(root, 'reader'); mkdirSync(reader); await cloneClawflows(reader, config());
    expect(await resolveSavedWorkflowRef(reader, 'flow')).toMatchObject({ commit: result.commit, ref: 'refs/heads/wf/flow' });
    expect(rg('ls-tree', '-r', result.commit)).toBe(rg('ls-tree', '-r', first.commit));
  });
  it('first save creates wf only and can resume its exact prepared commit', async () => {
    const prepared = await withPreparedWorkflowSpec({ workflowId: 'flow', packId: 'pack', spec, config: config(), author, createOnly: true }, async p => p);
    expect(head()).toBeNull(); expect(head('saved')).toBeNull();
    await resumePreparedPackCandidate(prepared, config()); expect(head()).toBe(prepared.commit); expect(head('saved')).toBeNull();
    expect(await resumePreparedPackCandidate(prepared, config())).toEqual({ branch: 'wf/flow', commit: prepared.commit });
  });
  it('recovery verifies source provenance even when the exact commit is already on wf', async () => {
    const base = pack(); publish(base, 'wf');
    const prepared = await withPreparedPackCandidate(input(base), async (p, push) => { await push(); return p; });
    const wrongSource = git(seed, 'rev-parse', `${base}^`);
    await expect(resumePreparedPackCandidate({ ...prepared, sourceCommit: wrongSource }, config())).rejects.toThrow(/commit|source|provenance/i);
    expect(head()).toBe(prepared.commit); expect(head('saved')).toBeNull();
  });
  it('rejects a competing wf writer using the original lease', async () => {
    const base = pack(); publish(base, 'wf');
    const first = await withPreparedPackCandidate(input(base), async p => p);
    const second = await withPreparedPackCandidate({ ...input(base), changes: [{ path: 'new.txt', operation: 'create', mode: '100644', contentBase64: Buffer.from('second').toString('base64') }] }, async p => p);
    await resumePreparedPackCandidate(first, config());
    await expect(resumePreparedPackCandidate(second, config())).rejects.toThrow(); expect(head()).toBe(first.commit); expect(head('saved')).toBeNull();
  });
  it('two concurrent wf pushes cannot both win the same lease', async () => {
    const base = pack(); publish(base, 'wf');
    const execute = execution.executeGit; let arrivals = 0; let release!: () => void;
    const both = new Promise<void>(resolve => { release = resolve; });
    vi.spyOn(execution, 'executeGit').mockImplementation(async (args, options) => {
      if (args[0] === 'push') { if (++arrivals === 2) release(); await both; }
      return execute(args, options);
    });
    const save = (content: string) => withPreparedPackCandidate({ ...input(base), changes: [{ path: 'new.txt', operation: 'create', mode: '100644', contentBase64: Buffer.from(content).toString('base64') }] }, async (_p, push) => push());
    const results = await Promise.allSettled([save('first'), save('second')]);
    expect(arrivals).toBe(2); expect(results.filter(result => result.status === 'fulfilled')).toHaveLength(1);
    expect(results.filter(result => result.status === 'rejected')).toHaveLength(1);
    expect(['first', 'second']).toContain(rg('show', `${head()}:new.txt`)); expect(head('saved')).toBeNull();
  });
  it('ignores saved movement between preparation and push', async () => {
    const base = pack(); publish(base, 'wf'); publish(base, 'saved');
    let moved = '';
    const result = await withPreparedPackCandidate(input(base), async (_p, push) => {
      git(seed, 'commit', '--allow-empty', '-m', 'saved changed'); moved = git(seed, 'rev-parse', 'HEAD'); publish(moved, 'saved'); return push();
    });
    expect(head()).toBe(result.commit); expect(head('saved')).toBe(moved);
  });
  it('allows an identical legacy ref appearing while the root wf source is unchanged', async () => {
    const base = pack(); publish(base, 'wf');
    const result = await withPreparedPackCandidate(input(base), async (_p, push) => { publish(base, 'saved'); return push(); });
    expect(head()).toBe(result.commit); expect(head('saved')).toBe(base); assertRootMetadata(result.commit);
  });
  it('preserves a nested wf layout without consulting an independent saved ref', async () => {
    const base = pack('packs/pack'); publish(base, 'wf'); publish(base, 'saved');
    const result = await withPreparedPackCandidate(input(base), async (_p, push) => push());
    expect(head()).toBe(result.commit); expect(head('saved')).toBe(base);
    expect(rg('ls-tree', '-r', result.commit)).toBe(rg('ls-tree', '-r', base));
    expect(rg('show', '-s', '--format=%B', result.commit)).not.toContain('ClawMind saved candidate');
  });
  it('keeps nested wf-only layout without fake metadata', async () => {
    const base = pack('packs/pack'); publish(base, 'wf');
    const result = await withPreparedPackCandidate(input(base), async (_p, push) => push());
    expect(head()).toBe(result.commit); expect(rg('show', '-s', '--format=%B', result.commit)).not.toContain('ClawMind saved candidate');
    expect(rg('ls-tree', '-r', result.commit)).toBe(rg('ls-tree', '-r', base));
  });
  it('rejects non-wf source commits without rewinding wf', async () => {
    const base = pack(); publish(base, 'saved');
    git(seed, 'commit', '--allow-empty', '-m', 'newer wf'); const wf = git(seed, 'rev-parse', 'HEAD'); publish(wf, 'wf');
    await expect(withPreparedPackCandidate(input(base, wf), async (_p, push) => push())).rejects.toThrow();
    const divergent = git(seed, 'commit-tree', `${base}^{tree}`, '-p', base, '-m', 'different saved'); publish(divergent, 'saved');
    await expect(withPreparedPackCandidate(input(divergent, wf), async (_p, push) => push())).rejects.toThrow(); expect(head()).toBe(wf); expect(head('saved')).toBe(divergent);
  });
  it('refuses legacy script writer target saved even when that branch exists', async () => {
    const base = pack('packs/pack'); publish(base, 'saved');
    git(seed, 'remote', 'add', 'origin', remote);
    await expect(writePackScripts({ repoDir: seed, workflowId: 'flow', packId: 'pack', baseCommit: base, targetBranch: 'saved/flow', basePackPath: 'packs/pack', author, changes: [{ path: 'scripts/run.sh', content: 'changed' }] })).rejects.toThrow();
    expect(head('saved')).toBe(base); expect(head()).toBeNull();
  });
  it('legacy commitAndPushSaved cannot recreate saved', async () => {
    const base = pack(); publish(base, 'wf'); const dir = path.join(root, 'clone'); mkdirSync(dir); await cloneClawflows(dir, config());
    git(dir, 'checkout', '--detach', base); writeFileSync(path.join(dir, 'workflows/flow.yaml'), 'id: flow\ntitle: old writer\n');
    // This obsolete primitive has no DB-first callback; fail closed rather than publish an unprotected tree.
    await expect(commitAndPushSaved(dir, 'flow', '', author)).rejects.toThrow(/retired|prepared|unsupported/i);
    expect(head()).toBe(base); expect(head('saved')).toBeNull();
  });
  it('the real push carries the explicit wf lease and never any saved refspec', async () => {
    const base = pack(); publish(base, 'wf'); const execute = execution.executeGit; const pushes: string[][] = [];
    vi.spyOn(execution, 'executeGit').mockImplementation((args, options) => { if (args[0] === 'push') pushes.push(args); return execute(args, options); });
    await withPreparedPackCandidate(input(base), async (_p, push) => push());
    expect(pushes).toHaveLength(1); expect(pushes[0]).toContain(`--force-with-lease=refs/heads/wf/flow:${base}`);
    expect(pushes[0].join(' ')).not.toContain('saved/flow');
  });
});
