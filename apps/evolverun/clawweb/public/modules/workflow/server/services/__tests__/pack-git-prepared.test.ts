// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import * as gitExecution from '@avernet/clawweb-shared/server/services/git-execution';
import { createLocalRemote, runGit } from './clawflows-git-client-fixture.js';
import { cloneClawflows } from '../clawflows-git-client.js';
import {
  withPreparedPackCandidate, pushPreparedPackCandidate, resumePreparedPackCandidate, PackCandidateOutcomeUnknownError, PackCandidateConflictError,
  type SavePackCandidateInput, type PreparedPackCandidate,
} from '../pack-candidate-save.js';
import {
  inspectDeployCandidate, prepareDeployCandidate, pushPreparedDeployment, resumePreparedDeployment, DeploymentOutcomeUnknownError, DeploymentConflictError,
} from '../auto-heal-deploy-git.js';
import { restorePreparedGitBundle, createPreparedGitBundle, MAX_PREPARED_BUNDLE_BYTES } from '../pack-git-prepared.js';

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
  expectedSavedHead: null, config: { remoteUrl: remote, username: author.name, email: author.email }, changes: [
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
function newerSaved() {
  writeFileSync(path.join(seed, 'workflows/flow.yaml'), 'id: flow\ntitle: S2\n');
  git(seed, 'add', '.'); git(seed, 'commit', '-m', 'S2');
  const next = git(seed, 'rev-parse', 'HEAD');
  git(seed, 'push', '--force', remote, `${next}:refs/heads/saved/flow`);
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
afterEach(() => { vi.restoreAllMocks(); rmSync(root, { recursive: true, force: true }); });

describe('prepared save primitives with a real bare remote', () => {
  it('exposes exact serializable metadata before push and touches saved only', async () => {
    const before = refs();
    let registered: PreparedPackCandidate | undefined;
    const pushSpy = vi.spyOn(gitExecution, 'executeGit');
    const result = await withPreparedPackCandidate(input(), async (prepared, push) => {
      expect(refs()).toBe(before);
      expect(prepared).toMatchObject({ workflowId: 'flow', packId: 'pack', branch: 'saved/flow', content,
        sourceVersion: 'current', sourceCommit: current, expectedSavedHead: null,
        manifestContent: 'id: pack\nworkflows: [flow]\n', workflowFile: 'workflows/flow.yaml', packPath: '' });
      expect(prepared.commit).toMatch(/^[a-f0-9]{40}$/);
      expect(push.length).toBe(0);
      registered = JSON.parse(JSON.stringify(prepared));
      expect(Buffer.from(prepared.bundleBase64, 'base64').length).toBeLessThanOrEqual(MAX_PREPARED_BUNDLE_BYTES);
      expect(pushSpy.mock.calls.some(([args]) => args[0] === 'push')).toBe(false);
      return push();
    });
    expect(result.commit).toBe(registered!.commit);
    expect(ref('refs/heads/saved/flow')).toBe(result.commit);
    expect(ref('refs/heads/wf/flow')).toBe(current);
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
    })).rejects.toMatchObject({ name: 'PackCandidateOutcomeUnknownError', branch: 'saved/flow' });
    expect(ref('refs/heads/saved/flow')).toBe(prepared.commit);
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

  it('CAS rejects a competing saved writer but does not CAS wf', async () => {
    let next = '';
    await expect(withPreparedPackCandidate(input(), async (_prepared, push) => {
      next = newerSaved();
      return push();
    })).rejects.toBeInstanceOf(PackCandidateOutcomeUnknownError);
    expect(ref('refs/heads/saved/flow')).toBe(next);
    expect(ref('refs/heads/wf/flow')).toBe(current);
    git(seed, 'push', remote, ':refs/heads/saved/flow');
    const result = await withPreparedPackCandidate(input(), async (_prepared, push) => {
      git(seed, 'push', remote, `${next}:refs/heads/wf/flow`);
      return push();
    });
    expect(ref('refs/heads/saved/flow')).toBe(result.commit);
    expect(ref('refs/heads/wf/flow')).toBe(next);
  });

  it('saves S, deploys release R with the same tree, then edits current R over observed saved S', async () => {
    const saved = await save(); const dir = await clone();
    const release = await prepareDeployCandidate(dir, 'flow', saved.commit, current, 1, 1, author);
    await pushPreparedDeployment(dir, release);
    expect(release.releaseCommit).not.toBe(saved.commit);
    expect(ref(`${release.releaseCommit}^{tree}`)).toBe(ref(`${saved.commit}^{tree}`));
    const edited = await withPreparedPackCandidate({ ...input(), sourceCommit: release.releaseCommit,
      expectedSavedHead: saved.commit, changes: [{ path: 'workflows/flow.yaml', operation: 'replace',
        contentBase64: b64('id: flow\ntitle: Current edited after deploy\n'), mode: '100644' }] }, async (p, push) => {
      expect(p.sourceCommit).toBe(release.releaseCommit); expect(p.expectedSavedHead).toBe(saved.commit);
      await push(); return p;
    });
    expect(ref('refs/heads/saved/flow')).toBe(edited.commit);
    expect(ref('refs/heads/wf/flow')).toBe(release.releaseCommit);
    expect(ref('refs/tags/deploy/flow/#1')).toBe(release.tagObject);
    expect(remoteGit('show', '-s', '--format=%P', edited.commit)).toBe(release.releaseCommit);
  });

  it('recovers the exact unpublished commit from JSON plus a self-contained bundle', async () => {
    const prepared = JSON.parse(JSON.stringify(await withPreparedPackCandidate(input(), async p => p)));
    const recovered = path.join(root, 'empty'); mkdirSync(recovered); git(recovered, 'init');
    git(recovered, 'remote', 'add', 'origin', remote);
    await restorePreparedGitBundle(recovered, prepared.bundleBase64);
    expect(git(recovered, 'show', `${prepared.commit}:workflows/flow.yaml`)).toBe(content.trim());
    const result = await pushPreparedPackCandidate(recovered, prepared);
    expect(result.commit).toBe(prepared.commit);
    expect(ref('refs/heads/saved/flow')).toBe(prepared.commit);
    expect(ref('refs/heads/wf/flow')).toBe(current);
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
      .rejects.toMatchObject({ name: 'PackCandidateOutcomeUnknownError', branch: 'saved/flow' });
    expect(pushes).toBe(1);
    expect(ref('refs/heads/saved/flow')).toBe(recorded.commit);
    expect(ref('refs/heads/wf/flow')).toBe(current);
  });
});

describe('prepared deployment primitives with a real bare remote', () => {
  it('reads S by commit after saved advances, prepares without push, then deploys precisely S', async () => {
    const saved = await save();
    const next = newerSaved();
    const dir = await clone();
    const inspected = await inspectDeployCandidate(dir, 'flow', 'pack', saved.commit);
    expect(inspected.content).toBe(content);
    expect(inspected.previousCommit).toBe(current);
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
    expect(ref('refs/heads/wf/flow')).toBe(prepared.releaseCommit);
    expect(ref('refs/tags/deploy/flow/#1')).toBe(prepared.tagObject);
    expect(ref('refs/heads/saved/flow')).toBe(next);
    expect(execFileSync('git', ['--git-dir', remote, 'show', `${result.commit}:asset.bin`])).toEqual(binary);
    expect(remoteGit('ls-tree', result.commit, 'scripts/run.sh')).toMatch(/^100755 blob/);
  });

  it('does not consult saved at push time even after force replacement or deletion', async () => {
    const saved = await save(); const dir = await clone();
    const prepared = await prepareDeployCandidate(dir, 'flow', saved.commit, current, 1, 1, author);
    git(seed, 'push', remote, ':refs/heads/saved/flow');
    const spy = vi.spyOn(gitExecution, 'executeGit');
    await pushPreparedDeployment(dir, prepared);
    expect(spy.mock.calls.some(([args]) => args.join(' ').includes('saved/flow'))).toBe(false);
    expect(ref('refs/heads/wf/flow')).toBe(prepared.releaseCommit);
  });

  it.each(['advanced', 'deleted', 'created'])('keeps atomic wf CAS when wf was %s after prepare', async state => {
    const saved = await save(); const dir = await clone();
    const expected = state === 'created' ? '' : current;
    if (state === 'created') git(seed, 'push', remote, ':refs/heads/wf/flow');
    const prepared = await prepareDeployCandidate(dir, 'flow', saved.commit, expected, 1, 1, author);
    if (state === 'deleted') git(seed, 'push', remote, ':refs/heads/wf/flow');
    else git(dir, 'push', '--force', 'origin', `${saved.commit}:refs/heads/wf/flow`);
    const before = refs();
    await expect(pushPreparedDeployment(dir, prepared)).rejects.toBeInstanceOf(DeploymentOutcomeUnknownError);
    expect(refs()).toBe(before);
    expect(remoteGit('tag', '-l')).toBe('');
  });

  it('creates wf using an empty lease and never overwrites a concurrently created tag', async () => {
    const saved = await save(); const dir = await clone();
    git(seed, 'push', remote, ':refs/heads/wf/flow');
    const first = await prepareDeployCandidate(dir, 'flow', saved.commit, '', 1, 1, author);
    await pushPreparedDeployment(dir, first);
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
    expect(ref('refs/heads/wf/flow')).toBe(prepared.releaseCommit);
  });

  it('retains the exact reconciliation identities after a successful atomic push loses its acknowledgement', async () => {
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
    expect(ref('refs/heads/wf/flow')).toBe(prepared.releaseCommit);
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
    expect(ref('refs/heads/wf/flow')).toBe(current);
  });

  it('never overwrites a newer saved head when resuming an old request', async () => {
    const prepared = await withPreparedPackCandidate(input(), async p => p);
    const next = newerSaved();
    const execute = vi.spyOn(gitExecution, 'executeGit');
    await expect(resumePreparedPackCandidate(prepared, input().config)).rejects.toBeInstanceOf(PackCandidateConflictError);
    expect(ref('refs/heads/saved/flow')).toBe(next);
    expect(execute.mock.calls.filter(([args]) => args[0] === 'push')).toHaveLength(0);
  });

  it('resumes exactly the original release/tag, retaining version/deploy number and ignoring newer saved', async () => {
    const saved = await save(); const dir = await clone();
    const prepared = JSON.parse(JSON.stringify(await prepareDeployCandidate(dir, 'flow', saved.commit, current, 7, 4, author)));
    rmSync(dir, { recursive: true, force: true });
    const next = newerSaved();
    const execute = vi.spyOn(gitExecution, 'executeGit');
    expect(await resumePreparedDeployment(prepared, input().config)).toMatchObject({ commit: prepared.releaseCommit, deployNumber: 7 });
    expect(await resumePreparedDeployment(prepared, input().config)).toMatchObject({ commit: prepared.releaseCommit, tagName: 'deploy/flow/#7' });
    expect(execute.mock.calls.filter(([args]) => args[0] === 'push')).toHaveLength(1);
    expect(ref('refs/heads/saved/flow')).toBe(next);
    expect(ref('refs/tags/deploy/flow/#7')).toBe(prepared.tagObject);
    expect(remoteGit('tag', '-l')).toBe('deploy/flow/#7');
  });

  it.each(['branch only', 'tag only', 'different tag', 'newer branch'])('conflicts on mixed deployment state: %s', async state => {
    const saved = await save(); const dir = await clone();
    const prepared = await prepareDeployCandidate(dir, 'flow', saved.commit, current, 1, 1, author);
    if (state === 'branch only' || state === 'different tag') {
      git(dir, 'push', '--force', 'origin', `${prepared.releaseCommit}:refs/heads/wf/flow`);
    }
    if (state === 'tag only') git(dir, 'push', 'origin', `${prepared.tagObject}:refs/tags/${prepared.tagName}`);
    if (state === 'different tag') git(dir, 'push', 'origin', `${current}:refs/tags/${prepared.tagName}`);
    if (state === 'newer branch') git(dir, 'push', '--force', 'origin', `${saved.commit}:refs/heads/wf/flow`);
    const before = refs(); const execute = vi.spyOn(gitExecution, 'executeGit');
    await expect(resumePreparedDeployment(prepared, input().config)).rejects.toBeInstanceOf(DeploymentConflictError);
    expect(refs()).toBe(before);
    expect(execute.mock.calls.filter(([args]) => args[0] === 'push')).toHaveLength(0);
  });

  it('allows two concurrent resumptions of the same save to converge without new objects', async () => {
    const prepared = await withPreparedPackCandidate(input(), async p => p);
    const outcomes = await Promise.allSettled([resumePreparedPackCandidate(prepared, input().config),
      resumePreparedPackCandidate(prepared, input().config)]);
    expect(outcomes.some(value => value.status === 'fulfilled')).toBe(true);
    // A losing receive-pack may report an ambiguous lock rejection; reconciliation
    // still converges, and no caller can publish a different candidate identity.
    for (const value of outcomes) if (value.status === 'rejected') expect(value.reason).toBeInstanceOf(PackCandidateOutcomeUnknownError);
    expect(ref('refs/heads/saved/flow')).toBe(prepared.commit);
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
    await expect(pushPreparedPackCandidate(dir, { ...saved, branch: 'wf/flow' })).rejects.toThrow('Invalid prepared candidate');
    await expect(pushPreparedPackCandidate(dir, { ...saved, sourceCommit: saved.commit, sourceVersion: 'draft', expectedSavedHead: saved.commit }))
      .rejects.toThrow('Invalid prepared candidate commit');
    const prepared = await prepareDeployCandidate(dir, 'flow', saved.commit, current, 1, 1, author);
    const before = refs();
    await expect(pushPreparedDeployment(dir, { ...prepared, version: 2 })).rejects.toThrow('annotation');
    await expect(pushPreparedDeployment(dir, { ...prepared, candidateCommit: current })).rejects.toThrow('objects');
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
});
