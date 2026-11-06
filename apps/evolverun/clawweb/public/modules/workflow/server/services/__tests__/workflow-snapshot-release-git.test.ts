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
const author = { name: 'Fixture', email: 'fixture@example.invalid' };
const document = { id: 'flow', content: 'legacy content', nodes: [{ id: 'step', executor: { type: 'agent' } }] };
const yaml = '# legacy formatting\ncontent: legacy content\nid: flow\nnodes: [{id: step, executor: {type: agent}}]\n';
const git = (cwd: string, ...args: string[]) => runGit(cwd, args);
const remoteGit = (...args: string[]) => git(root, '--git-dir', remote, ...args);
const setRef = (ref: string, oid: string) => remoteGit('update-ref', ref, oid);
const refs = () => remoteGit('show-ref');
const input = () => ({ repoDir: repo, workflowId: 'flow', packId: 'pack', candidateCommit: candidate,
  dbSpecJson: JSON.stringify(document), deployNumber: 1, version: 1, author, requestId: 'request-123' });
function commit(message: string) { git(seed, 'add', '-A'); git(seed, 'commit', '-m', message); return git(seed, 'rev-parse', 'HEAD'); }
function refresh() { git(repo, 'fetch', '--no-tags', 'origin', '+refs/heads/*:refs/remotes/origin/*'); }
beforeEach(() => {
  root = mkdtempSync(path.join(tmpdir(), 'snapshot-release-')); remote = createLocalRemote(root); seed = path.join(root, 'seed');
  mkdirSync(path.join(seed, 'workflows')); mkdirSync(path.join(seed, 'scripts'));
  writeFileSync(path.join(seed, 'workflow.pack.yaml'), 'id: pack\nworkflows: [flow]\n');
  writeFileSync(path.join(seed, 'workflows/flow.yaml'), yaml);
  writeFileSync(path.join(seed, 'scripts/run.sh'), '#!/bin/sh\n'); chmodSync(path.join(seed, 'scripts/run.sh'), 0o755);
  active = commit('active'); git(seed, 'push', remote, `${active}:refs/heads/wf/flow`);
  writeFileSync(path.join(seed, 'asset.bin'), Buffer.from([0, 255, 1])); candidate = commit('saved');
  git(seed, 'push', remote, `${candidate}:refs/heads/saved/flow`);
  repo = path.join(root, 'repo'); git(root, 'clone', remote, repo); refresh();
});
afterEach(() => { vi.restoreAllMocks(); rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); });

describe('snapshot release preparation (not saved provenance)', () => {
  it('prepares exact saved tree and sole candidate parent, preserving legacy content and without publishing', async () => {
    const before = refs(); const result = await prepareWorkflowSnapshotRelease(input());
    expect(result.candidateCommit).toBe(candidate); expect(result.previousCommit).toBe(active);
    expect(result.content).toBe(yaml); expect(result.packId).toBe('pack');
    expect(git(repo, 'rev-parse', `${result.releaseCommit}^{tree}`)).toBe(git(repo, 'rev-parse', `${candidate}^{tree}`));
    expect(git(repo, 'show', '-s', '--format=%P', result.releaseCommit)).toBe(candidate);
    expect(git(repo, 'ls-tree', result.releaseCommit, 'scripts/run.sh')).toMatch(/^100755 blob/);
    expect(refs()).toBe(before);
    // Matching saved and DB are not a completed-save receipt; callers must verify their journal independently.
  });
  it('rejects history, missing saved, mismatched identities and mismatched DB projections', async () => {
    await expect(prepareWorkflowSnapshotRelease({ ...input(), candidateCommit: active })).rejects.toThrow();
    await expect(prepareWorkflowSnapshotRelease({ ...input(), candidateCommit: 'HEAD' })).rejects.toThrow();
    await expect(prepareWorkflowSnapshotRelease({ ...input(), packId: 'other' })).rejects.toThrow();
    await expect(prepareWorkflowSnapshotRelease({ ...input(), workflowId: 'other' })).rejects.toThrow();
    await expect(prepareWorkflowSnapshotRelease({ ...input(), dbSpecJson: JSON.stringify({ ...document, content: 'changed' }) })).rejects.toThrow();
    remoteGit('update-ref', '-d', 'refs/heads/saved/flow');
    await expect(prepareWorkflowSnapshotRelease(input())).rejects.toThrow();
  });
  it.each(['0.10000000000000001', '1e-999', '9007199254740991.1', '-0'])('rejects lossy raw DB numeric literal %s before preparing a release', async numeric => {
    const raw = JSON.stringify(document).replace('\"id\":\"flow\"', `\"id\":\"flow\",\"amount\":${numeric}`);
    // The saved YAML matches the rounded JS interpretation exactly; rejecting
    // it must depend on checking the original DB bytes, not a field mismatch.
    const rounded = JSON.parse(numeric) as number;
    writeFileSync(path.join(seed, 'workflows/flow.yaml'), `${yaml}amount: ${String(rounded)}\n`);
    candidate = commit('matching rounded candidate'); git(seed, 'push', remote, `${candidate}:refs/heads/saved/flow`); refresh();
    const before = refs();
    await expect(prepareWorkflowSnapshotRelease({ ...input(), dbSpecJson: raw })).rejects.toThrow();
    expect(refs()).toBe(before);
  });
  it.each(['removed', 'mode'])('refuses production resource %s even if candidate is saved', async change => {
    if (change === 'removed') rmSync(path.join(seed, 'scripts/run.sh'));
    else chmodSync(path.join(seed, 'scripts/run.sh'), 0o644);
    candidate = commit(change); git(seed, 'push', remote, `${candidate}:refs/heads/saved/flow`); refresh();
    const before = refs(); await expect(prepareWorkflowSnapshotRelease(input())).rejects.toMatchObject({ code: 'DEPLOY_TREE_CONFLICT' });
    expect(refs()).toBe(before);
  });
  it('rejects changed outside blobs for a nested Pack, while allowing changes inside it', async () => {
    mkdirSync(path.join(seed, 'packs')); mkdirSync(path.join(seed, 'packs/pack'));
    renameSync(path.join(seed, 'workflow.pack.yaml'), path.join(seed, 'packs/pack/workflow.pack.yaml'));
    renameSync(path.join(seed, 'workflows'), path.join(seed, 'packs/pack/workflows'));
    mkdirSync(path.join(seed, 'packs/pack-extra'));
    writeFileSync(path.join(seed, 'packs/pack-extra/data.bin'), Buffer.from([0, 1, 2]));
    writeFileSync(path.join(seed, 'packs/pack/local.txt'), 'before');
    active = commit('nested active'); git(seed, 'push', remote, `${active}:refs/heads/wf/flow`);
    writeFileSync(path.join(seed, 'packs/pack/local.txt'), 'allowed');
    writeFileSync(path.join(seed, 'new-outside.txt'), 'addition');
    candidate = commit('inside change'); git(seed, 'push', remote, `${candidate}:refs/heads/saved/flow`); refresh();
    await expect(prepareWorkflowSnapshotRelease(input())).resolves.toBeDefined();
    writeFileSync(path.join(seed, 'packs/pack-extra/data.bin'), Buffer.from([0, 1, 3]));
    candidate = commit('outside change'); git(seed, 'push', remote, `${candidate}:refs/heads/saved/flow`); refresh();
    const before = refs();
    await expect(prepareWorkflowSnapshotRelease(input())).rejects.toMatchObject({ code: 'DEPLOY_TREE_CONFLICT', paths: ['packs/pack-extra/data.bin'] });
    expect(refs()).toBe(before);
  });

  it('compares outside symlink blobs and gitlink IDs without interpreting unusual path names', async () => {
    symlinkSync('first-target', path.join(seed, 'outside-link'));
    writeFileSync(path.join(seed, 'tab\tline\nbinary'), Buffer.from([0, 255]));
    git(seed, 'add', '-A');
    git(seed, 'update-index', '--add', '--cacheinfo', '160000', active, 'outside-module');
    git(seed, 'commit', '-m', 'external entries'); const before = git(seed, 'rev-parse', 'HEAD');
    unlinkSync(path.join(seed, 'outside-link')); symlinkSync('second-target', path.join(seed, 'outside-link'));
    writeFileSync(path.join(seed, 'tab\tline\nbinary'), Buffer.from([0, 254]));
    git(seed, 'add', '-A');
    git(seed, 'update-index', '--add', '--cacheinfo', '160000', before, 'outside-module');
    git(seed, 'commit', '-m', 'changed external entries'); const after = git(seed, 'rev-parse', 'HEAD');
    await expect(assertDeploymentTreePreserved(seed, before, after, 'request', 'packs/pack/'))
      .rejects.toMatchObject({ code: 'DEPLOY_TREE_CONFLICT', paths: ['outside-link', 'outside-module', 'tab\tline\nbinary'] });
    // Root selection has no outside paths: retain its previous content behavior.
    await expect(assertDeploymentTreePreserved(seed, before, after, 'request', '')).resolves.toBeUndefined();
    await expect(assertDeploymentTreePreserved(seed, before, after, 'request', '../pack/')).rejects.toThrow('Invalid Pack directory');
  });

  it('fails closed on non-UTF-8 Git paths in the deployed tree', async () => {
    // Construct directly in Git: macOS filesystems reject invalid UTF-8 names.
    const oid = git(seed, 'rev-parse', `${candidate}:asset.bin`);
    const record = Buffer.concat([Buffer.from(`100644 blob ${oid}\t`), Buffer.from([0xff, 0])]);
    const tree = execFileSync('git', ['mktree', '-z'], { cwd: seed, input: record, encoding: 'utf8' }).trim();
    const before = git(seed, 'commit-tree', tree, '-m', 'invalid path');
    await expect(assertDeploymentTreePreserved(seed, before, before, 'request', 'packs/pack/')).rejects.toThrow();
  });

  it('does not fall back to wf when saved is missing and permits a genuinely absent wf', async () => {
    remoteGit('update-ref', '-d', 'refs/heads/saved/flow');
    await expect(prepareWorkflowSnapshotRelease({ ...input(), candidateCommit: active })).rejects.toThrow('Saved ref does not match candidate');
    setRef('refs/heads/saved/flow', candidate); remoteGit('update-ref', '-d', 'refs/heads/wf/flow');
    const before = refs(); const prepared = await prepareWorkflowSnapshotRelease(input());
    expect(prepared.previousCommit).toBe(''); expect(refs()).toBe(before);
  });
  it('rejects duplicate matching manifests instead of selecting an ambiguous Pack', async () => {
    mkdirSync(path.join(seed, 'pack'));
    renameSync(path.join(seed, 'workflow.pack.yaml'), path.join(seed, 'pack/workflow.pack.yaml'));
    renameSync(path.join(seed, 'workflows'), path.join(seed, 'pack/workflows'));
    mkdirSync(path.join(seed, 'other')); mkdirSync(path.join(seed, 'other/workflows'));
    writeFileSync(path.join(seed, 'other/workflow.pack.yaml'), 'id: other\nworkflows: [flow]\n');
    writeFileSync(path.join(seed, 'other/workflows/flow.yaml'), yaml);
    candidate = commit('ambiguous'); git(seed, 'push', remote, `${candidate}:refs/heads/saved/flow`); refresh();
    await expect(prepareWorkflowSnapshotRelease(input())).rejects.toThrow('Candidate manifest workflow mismatch');
  });
  it('rejects mixed root and nested Pack layout separately', async () => {
    mkdirSync(path.join(seed, 'other'));
    writeFileSync(path.join(seed, 'other/workflow.pack.yaml'), 'id: other\nworkflows: [flow]\n');
    candidate = commit('mixed'); git(seed, 'push', remote, `${candidate}:refs/heads/saved/flow`); refresh();
    await expect(prepareWorkflowSnapshotRelease(input())).rejects.toThrow('Mixed root and nested Pack layout');
  });
  it('does not trust a saved ref from another workflow or an arbitrary SHA', async () => {
    remoteGit('update-ref', '-d', 'refs/heads/saved/flow'); setRef('refs/heads/saved/other', candidate);
    await expect(prepareWorkflowSnapshotRelease(input())).rejects.toThrow('Saved ref does not match candidate');
  });
  it('fails closed when the matching saved object is not locally available', async () => {
    const emptyRepo = path.join(root, 'unfetched'); git(root, 'clone', '--no-local', '--single-branch', remote, emptyRepo);
    const before = refs();
    await expect(prepareWorkflowSnapshotRelease({ ...input(), repoDir: emptyRepo })).rejects.toThrow();
    expect(refs()).toBe(before);
  });
  it('rejects malformed or identity-mixed candidate YAML without changing refs', async () => {
    writeFileSync(path.join(seed, 'workflows/flow.yaml'), 'id: flow\nid: other\nnodes: []\n');
    candidate = commit('invalid-yaml'); git(seed, 'push', remote, `${candidate}:refs/heads/saved/flow`); refresh();
    const before = refs(); await expect(prepareWorkflowSnapshotRelease(input())).rejects.toThrow();
    expect(refs()).toBe(before);
  });
  it.each(['saved', 'wf'])('rejects %s moving while preparing', async branch => {
    const execute = gitExecution.executeGit; let observed = false;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      const output = await execute(args, options);
      if (args[0] === 'ls-remote' && !observed) {
        observed = true; setRef(`refs/heads/${branch}/flow`, branch === 'saved' ? active : candidate);
      }
      return output;
    });
    await expect(prepareWorkflowSnapshotRelease(input())).rejects.toThrow('Workflow refs changed during preparation');
    expect(observed).toBe(true);
  });
});
