// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync, chmodSync, renameSync, symlinkSync, unlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import * as gitExecution from '@avernet/clawweb-shared/server/services/git-execution';
import { createLocalRemote, runGit } from './clawflows-git-client-fixture.js';
import { prepareWorkflowSnapshotRelease } from '../workflow-snapshot-release-git.js';
import { assertDeploymentTreePreserved } from '../deploy-tree-guard.js';

let root: string, remote: string, seed: string, repo: string, active: string, candidate: string;
const author = { name: 'Fixture', email: 'fixture@example.invalid' }, activeTag = 'deploy/flow/#1';
const document = { id: 'flow', content: 'legacy content', nodes: [{ id: 'step', executor: { type: 'agent' } }] };
const yaml = '# legacy formatting\ncontent: legacy content\nid: flow\nnodes: [{id: step, executor: {type: agent}}]\n';
const git = (cwd: string, ...args: string[]) => runGit(cwd, args);
const remoteGit = (...args: string[]) => git(root, '--git-dir', remote, ...args);
const refs = () => remoteGit('show-ref');
const input = () => ({ repoDir: repo, workflowId: 'flow', packId: 'pack', candidateCommit: candidate,
  dbSpecJson: JSON.stringify(document), deployNumber: 2, version: 2, author, requestId: 'request-123', activeDeployment: { tag_name: activeTag } });
function commit(message: string) { git(seed, 'add', '-A'); git(seed, 'commit', '-m', message); return git(seed, 'rev-parse', 'HEAD'); }
function refresh() { git(repo, 'fetch', '--no-tags', 'origin', '+refs/heads/*:refs/remotes/origin/*'); }
function save() { git(seed, 'push', remote, `${candidate}:refs/heads/wf/flow`); refresh(); }
function activate(commit: string) { git(seed, 'push', '--force', remote, `${commit}:refs/tags/${activeTag}`); }
beforeEach(() => {
  root = mkdtempSync(path.join(tmpdir(), 'snapshot-release-')); remote = createLocalRemote(root); seed = path.join(root, 'seed');
  mkdirSync(path.join(seed, 'workflows')); mkdirSync(path.join(seed, 'scripts'));
  writeFileSync(path.join(seed, 'workflow.pack.yaml'), 'id: pack\nworkflows: [flow]\n'); writeFileSync(path.join(seed, 'workflows/flow.yaml'), yaml);
  writeFileSync(path.join(seed, 'scripts/run.sh'), '#!/bin/sh\n'); chmodSync(path.join(seed, 'scripts/run.sh'), 0o755);
  active = commit('active'); git(seed, 'tag', '-a', activeTag, active, '-m', 'active'); git(seed, 'push', remote, `refs/tags/${activeTag}`);
  writeFileSync(path.join(seed, 'asset.bin'), Buffer.from([0, 255, 1])); candidate = commit('saved'); git(seed, 'push', remote, `${candidate}:refs/heads/wf/flow`);
  repo = path.join(root, 'repo'); git(root, 'clone', remote, repo); refresh();
});
afterEach(() => { vi.restoreAllMocks(); rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); });

describe('tag-only snapshot release preparation with an authorized active record', () => {
  it('prepares candidate itself without publishing or creating a child commit', async () => {
    const before = refs(); const result = await prepareWorkflowSnapshotRelease(input());
    expect(result.candidateCommit).toBe(candidate); expect(result.previousCommit).toBe(active);
    expect(result.releaseCommit).toBe(candidate); expect(result.commit).toBe(candidate); expect(result.content).toBe(yaml);
    expect(git(repo, 'ls-tree', result.commit, 'scripts/run.sh')).toMatch(/^100755 blob/); expect(refs()).toBe(before);
  });
  it('allows first deployment only with an explicit null active record', async () => {
    const before = refs(); const prepared = await prepareWorkflowSnapshotRelease({ ...input(), activeDeployment: null });
    expect(prepared.previousCommit).toBe(''); expect(prepared.commit).toBe(candidate); expect(refs()).toBe(before);
    await expect(prepareWorkflowSnapshotRelease({ ...input(), activeDeployment: undefined } as never)).rejects.toThrow();
  });
  it('rejects arbitrary history, absent wf, wrong identities and divergent DB', async () => {
    await expect(prepareWorkflowSnapshotRelease({ ...input(), candidateCommit: active })).rejects.toThrow();
    await expect(prepareWorkflowSnapshotRelease({ ...input(), packId: 'other' })).rejects.toThrow();
    await expect(prepareWorkflowSnapshotRelease({ ...input(), workflowId: 'other' })).rejects.toThrow();
    await expect(prepareWorkflowSnapshotRelease({ ...input(), dbSpecJson: JSON.stringify({ ...document, content: 'unsaved' }) })).rejects.toThrow();
    remoteGit('update-ref', '-d', 'refs/heads/wf/flow'); await expect(prepareWorkflowSnapshotRelease(input())).rejects.toThrow();
  });
  it('does not substitute a wf draft for a missing active production tag', async () => {
    remoteGit('update-ref', '-d', `refs/tags/${activeTag}`); const before = refs();
    await expect(prepareWorkflowSnapshotRelease(input())).rejects.toThrow(); expect(refs()).toBe(before);
  });
  it.each(['0.10000000000000001', '1e-999', '9007199254740991.1', '-0'])('rejects lossy DB numeric literal %s', async numeric => {
    const raw = JSON.stringify(document).replace('"id":"flow"', `"id":"flow","amount":${numeric}`);
    writeFileSync(path.join(seed, 'workflows/flow.yaml'), `${yaml}amount: ${String(JSON.parse(numeric))}\n`); candidate = commit('rounded'); save();
    const before = refs(); await expect(prepareWorkflowSnapshotRelease({ ...input(), dbSpecJson: raw })).rejects.toThrow(); expect(refs()).toBe(before);
  });
  it.each(['removed', 'mode'])('protects production scripts against %s despite wf already being the candidate', async change => {
    if (change === 'removed') rmSync(path.join(seed, 'scripts/run.sh')); else chmodSync(path.join(seed, 'scripts/run.sh'), 0o644);
    candidate = commit(change); save(); const before = refs();
    await expect(prepareWorkflowSnapshotRelease(input())).rejects.toMatchObject({ code: 'DEPLOY_TREE_CONFLICT' }); expect(refs()).toBe(before);
  });
  it('protects outside blobs for a nested Pack', async () => {
    mkdirSync(path.join(seed, 'packs/pack'), { recursive: true });
    renameSync(path.join(seed, 'workflow.pack.yaml'), path.join(seed, 'packs/pack/workflow.pack.yaml'));
    renameSync(path.join(seed, 'workflows'), path.join(seed, 'packs/pack/workflows'));
    active = commit('nested active'); activate(active);
    writeFileSync(path.join(seed, 'README'), 'unseen outside content'); candidate = commit('outside change'); save();
    await expect(prepareWorkflowSnapshotRelease(input())).rejects.toMatchObject({ code: 'DEPLOY_TREE_CONFLICT', paths: ['README'] });
  });
  it('keeps symlink/gitlink/outside object mode protection and unusual Git names', async () => {
    symlinkSync('first-target', path.join(seed, 'outside-link')); writeFileSync(path.join(seed, 'tab\tline\nbinary'), Buffer.from([0, 255]));
    git(seed, 'add', '-A'); git(seed, 'update-index', '--add', '--cacheinfo', '160000', active, 'outside-module'); git(seed, 'commit', '-m', 'external entries');
    const before = git(seed, 'rev-parse', 'HEAD'); unlinkSync(path.join(seed, 'outside-link')); symlinkSync('second-target', path.join(seed, 'outside-link'));
    writeFileSync(path.join(seed, 'tab\tline\nbinary'), Buffer.from([0, 254])); git(seed, 'add', '-A');
    git(seed, 'update-index', '--add', '--cacheinfo', '160000', before, 'outside-module'); git(seed, 'commit', '-m', 'external changed'); const after = git(seed, 'rev-parse', 'HEAD');
    await expect(assertDeploymentTreePreserved(seed, before, after, 'request', 'packs/pack/')).rejects.toMatchObject({ code: 'DEPLOY_TREE_CONFLICT', paths: ['outside-link', 'outside-module', 'tab\tline\nbinary'] });
    await expect(assertDeploymentTreePreserved(seed, before, after, 'request', '')).resolves.toBeUndefined();
    await expect(assertDeploymentTreePreserved(seed, before, after, 'request', '../pack/')).rejects.toThrow('Invalid Pack directory');
  });
  it('fails closed for non-UTF8 deployed paths', async () => {
    const oid = git(seed, 'rev-parse', `${candidate}:asset.bin`); const record = Buffer.concat([Buffer.from(`100644 blob ${oid}\t`), Buffer.from([0xff, 0])]);
    const tree = execFileSync('git', ['mktree', '-z'], { cwd: seed, input: record, encoding: 'utf8' }).trim();
    const bad = git(seed, 'commit-tree', tree, '-m', 'invalid path'); await expect(assertDeploymentTreePreserved(seed, bad, candidate, 'request', '')).rejects.toThrow();
  });
  it('keeps a nested packs/ manifest as an opaque root Pack resource', async () => {
    mkdirSync(path.join(seed, 'packs/other'), { recursive: true });
    const nested = 'id: other\nworkflows: [flow]\n';
    writeFileSync(path.join(seed, 'packs/other/workflow.pack.yaml'), nested);
    candidate = commit('nested resource'); save();
    const prepared = await prepareWorkflowSnapshotRelease(input());
    expect(prepared.commit).toBe(candidate);
    expect(git(repo, 'show', `${prepared.commit}:packs/other/workflow.pack.yaml`)).toBe(nested.trimEnd());
  });
  it('rejects mixed candidate layout and malformed or forged YAML', async () => {
    mkdirSync(path.join(seed, 'scripts/other'), { recursive: true }); writeFileSync(path.join(seed, 'scripts/other/workflow.pack.yaml'), 'id: other\nworkflows: [flow]\n');
    candidate = commit('mixed'); save(); await expect(prepareWorkflowSnapshotRelease(input())).rejects.toThrow();
    rmSync(path.join(seed, 'scripts/other'), { recursive: true }); writeFileSync(path.join(seed, 'workflows/flow.yaml'), 'id: flow\nid: other\nnodes: []\n');
    candidate = commit('invalid YAML'); save(); await expect(prepareWorkflowSnapshotRelease(input())).rejects.toThrow();
  });
  it('freezes a confirmed saved candidate even when wf advances during preparation', async () => {
    const execute = gitExecution.executeGit; let changed = false;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      const output = await execute(args, options);
      if (args[0] === 'ls-remote' && !changed) { changed = true; remoteGit('update-ref', 'refs/heads/wf/flow', active); }
      return output;
    });
    const prepared = await prepareWorkflowSnapshotRelease(input()); expect(prepared.commit).toBe(candidate); expect(changed).toBe(true);
  });
  it('rejects a production tag changing during preparation', async () => {
    const execute = gitExecution.executeGit; let changed = false;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      const output = await execute(args, options);
      if (args[0] === 'ls-remote' && args.some(arg => arg.includes('refs/tags/')) && !changed) { changed = true; activate(candidate); }
      return output;
    });
    await expect(prepareWorkflowSnapshotRelease(input())).rejects.toThrow(); expect(changed).toBe(true);
  });
});
