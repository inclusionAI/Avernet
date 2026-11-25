// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { once } from 'node:events';
import express from 'express';
import { randomBytes } from 'node:crypto';
import { mkdtempSync, mkdirSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import * as gitExecution from '@avernet/clawweb-shared/server/services/git-execution';
import { createLocalRemote, runGit } from './clawflows-git-client-fixture.js';
import { cloneClawflows } from '../clawflows-git-client.js';
import { withPreparedWorkflowSpec } from '../pack-spec-save.js';
import { createAutoHealConfigTestDb } from '../../config/__tests__/auto-heal-git-fixtures.js';
import { createPackVersionRouter } from '../../routes/pack-version.js';
import { savePackVersionToRemote } from '../pack-version-remote.js';
import {
  withPreparedPackCandidate, pushPreparedPackCandidate, resumePreparedPackCandidate, PackCandidateOutcomeUnknownError, PackCandidateConflictError,
  type SavePackCandidateInput, type PreparedPackCandidate,
} from '../pack-candidate-save.js';
import {
  inspectDeployCandidate, prepareDeployCandidate, pushPreparedDeployment, resumePreparedDeployment, DeploymentOutcomeUnknownError, DeploymentConflictError,
} from '../auto-heal-deploy-git.js';
import { restorePreparedGitBundle, createPreparedGitBundle, readPreparedPackMetadata, MAX_PREPARED_BUNDLE_BYTES } from '../pack-git-prepared.js';

vi.setConfig({ testTimeout: 30_000, hookTimeout: 30_000 });
let root: string, remote: string, seed: string, current: string;
const author = { name: 'Fixture', email: 'fixture@example.invalid' };
const content = '# Preserve comments and formatting\nid: flow\ntitle: "Saved S"\n';
const binary = Buffer.from([0, 0xff, 0x80, 13, 10, 1]);
const b64 = (value: string | Buffer) => Buffer.from(value).toString('base64');
const git = (dir: string, ...args: string[]) => runGit(dir, args);
const remoteGit = (...args: string[]) => git(root, '--git-dir', remote, ...args);
const ref = (name: string) => remoteGit('rev-parse', name);
const refs = () => remoteGit('show-ref');
const input = (): SavePackCandidateInput => ({ workflowId: 'flow', sourceVersion: 'current', sourceCommit: current,
  expectedSavedHead: current, config: { remoteUrl: remote, username: author.name, email: author.email }, changes: [
    { path: 'workflows/flow.yaml', operation: 'replace', contentBase64: b64(content), mode: '100644' },
    { path: 'scripts/run.sh', operation: 'create', contentBase64: b64('#!/bin/sh\nprintf saved\n'), mode: '100755' },
    { path: 'asset.bin', operation: 'create', contentBase64: b64(binary), mode: '100644' },
  ] });
async function clone(name = 'clone') {
  const dir = path.join(root, name); mkdirSync(dir);
  await cloneClawflows(dir, input().config);
  return dir;
}
async function save() {
  return withPreparedPackCandidate(input(), async (prepared, push) => { await push(); return prepared; });
}
function newerWf() {
  git(seed, 'fetch', remote, 'refs/heads/wf/flow');
  git(seed, 'checkout', '--detach', 'FETCH_HEAD');
  writeFileSync(path.join(seed, 'workflows/flow.yaml'), 'id: flow\ntitle: S2\n');
  git(seed, 'add', '.'); git(seed, 'commit', '-m', 'S2');
  const next = git(seed, 'rev-parse', 'HEAD');
  git(seed, 'push', '--force', remote, `${next}:refs/heads/wf/flow`);
  return next;
}
beforeEach(() => {
  root = mkdtempSync(path.join(tmpdir(), 'prepared-pack-'));
  remote = createLocalRemote(root); seed = path.join(root, 'seed');
  mkdirSync(path.join(seed, 'workflows')); mkdirSync(path.join(seed, 'scripts'));
  writeFileSync(path.join(seed, 'workflow.pack.yaml'), 'id: pack\nworkflows: [flow]\n');
  writeFileSync(path.join(seed, 'workflows/flow.yaml'), 'id: flow\ntitle: Current\n');
  git(seed, 'add', '.'); git(seed, 'commit', '-m', 'current'); current = git(seed, 'rev-parse', 'HEAD');
  git(seed, 'push', remote, `${current}:refs/heads/wf/flow`);
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs(); rmSync(root, { recursive: true, force: true }); });

describe('prepared save primitives with a real bare remote', () => {
  it('reads the root Pack and preserves nested packs manifests as ordinary files', async () => {
    mkdirSync(path.join(seed, 'packs/pack/workflows'), { recursive: true });
    writeFileSync(path.join(seed, 'packs/pack/workflow.pack.yaml'), 'id: pack\nworkflows: [flow]\n');
    writeFileSync(path.join(seed, 'packs/pack/workflows/flow.yaml'), 'id: flow\ntitle: Historical\n');
    git(seed, 'add', '.'); git(seed, 'commit', '-m', 'nested resource');
    const commit = git(seed, 'rev-parse', 'HEAD');
    const metadata = await readPreparedPackMetadata(seed, 'flow', commit);
    expect(metadata).toMatchObject({ packId: 'pack', packPath: '', workflowFile: 'workflows/flow.yaml' });
    expect(metadata.content).toBe('id: flow\ntitle: Current\n');
    expect(git(seed, 'ls-tree', '-r', '--name-only', commit).split('\n')).toContain('packs/pack/workflow.pack.yaml');
  });

  it('exposes exact serializable metadata before push and touches wf only', async () => {
    const before = refs();
    let registered: PreparedPackCandidate | undefined;
    const pushSpy = vi.spyOn(gitExecution, 'executeGit');
    const result = await withPreparedPackCandidate(input(), async (prepared, push) => {
      expect(refs()).toBe(before);
      expect(prepared).toMatchObject({ workflowId: 'flow', packId: 'pack', branch: 'wf/flow', content,
        sourceVersion: 'current', sourceCommit: current, expectedSavedHead: current,
        manifestContent: 'id: pack\nworkflows: [flow]\n', workflowFile: 'workflows/flow.yaml', packPath: '' });
      expect(prepared.commit).toMatch(/^[a-f0-9]{40}$/);
      expect(push.length).toBe(0);
      registered = JSON.parse(JSON.stringify(prepared));
      expect(Buffer.from(prepared.bundleBase64, 'base64').length).toBeLessThanOrEqual(MAX_PREPARED_BUNDLE_BYTES);
      expect(pushSpy.mock.calls.some(([args]) => args[0] === 'push')).toBe(false);
      return push();
    });
    expect(result.commit).toBe(registered!.commit);
    expect(remoteGit('branch', '--list', 'saved/flow')).toBe('');
    expect(ref('refs/heads/wf/flow')).toBe(result.commit);
    expect(remoteGit('tag', '-l')).toBe('');
    expect(execFileSync('git', ['--git-dir', remote, 'show', `${result.commit}:asset.bin`])).toEqual(binary);
    expect(remoteGit('ls-tree', result.commit, 'scripts/run.sh')).toMatch(/^100755 blob/);
  });

  it('propagates a failed push even when the callback returns without awaiting it', async () => {
    const execute = gitExecution.executeGit;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      if (args[0] === 'push') throw new Error('connection lost');
      return execute(args, options);
    });
    await expect(withPreparedPackCandidate(input(), async (_prepared, push) => {
      void push().catch(() => {});
      return 'registered';
    })).rejects.toBeInstanceOf(PackCandidateOutcomeUnknownError);
  });

  it('keeps reconciliation identity when the callback throws while a push is in flight', async () => {
    const execute = gitExecution.executeGit; let prepared!: PreparedPackCandidate;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      const result = await execute(args, options);
      if (args[0] === 'push') throw new Error('lost acknowledgement');
      return result;
    });
    await expect(withPreparedPackCandidate(input(), async (p, push) => {
      prepared = p; void push().catch(() => {}); throw new Error('callback post-registration failure');
    })).rejects.toMatchObject({ name: 'PackCandidateOutcomeUnknownError', branch: 'wf/flow' });
    expect(ref('refs/heads/wf/flow')).toBe(prepared.commit);
  });

  it('does not push when durable registration fails or the callback returns without push', async () => {
    const before = refs();
    await expect(withPreparedPackCandidate(input(), async () => { throw new Error('registration failed'); }))
      .rejects.toThrow('registration failed');
    const prepared = await withPreparedPackCandidate(input(), async p => p);
    expect(prepared.content).toBe(content);
    expect(refs()).toBe(before);
  });

  it('keeps push single-use and invalidates it once the callback leaves', async () => {
    let escaped!: () => Promise<unknown>;
    await withPreparedPackCandidate(input(), async (_prepared, push) => { escaped = push; });
    await expect(escaped()).rejects.toThrow('no longer active');
    await withPreparedPackCandidate(input(), async (_prepared, push) => {
      await push();
      await expect(push()).rejects.toThrow('already attempted');
    });
  });

  it('rejects a competing wf writer without overwriting it', async () => {
    await expect(withPreparedPackCandidate(input(), async (_prepared, push) => {
      newerWf(); return push();
    })).rejects.toBeInstanceOf(PackCandidateOutcomeUnknownError);
    expect(ref('refs/heads/wf/flow')).not.toBe(current);
  });

  it('ignores a divergent saved ref and never reads or changes it', async () => {
    writeFileSync(path.join(seed, 'workflows/flow.yaml'), 'id: flow\ntitle: old saved\n');
    git(seed, 'add', '.'); git(seed, 'commit', '-m', 'unrelated old saved');
    const legacy = git(seed, 'rev-parse', 'HEAD');
    git(seed, 'push', remote, `${legacy}:refs/heads/saved/flow`);
    const execute = vi.spyOn(gitExecution, 'executeGit');
    const result = await withPreparedPackCandidate(input(), async (prepared, push) => { await push(); return prepared; });
    expect(result).toMatchObject({ sourceCommit: current, expectedSavedHead: current, branch: 'wf/flow' });
    expect(result).not.toHaveProperty('legacySavedHead');
    expect(ref('refs/heads/wf/flow')).toBe(result.commit);
    expect(ref('refs/heads/saved/flow')).toBe(legacy);
    expect(execute.mock.calls.some(([args]) => args.some(arg => arg.includes('saved/flow')))).toBe(false);
  });

  it('does not use a saved-only ref as the source of a Pack edit', async () => {
    git(seed, 'push', remote, `${current}:refs/heads/saved/flow`, ':refs/heads/wf/flow');
    const before = refs(), callback = vi.fn();
    await expect(withPreparedPackCandidate({ ...input(), sourceVersion: 'draft', expectedSavedHead: null }, callback))
      .rejects.toBeInstanceOf(PackCandidateConflictError);
    expect(callback).not.toHaveBeenCalled(); expect(refs()).toBe(before);
  });

  it('creates an empty new Pack when only saved and default HEAD exist', async () => {
    git(seed, 'push', remote, `${current}:refs/heads/saved/flow`, ':refs/heads/wf/flow');
    const execute = vi.spyOn(gitExecution, 'executeGit');
    const spec = { id: 'flow', title: 'First wf', nodes: [] };
    const prepared = await withPreparedWorkflowSpec({ workflowId: 'flow', packId: 'pack', spec,
      config: input().config, author, createOnly: true, expectedSavedHead: null }, async (candidate, push) => {
      await push(); return candidate;
    });
    expect(prepared.sourceCommit).toBeNull();
    expect(ref('refs/heads/wf/flow')).toBe(prepared.commit);
    expect(ref('refs/heads/saved/flow')).toBe(current);
    expect(remoteGit('show', '-s', '--format=%P', prepared.commit)).toBe('');
    expect(remoteGit('ls-tree', '-r', '--name-only', prepared.commit).split('\n').sort())
      .toEqual(['packs/pack/workflow.pack.yaml', 'packs/pack/workflows/flow.yaml']);
    expect(execute.mock.calls.some(([args]) => args.some(arg => arg.includes('saved/flow')))).toBe(false);
  });

  it('saves a nested wf Pack even when a different saved ref exists', async () => {
    mkdirSync(path.join(seed, 'packs/pack'), { recursive: true });
    git(seed, 'mv', 'workflow.pack.yaml', 'workflows', 'packs/pack/');
    git(seed, 'add', '.'); git(seed, 'commit', '-m', 'nested');
    const nested = git(seed, 'rev-parse', 'HEAD');
    git(seed, 'push', remote, `${nested}:refs/heads/wf/flow`, `${current}:refs/heads/saved/flow`);
    current = nested;
    const result = await withPreparedPackCandidate(input(), async (prepared, push) => { await push(); return prepared; });
    expect(result.packPath).toBe('packs/pack/');
    expect(ref('refs/heads/wf/flow')).toBe(result.commit);
    expect(ref('refs/heads/saved/flow')).not.toBe(result.commit);
  });

  it('does not push when the real save service cannot recheck its DB row', async () => {
    const db = createAutoHealConfigTestDb();
    await db.exec('CREATE TABLE workflow_deploy_history (workflow_id TEXT, version INTEGER)');
    const execute = vi.spyOn(gitExecution, 'executeGit'); const before = refs();
    try {
      await expect(savePackVersionToRemote({ ...input(), db, gitConfig: input().config, gitAuthor: author,
        actorId: 'editor', requestId: 'db-failure-001', changes: [
          { path: 'asset.bin', operation: 'create', contentBase64: b64(binary), mode: '100644' },
        ] })).rejects.toThrow(/workflow_specs/);
      expect(execute.mock.calls.some(([args]) => args[0] === 'commit-tree')).toBe(true);
      expect(execute.mock.calls.some(([args]) => args[0] === 'push')).toBe(false);
      expect(refs()).toBe(before);
    } finally { await db.close(); }
  });

  it('tags the exact saved candidate and rejects stale source before editing wf', async () => {
    const saved = await save(); const dir = await clone();
    const release = await prepareDeployCandidate(dir, 'flow', saved.commit, '', 1, 1, author);
    await pushPreparedDeployment(dir, release);
    expect(release.releaseCommit).toBe(saved.commit);
    expect(ref('refs/heads/wf/flow')).toBe(saved.commit);
    const changes: SavePackCandidateInput['changes'] = [{ path: 'workflows/flow.yaml', operation: 'replace',
      contentBase64: b64('id: flow\ntitle: Edited after deploy\n'), mode: '100644' }];
    await expect(withPreparedPackCandidate({ ...input(), sourceCommit: current,
      expectedSavedHead: saved.commit, changes }, async (_p, push) => push())).rejects.toBeInstanceOf(PackCandidateConflictError);
    const edited = await withPreparedPackCandidate({ ...input(), sourceVersion: 'draft', sourceCommit: saved.commit,
      expectedSavedHead: saved.commit, changes }, async (p, push) => { await push(); return p; });
    expect(ref('refs/heads/wf/flow')).toBe(edited.commit);
    expect(ref('refs/tags/deploy/flow/#1^{commit}')).toBe(saved.commit);
    expect(remoteGit('show', '-s', '--format=%P', edited.commit)).toBe(saved.commit);
    expect(remoteGit('show', `${edited.commit}:workflows/flow.yaml`)).toBe('id: flow\ntitle: Edited after deploy');
    expect(execFileSync('git', ['--git-dir', remote, 'show', `${edited.commit}:asset.bin`])).toEqual(binary);
  });

  it('returns VERSION_CONFLICT through the real save service and HTTP route without replacing wf', async () => {
    const saved = await save();
    const db = createAutoHealConfigTestDb();
    await db.exec('CREATE TABLE workflow_deploy_history (workflow_id TEXT, version INTEGER)');
    const app = express(); app.use(express.json()); app.use(createPackVersionRouter({
      resolvePrincipal: async () => ({ actorId: 'trusted' }),
      botPermRepo: { hasEditPermission: async () => true },
      workflowSpecRepo: { findByWorkflowId: async () => ({ workflow_id: 'flow', pack_id: 'pack' }) },
      readSnapshot: async () => { throw new Error('Unexpected read'); },
      saveCandidate: candidate => savePackVersionToRemote({ ...candidate, db, gitConfig: input().config, gitAuthor: author }),
    }));
    const server = app.listen(0, '127.0.0.1');
    try {
      await once(server, 'listening');
      const address = server.address();
      if (!address || typeof address === 'string') throw new Error('Missing local address');
      const response = await fetch(`http://127.0.0.1:${address.port}/flow/pack-version`, {
        method: 'PUT', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ sourceVersion: 'current', sourceCommit: current, expectedSavedHead: saved.commit,
          requestId: 'conflict-request-01', changes: [{ path: 'asset.bin', operation: 'replace', contentBase64: b64('changed'), mode: '100644' }] }),
      });
      expect(response.status).toBe(409);
      expect(response.headers.get('cache-control')).toBe('no-store');
      expect(await response.json()).toEqual({ error: 'Workflow operation could not be completed', code: 'VERSION_CONFLICT', requestId: 'conflict-request-01' });
      expect(ref('refs/heads/wf/flow')).toBe(saved.commit);
      expect(execFileSync('git', ['--git-dir', remote, 'show', `${saved.commit}:asset.bin`])).toEqual(binary);
    } finally {
      await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
      await db.close();
    }
  });

  it('recovers the exact unpublished commit from JSON plus a self-contained bundle', async () => {
    const prepared = JSON.parse(JSON.stringify(await withPreparedPackCandidate(input(), async p => p)));
    const recovered = path.join(root, 'empty'); mkdirSync(recovered); git(recovered, 'init');
    git(recovered, 'remote', 'add', 'origin', remote);
    await restorePreparedGitBundle(recovered, prepared.bundleBase64);
    expect(git(recovered, 'show', `${prepared.commit}:workflows/flow.yaml`)).toBe(content.trim());
    const result = await pushPreparedPackCandidate(recovered, prepared);
    expect(result.commit).toBe(prepared.commit);
    expect(ref('refs/heads/wf/flow')).toBe(prepared.commit);
    expect(remoteGit('branch', '--list', 'saved/flow')).toBe('');
  });

  it('reports lost acknowledgement as unknown, never queries refs or retries after the push', async () => {
    const execute = gitExecution.executeGit;
    let attempted = false, pushes = 0;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      if (attempted && args[0] === 'ls-remote') throw new Error('must not infer success by rereading');
      const result = await execute(args, options);
      if (args[0] === 'push') { attempted = true; pushes++; throw new Error('lost acknowledgement'); }
      return result;
    });
    let recorded!: PreparedPackCandidate;
    await expect(withPreparedPackCandidate(input(), async (prepared, push) => { recorded = prepared; return push(); }))
      .rejects.toMatchObject({ name: 'PackCandidateOutcomeUnknownError', branch: 'wf/flow' });
    expect(pushes).toBe(1);
    expect(ref('refs/heads/wf/flow')).toBe(recorded.commit);
    expect(remoteGit('branch', '--list', 'saved/flow')).toBe('');
  });
});

describe('prepared deployment primitives with a real bare remote', () => {
  it('reads S by commit after wf advances, prepares without push, then tags precisely S', async () => {
    const saved = await save();
    const next = newerWf();
    const dir = await clone();
    const inspected = await inspectDeployCandidate(dir, 'flow', 'pack', saved.commit);
    expect(inspected.content).toBe(content);
    expect(inspected.previousCommit).toBe('');
    const before = refs();
    const prepared = await prepareDeployCandidate(dir, 'flow', saved.commit, current, 1, 3, author);
    expect(refs()).toBe(before);
    expect(prepared).toMatchObject({ branch: 'wf/flow', candidateCommit: saved.commit, previousCommit: current,
      tagName: 'deploy/flow/#1', deployNumber: 1, version: 3, commit: prepared.releaseCommit,
      packId: 'pack', manifestContent: 'id: pack\nworkflows: [flow]\n', workflowFile: 'workflows/flow.yaml', content });
    expect(git(dir, 'rev-parse', `${prepared.releaseCommit}^{tree}`)).toBe(git(dir, 'rev-parse', `${saved.commit}^{tree}`));
    expect(git(dir, 'cat-file', '-p', prepared.tagObject)).toContain(`candidateCommit=${saved.commit}`);
    const result = await pushPreparedDeployment(dir, prepared);
    expect(result.commit).toBe(prepared.releaseCommit);
    expect(ref('refs/tags/deploy/flow/#1')).toBe(prepared.tagObject);
    expect(ref('refs/heads/wf/flow')).toBe(next);
    expect(prepared.releaseCommit).toBe(saved.commit);
    expect(execFileSync('git', ['--git-dir', remote, 'show', `${result.commit}:asset.bin`])).toEqual(binary);
    expect(remoteGit('ls-tree', result.commit, 'scripts/run.sh')).toMatch(/^100755 blob/);
  });

  it('does not consult saved at push time even after force replacement or deletion', async () => {
    const saved = await save(); const dir = await clone();
    const prepared = await prepareDeployCandidate(dir, 'flow', saved.commit, current, 1, 1, author);
    git(seed, 'push', remote, `${current}:refs/heads/saved/flow`);
    git(seed, 'push', remote, ':refs/heads/saved/flow');
    const spy = vi.spyOn(gitExecution, 'executeGit');
    await pushPreparedDeployment(dir, prepared);
    expect(spy.mock.calls.some(([args]) => args.join(' ').includes('saved/flow'))).toBe(false);
    expect(ref('refs/heads/wf/flow')).toBe(saved.commit);
  });

  it.each(['advanced', 'deleted', 'created'])('publishes only a tag when wf was %s after prepare', async state => {
    const saved = await save(); const dir = await clone();
    if (state === 'created') git(seed, 'push', remote, ':refs/heads/wf/flow');
    const prepared = await prepareDeployCandidate(dir, 'flow', saved.commit, current, 1, 1, author);
    if (state === 'deleted') git(seed, 'push', remote, ':refs/heads/wf/flow');
    else if (state === 'created') git(dir, 'push', 'origin', `${saved.commit}:refs/heads/wf/flow`);
    else newerWf();
    const wfBefore = remoteGit('branch', '--list', 'wf/flow') ? ref('refs/heads/wf/flow') : null;
    await pushPreparedDeployment(dir, prepared);
    const wfAfter = remoteGit('branch', '--list', 'wf/flow') ? ref('refs/heads/wf/flow') : null;
    expect(wfAfter).toBe(wfBefore);
    expect(ref(`refs/tags/${prepared.tagName}^{commit}`)).toBe(saved.commit);
  });

  it('does not create wf and never overwrites a concurrently created tag', async () => {
    const saved = await save(); const dir = await clone();
    git(seed, 'push', remote, ':refs/heads/wf/flow');
    const first = await prepareDeployCandidate(dir, 'flow', saved.commit, '', 1, 1, author);
    await pushPreparedDeployment(dir, first);
    expect(remoteGit('branch', '--list', 'wf/flow')).toBe('');
    const second = await prepareDeployCandidate(dir, 'flow', saved.commit, first.releaseCommit, 2, 2, author);
    git(seed, 'tag', '-a', second.tagName, '-m', 'another writer', current);
    git(seed, 'push', remote, `refs/tags/${second.tagName}`);
    const before = refs();
    await expect(pushPreparedDeployment(dir, second)).rejects.toBeInstanceOf(DeploymentOutcomeUnknownError);
    expect(refs()).toBe(before);
  });

  it('pushes the prepared tag object rather than a mutable local tag name', async () => {
    const saved = await save(); const dir = await clone();
    const prepared = await prepareDeployCandidate(dir, 'flow', saved.commit, current, 1, 1, author);
    git(dir, 'tag', '-f', prepared.tagName, current);
    await pushPreparedDeployment(dir, prepared);
    expect(ref(`refs/tags/${prepared.tagName}`)).toBe(prepared.tagObject);
    expect(ref(`refs/tags/${prepared.tagName}^{commit}`)).toBe(prepared.releaseCommit);
  });

  it('restores exact release and annotated tag objects after the preparation clone is removed', async () => {
    const saved = await save(); const dir = await clone();
    const prepared = JSON.parse(JSON.stringify(await prepareDeployCandidate(dir, 'flow', saved.commit, current, 1, 1, author)));
    rmSync(dir, { recursive: true, force: true });
    const recovered = path.join(root, 'recovery'); mkdirSync(recovered); git(recovered, 'init');
    git(recovered, 'remote', 'add', 'origin', remote);
    await restorePreparedGitBundle(recovered, prepared.bundleBase64);
    await pushPreparedDeployment(recovered, prepared);
    expect(ref(`refs/tags/${prepared.tagName}`)).toBe(prepared.tagObject);
    expect(ref('refs/heads/wf/flow')).toBe(saved.commit);
  });

  it('retains the exact reconciliation identities after a successful tag push loses its acknowledgement', async () => {
    const saved = await save(); const dir = await clone();
    const prepared = await prepareDeployCandidate(dir, 'flow', saved.commit, current, 1, 1, author);
    const execute = gitExecution.executeGit; let pushes = 0;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      if (pushes && args[0] === 'ls-remote') throw new Error('no ref reread after unknown outcome');
      const result = await execute(args, options);
      if (args[0] === 'push') { pushes++; throw new Error('acknowledgement lost'); }
      return result;
    });
    await expect(pushPreparedDeployment(dir, prepared)).rejects.toMatchObject({ name: 'DeploymentOutcomeUnknownError',
      branch: prepared.branch, tagName: prepared.tagName, commit: prepared.releaseCommit, tagObject: prepared.tagObject });
    expect(pushes).toBe(1);
    expect(ref('refs/heads/wf/flow')).toBe(saved.commit);
    expect(ref(`refs/tags/${prepared.tagName}`)).toBe(prepared.tagObject);
  });
});


describe('exact prepared replay and recovery', () => {
  it('resumes an unpublished save once, then recognizes exact desired without another push', async () => {
    const prepared = JSON.parse(JSON.stringify(await withPreparedPackCandidate(input(), async p => p)));
    const execute = vi.spyOn(gitExecution, 'executeGit');
    expect(await resumePreparedPackCandidate(prepared, input().config)).toEqual({ branch: prepared.branch, commit: prepared.commit });
    expect(await resumePreparedPackCandidate(prepared, input().config)).toEqual({ branch: prepared.branch, commit: prepared.commit });
    expect(execute.mock.calls.filter(([args]) => args[0] === 'push')).toHaveLength(1);
    expect(ref('refs/heads/wf/flow')).toBe(prepared.commit);
  });

  it('never overwrites a newer wf head when resuming an old request', async () => {
    const prepared = await withPreparedPackCandidate(input(), async p => p);
    const next = newerWf();
    const execute = vi.spyOn(gitExecution, 'executeGit');
    await expect(resumePreparedPackCandidate(prepared, input().config)).rejects.toBeInstanceOf(PackCandidateConflictError);
    expect(ref('refs/heads/wf/flow')).toBe(next);
    expect(execute.mock.calls.filter(([args]) => args[0] === 'push')).toHaveLength(0);
  });

  it('resumes exactly the original release/tag, retaining version/deploy number and ignoring newer wf', async () => {
    const saved = await save(); const dir = await clone();
    const prepared = JSON.parse(JSON.stringify(await prepareDeployCandidate(dir, 'flow', saved.commit, current, 7, 4, author)));
    rmSync(dir, { recursive: true, force: true });
    const next = newerWf();
    const execute = vi.spyOn(gitExecution, 'executeGit');
    expect(await resumePreparedDeployment(prepared, input().config)).toMatchObject({ commit: prepared.releaseCommit, deployNumber: 7 });
    expect(await resumePreparedDeployment(prepared, input().config)).toMatchObject({ commit: prepared.releaseCommit, tagName: 'deploy/flow/#7' });
    expect(execute.mock.calls.filter(([args]) => args[0] === 'push')).toHaveLength(1);
    expect(ref('refs/heads/wf/flow')).toBe(next);
    expect(ref('refs/tags/deploy/flow/#7')).toBe(prepared.tagObject);
    expect(remoteGit('tag', '-l')).toBe('deploy/flow/#7');
  });

  it.each(['tag only', 'different tag', 'newer branch'])('reconciles deployment tag state: %s', async state => {
    const saved = await save(); const dir = await clone();
    const prepared = await prepareDeployCandidate(dir, 'flow', saved.commit, current, 1, 1, author);
    if (state === 'tag only') git(dir, 'push', 'origin', `${prepared.tagObject}:refs/tags/${prepared.tagName}`);
    if (state === 'different tag') git(dir, 'push', 'origin', `${current}:refs/tags/${prepared.tagName}`);
    if (state === 'newer branch') newerWf();
    const before = refs(); const execute = vi.spyOn(gitExecution, 'executeGit');
    if (state === 'different tag') {
      await expect(resumePreparedDeployment(prepared, input().config)).rejects.toBeInstanceOf(DeploymentConflictError);
      expect(refs()).toBe(before);
    } else {
      await expect(resumePreparedDeployment(prepared, input().config)).resolves.toMatchObject({ commit: saved.commit });
      expect(ref(`refs/tags/${prepared.tagName}^{commit}`)).toBe(saved.commit);
      if (state === 'tag only') expect(refs()).toBe(before);
    }
    expect(execute.mock.calls.filter(([args]) => args[0] === 'push')).toHaveLength(state === 'newer branch' ? 1 : 0);
  });

  it('allows two concurrent resumptions of the same save to converge without new objects', async () => {
    const prepared = await withPreparedPackCandidate(input(), async p => p);
    const outcomes = await Promise.allSettled([resumePreparedPackCandidate(prepared, input().config),
      resumePreparedPackCandidate(prepared, input().config)]);
    expect(outcomes.some(value => value.status === 'fulfilled')).toBe(true);
    // A losing receive-pack may report either an ambiguous lock rejection or a
    // pre-push lease conflict; reconciliation still converges, and no caller can
    // publish a different candidate identity.
    for (const value of outcomes) if (value.status === 'rejected') {
      expect([PackCandidateOutcomeUnknownError, PackCandidateConflictError]
        .some(errorType => value.reason instanceof errorType)).toBe(true);
    }
    expect(ref('refs/heads/wf/flow')).toBe(prepared.commit);
    expect((await resumePreparedPackCandidate(prepared, input().config)).commit).toBe(prepared.commit);
  });

  it('allows two concurrent resumptions of one deployment without allocating another release', async () => {
    const saved = await save(); const dir = await clone();
    const prepared = await prepareDeployCandidate(dir, 'flow', saved.commit, current, 1, 1, author);
    const outcomes = await Promise.allSettled([resumePreparedDeployment(prepared, input().config),
      resumePreparedDeployment(prepared, input().config)]);
    expect(outcomes.some(value => value.status === 'fulfilled')).toBe(true);
    for (const value of outcomes) if (value.status === 'rejected') expect(value.reason).toBeInstanceOf(DeploymentOutcomeUnknownError);
    expect((await resumePreparedDeployment(prepared, input().config)).commit).toBe(prepared.releaseCommit);
    expect(remoteGit('tag', '-l')).toBe('deploy/flow/#1');
  });

  it('fails closed on malformed, oversized or incremental bundles without touching refs', async () => {
    const dir = await clone(); const before = refs();
    await expect(restorePreparedGitBundle(dir, 'not base64')).rejects.toThrow('encoding');
    await expect(restorePreparedGitBundle(dir, 'x'.repeat(Math.ceil(MAX_PREPARED_BUNDLE_BYTES / 3) * 4 + 4))).rejects.toThrow('size');
    const incremental = b64(`# v2 git bundle\n-${current} prerequisite\n${current} refs/heads/base\n\nPACK`);
    await expect(restorePreparedGitBundle(dir, incremental)).rejects.toThrow('self-contained');
    expect(refs()).toBe(before);
  });
});


describe('prepared object validation boundaries', () => {
  it('rejects full-history bundles exceeding the limit before callback registration', async () => {
    mkdirSync(path.join(seed, 'packs/pack'), { recursive: true });
    git(seed, 'mv', 'workflow.pack.yaml', 'workflows', 'packs/pack/');
    writeFileSync(path.join(seed, 'large-unrelated-history.bin'), randomBytes(MAX_PREPARED_BUNDLE_BYTES + 1024 * 1024));
    git(seed, 'add', '.'); git(seed, 'commit', '-m', 'large unrelated history'); current = git(seed, 'rev-parse', 'HEAD');
    git(seed, 'push', remote, `${current}:refs/heads/wf/flow`);
    const callback = vi.fn(async (_prepared, push) => push());
    const before = refs();
    await expect(withPreparedPackCandidate(input(), callback)).rejects.toMatchObject({ code: 'GIT_OUTPUT_LIMIT' });
    expect(callback).not.toHaveBeenCalled(); expect(refs()).toBe(before);
  }, 60_000);

  it('validates prepared identities and object relationships before any push', async () => {
    const saved = await save(); const dir = await clone();
    await expect(pushPreparedPackCandidate(dir, { ...saved, branch: 'saved/flow' })).rejects.toThrow(/invalid|read.only/i);
    await expect(pushPreparedPackCandidate(dir, { ...saved, sourceCommit: saved.commit, sourceVersion: 'draft', expectedSavedHead: saved.commit }))
      .rejects.toThrow('Invalid prepared candidate commit');
    const prepared = await prepareDeployCandidate(dir, 'flow', saved.commit, current, 1, 1, author);
    const before = refs();
    await expect(pushPreparedDeployment(dir, { ...prepared, version: 2 })).rejects.toThrow('annotation');
    await expect(pushPreparedDeployment(dir, { ...prepared, candidateCommit: current })).rejects.toThrow('Invalid prepared deployment');
    await expect(pushPreparedDeployment(dir, { ...prepared, tagObject: saved.commit })).rejects.toThrow('objects');
    await expect(pushPreparedDeployment(dir, { ...prepared, commit: current })).rejects.toThrow('Invalid prepared deployment');
    await expect(prepareDeployCandidate(dir, 'flow', saved.commit, current, 0, 1, author)).rejects.toThrow('identity');
    await expect(inspectDeployCandidate(dir, 'flow', 'wrong-pack', saved.commit)).rejects.toThrow('pack identity');
    expect(refs()).toBe(before);
  });

  it('preserves raw YAML BOM/comments and discovers a nonstandard declared path in a nested pack', async () => {
    mkdirSync(path.join(seed, 'packs/pack'), { recursive: true });
    git(seed, 'mv', 'workflow.pack.yaml', 'workflows', 'packs/pack/');
    git(seed, 'mv', 'packs/pack/workflows/flow.yaml', 'packs/pack/flow-spec.yaml');
    const manifest = '# original comment\nid: pack\nworkflows: [{id: flow, file: flow-spec.yaml}]\n';
    writeFileSync(path.join(seed, 'packs/pack/workflow.pack.yaml'), manifest);
    git(seed, 'add', '.'); git(seed, 'commit', '-m', 'nested layout'); current = git(seed, 'rev-parse', 'HEAD');
    git(seed, 'push', remote, `${current}:refs/heads/wf/flow`);
    const raw = '\ufeff# BOM preserved\nid: flow\n';
    const prepared = await withPreparedPackCandidate({ ...input(), changes: [
      { path: 'flow-spec.yaml', operation: 'replace', contentBase64: b64(raw), mode: '100755' },
    ] }, async p => p);
    expect(prepared).toMatchObject({ packPath: 'packs/pack/', packId: 'pack', workflowFile: 'flow-spec.yaml', manifestContent: manifest, content: raw });
  });

  it('rejects shallow sources rather than calling an incomplete history self-contained', async () => {
    const dir = path.join(root, 'shallow');
    git(root, 'clone', '--depth=1', `file://${seed}`, dir);
    await expect(createPreparedGitBundle(dir, [current])).rejects.toThrow('shallow');
  });

  it('rejects ancestor Pack overlap before deployment', async () => {
    mkdirSync(path.join(seed, 'packs/pack'), { recursive: true });
    git(seed, 'mv', 'workflow.pack.yaml', 'workflows', 'packs/pack/');
    writeFileSync(path.join(seed, 'packs/workflow.pack.yaml'), 'id: packs\nworkflows: [parent]\n');
    mkdirSync(path.join(seed, 'packs/workflows'));
    writeFileSync(path.join(seed, 'packs/workflows/parent.yaml'), 'id: parent\n');
    git(seed, 'add', '.'); git(seed, 'commit', '-m', 'ancestor overlap'); current = git(seed, 'rev-parse', 'HEAD');
    git(seed, 'push', remote, `${current}:refs/heads/wf/flow`);
    const dir = await clone();
    await expect(inspectDeployCandidate(dir, 'flow', 'pack', current)).rejects.toThrow('Overlapping Pack layout');
  });

  it('rejects metadata mixed with another prepared record before push or successful reconciliation', async () => {
    const saved = await save(); const dir = await clone();
    await expect(pushPreparedPackCandidate(dir, { ...saved, content: 'id: flow\ntitle: forged\n' })).rejects.toThrow('metadata');
    await expect(resumePreparedPackCandidate({ ...saved, packId: 'different' }, input().config)).rejects.toThrow('metadata');
    await expect(resumePreparedPackCandidate({ ...saved, sourceVersion: 'draft', sourceCommit: saved.commit }, input().config))
      .rejects.toThrow(/commit|parent/i);
    const prepared = await prepareDeployCandidate(dir, 'flow', saved.commit, current, 1, 1, author);
    await expect(pushPreparedDeployment(dir, { ...prepared, workflowFile: 'different.yaml' })).rejects.toThrow('metadata');
    await pushPreparedDeployment(dir, prepared);
    await expect(resumePreparedDeployment({ ...prepared, manifestContent: 'id: other\n' }, input().config)).rejects.toThrow('metadata');
  });

  it('does not accept arbitrary or prerequisite-bearing data as a recoverable bundle', async () => {
    const dir = await clone();
    await expect(createPreparedGitBundle(dir, [])).rejects.toThrow('Invalid prepared objects');
    await expect(createPreparedGitBundle(dir, ['HEAD'])).rejects.toThrow('Invalid prepared objects');
    await expect(restorePreparedGitBundle(dir, b64('not a bundle'))).rejects.toThrow('self-contained');
    await expect(restorePreparedGitBundle(dir, b64(`# v2 git bundle\n${current} refs/heads/base\n\nPACK broken`))).rejects.toThrow();
  });

describe('configured temporary parent for prepared objects', () => {
  const configuredParent = () => {
    const parent = path.join(root, 'configured-temp'); mkdirSync(parent);
    vi.stubEnv('CLAWWEB_TEMP_DIR', parent);
    return parent;
  };

  it('creates deploy-tag staging under the configured parent', async () => {
    const parent = configuredParent();
    const saved = await save(); const dir = await clone();
    let tagFile = '';
    const execute = gitExecution.executeGit;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      if (args[0] === 'hash-object' && args.includes('-w')) tagFile = String(args.at(-1));
      return execute(args, options);
    });
    await prepareDeployCandidate(dir, 'flow', saved.commit, current, 1, 2, author);
    expect(tagFile.startsWith(path.join(parent, 'cw-prepared-tag-'))).toBe(true);
    expect(readdirSync(parent)).toEqual([]);
  });

  it('fails deploy-tag preparation when the configured parent is missing', async () => {
    const parent = configuredParent();
    const saved = await save(); const dir = await clone();
    vi.stubEnv('CLAWWEB_TEMP_DIR', path.join(parent, 'missing'));
    await expect(prepareDeployCandidate(dir, 'flow', saved.commit, current, 1, 2, author))
      .rejects.toThrow('临时目录不可用或配置无效');
  });

  it('creates bundle staging under the configured parent', async () => {
    const parent = configuredParent();
    const prepared = await withPreparedPackCandidate(input(), async candidate => candidate);
    const recovered = path.join(root, 'empty-bundle'); mkdirSync(recovered); git(recovered, 'init');
    git(recovered, 'remote', 'add', 'origin', remote);
    let bundleFile = '';
    const execute = gitExecution.executeGit;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      if (args[0] === 'bundle' && args[1] === 'verify') bundleFile = String(args.at(-1));
      return execute(args, options);
    });
    await restorePreparedGitBundle(recovered, prepared.bundleBase64);
    expect(path.dirname(bundleFile).startsWith(path.join(parent, 'cw-prepared-bundle-'))).toBe(true);
    expect(readdirSync(parent)).toEqual([]);
  });

  it('fails bundle restoration when the configured parent is missing', async () => {
    const parent = configuredParent();
    const prepared = await withPreparedPackCandidate(input(), async candidate => candidate);
    const recovered = path.join(root, 'missing-bundle-parent'); mkdirSync(recovered); git(recovered, 'init');
    git(recovered, 'remote', 'add', 'origin', remote);
    vi.stubEnv('CLAWWEB_TEMP_DIR', path.join(parent, 'missing'));
    await expect(restorePreparedGitBundle(recovered, prepared.bundleBase64))
      .rejects.toThrow('临时目录不可用或配置无效');
  });
});

});
