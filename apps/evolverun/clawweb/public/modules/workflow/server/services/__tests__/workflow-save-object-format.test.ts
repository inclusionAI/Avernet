// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import * as execution from '@avernet/clawweb-shared/server/services/git-execution';
import { runGit } from './clawflows-git-client-fixture.js';
import { withPreparedWorkflowSpec } from '../pack-spec-save.js';
import { withPreparedPackCandidate, resumePreparedPackCandidate, type PreparedPackCandidate } from '../pack-candidate-save.js';
import { createPreparedGitBundle, readPreparedPackMetadata } from '../pack-git-prepared.js';
import { initSavedWorkflowRepo, workflowSavedCommitMessage } from '../workflow-saved-ref.js';
vi.setConfig({ testTimeout: 30_000, hookTimeout: 30_000 });
let root: string, remote: string, seed: string, base: string;
const git = (dir: string, ...args: string[]) => runGit(dir, args);
const remoteGit = (...args: string[]) => git(root, '--git-dir', remote, ...args);
const author = { name: 'Fixture', email: 'fixture@example.invalid' };
const config = () => ({ remoteUrl: remote, username: author.name, email: author.email });
function recordGit() {
  const calls: string[][] = [], execute = execution.executeGit;
  vi.spyOn(execution, 'executeGit').mockImplementation((args, options) => { calls.push(args); return execute(args, options); });
  return calls;
}
function assertWfOnly(calls: string[][]) {
  expect(calls.flat().some(arg => arg.includes('saved/'))).toBe(false);
  expect(calls.flat()).not.toContain('clone');
  expect(calls.flat()).not.toContain('--all');
}
async function preparedJournal(initial = false): Promise<PreparedPackCandidate> {
  writeFileSync(path.join(seed, 'workflows/flow.yaml'), 'id: flow\ntitle: prepared\nnodes: []\n');
  git(seed, 'add', '.'); const tree = git(seed, 'write-tree');
  const source = await readPreparedPackMetadata(seed, 'flow', base);
  const message = await workflowSavedCommitMessage(seed, 'flow', tree, source);
  const commit = git(seed, 'commit-tree', tree, ...(initial ? [] : ['-p', base]), '-m', message);
  return { ...await readPreparedPackMetadata(seed, 'flow', commit), workflowId: 'flow', branch: 'wf/flow', commit,
    sourceVersion: initial ? 'initial' : 'draft', sourceCommit: initial ? null : base,
    expectedSavedHead: initial ? null : base, bundleBase64: await createPreparedGitBundle(seed, [commit]) };
}

describe.each(['sha1', 'sha256'] as const)('workflow save/resume object format: %s', format => {
  beforeEach(() => {
    root = mkdtempSync(path.join(tmpdir(), 'wf-format-')); remote = path.join(root, 'remote.git'); seed = path.join(root, 'seed'); mkdirSync(seed);
    git(root, 'init', '--bare', `--object-format=${format}`, '--initial-branch=main', remote);
    git(seed, 'init', `--object-format=${format}`, '--initial-branch=main');
    git(seed, 'config', 'user.name', author.name); git(seed, 'config', 'user.email', author.email);
    mkdirSync(path.join(seed, 'workflows')); mkdirSync(path.join(seed, 'scripts'));
    writeFileSync(path.join(seed, 'workflow.pack.yaml'), 'id: pack\nversion: 1\nworkflows: [flow]\n');
    writeFileSync(path.join(seed, 'workflows/flow.yaml'), 'id: flow\ntitle: before\nnodes: []\n');
    writeFileSync(path.join(seed, 'scripts/run.sh'), '#!/bin/sh\necho keep\n'); chmodSync(path.join(seed, 'scripts/run.sh'), 0o755);
    writeFileSync(path.join(seed, 'asset.bin'), Buffer.from([0, 255, 128, 42]));
    git(seed, 'add', '.'); git(seed, 'commit', '-m', 'source'); base = git(seed, 'rev-parse', 'HEAD');
    git(seed, 'push', remote, 'HEAD:refs/heads/main', 'HEAD:refs/heads/wf/flow');
  });
  afterEach(() => { vi.restoreAllMocks(); rmSync(root, { recursive: true, force: true }); });

  it.each([false, true])('creates a genuinely new workflow in the remote format without adopting HEAD (has HEAD=%s)', async hasHead => {
    remote = path.join(root, 'new-remote.git');
    git(root, 'init', '--bare', `--object-format=${format}`, '--initial-branch=main', remote);
    if (hasHead) git(seed, 'push', remote, 'HEAD:refs/heads/main');
    const before = remoteGit('for-each-ref', '--format=%(objectname) %(refname)');
    const calls = recordGit();
    const register = vi.fn(async (prepared: PreparedPackCandidate, push: () => Promise<unknown>) => {
      expect(remoteGit('for-each-ref', '--format=%(objectname) %(refname)')).toBe(before);
      expect(prepared).toMatchObject({ sourceVersion: 'initial', sourceCommit: null, expectedSavedHead: null });
      const probes = vi.mocked(execution.executeGit).mock.calls.filter(([, options]) => options?.env?.GIT_TRACE_PACKET);
      expect(probes).toHaveLength(1);
      expect(existsSync(probes[0][1]!.env!.GIT_TRACE_PACKET!)).toBe(false);
      await push(); return prepared;
    });
    const prepared = await withPreparedWorkflowSpec({ workflowId: 'new-flow', packId: 'new-pack',
      spec: { id: 'new-flow', title: 'New workflow', nodes: [] }, config: config(), author, createOnly: true }, register);
    expect(register).toHaveBeenCalledTimes(1);
    expect(prepared.commit).toHaveLength(format === 'sha256' ? 64 : 40);
    expect(remoteGit('rev-parse', 'refs/heads/wf/new-flow')).toBe(prepared.commit);
    expect(remoteGit('show', '-s', '--format=%P', prepared.commit)).toBe('');
    expect(remoteGit('ls-tree', '-r', '--name-only', prepared.commit).split('\n')).toEqual([
      'packs/new-pack/workflow.pack.yaml', 'packs/new-pack/workflows/new-flow.yaml',
    ]);
    expect(remoteGit('for-each-ref', '--format=%(objectname) %(refname)', 'refs/heads/main')).toBe(before);
    expect(calls.some(args => args.includes('fetch'))).toBe(false);
    assertWfOnly(calls);
  });
  it.each(['missing', 'outbound-only', 'conflicting', 'unsupported', 'incomplete', 'v0-incomplete', 'malformed', 'failed'])('rejects %s capabilities before the registration/DB callback', async kind => {
    const execute = execution.executeGit; const calls: string[][] = [];
    vi.spyOn(execution, 'executeGit').mockImplementation((args, options) => {
      calls.push(args);
      const tracePath = options?.env?.GIT_TRACE_PACKET;
      if (tracePath) {
        if (kind === 'failed') return Promise.reject(new Error('provider secret must not escape'));
        const transcript = kind === 'missing' ? '' : kind === 'outbound-only' ? `packet: ls-remote> object-format=${format}\n`
          : kind === 'v0-incomplete' ? `packet: ls-remote< ${'0'.repeat(base.length)} capabilities^{}\\0object-format=${format}\n`
          : `packet: ls-remote< version 2\npacket: ls-remote< object-format=${kind === 'unsupported' ? 'unknown' : format}\n`
            + (kind === 'conflicting' ? 'packet: ls-remote< object-format=sha512\n' : kind === 'malformed' ? 'packet: ls-remote< bad capability\n' : '')
            + (kind === 'incomplete' ? '' : 'packet: ls-remote< 0000\n');
        writeFileSync(tracePath, transcript);
        return Promise.resolve(Buffer.alloc(0));
      }
      return execute(args, options);
    });
    const register = vi.fn();
    await expect(withPreparedWorkflowSpec({ workflowId: 'new-flow', packId: 'new-pack',
      spec: { id: 'new-flow', nodes: [] }, config: config(), author, createOnly: true }, register))
      .rejects.toThrow('Cannot determine remote Git object format');
    expect(register).not.toHaveBeenCalled();
    expect(calls.some(args => args.includes('push') || args.includes('commit-tree'))).toBe(false);
    assertWfOnly(calls);
  });
  it.each(['v0', 'v1', 'v2'])('uses the standard SHA-1 default only for a complete %s advertisement without object-format', async protocol => {
    remote = path.join(root, 'old-sha1.git');
    git(root, 'init', '--bare', '--object-format=sha1', '--initial-branch=main', remote);
    const execute = execution.executeGit; const calls: string[][] = [];
    vi.spyOn(execution, 'executeGit').mockImplementation((args, options) => {
      calls.push(args);
      const tracePath = options?.env?.GIT_TRACE_PACKET;
      if (tracePath) {
        const packet = protocol === 'v2' ? 'packet: ls-remote< version 2\npacket: ls-remote< ls-refs\npacket: ls-remote< fetch=shallow\npacket: ls-remote< 0000\n'
          : (protocol === 'v1' ? 'packet: ls-remote< version 1\n' : '')
            + `packet: ls-remote< ${'0'.repeat(40)} capabilities^{}\\0multi_ack thin-pack agent=old-git\npacket: ls-remote< 0000\n`;
        writeFileSync(tracePath, packet);
        return Promise.resolve(Buffer.alloc(0));
      }
      return execute(args, options);
    });
    const prepared = await withPreparedWorkflowSpec({ workflowId: 'new-flow', packId: 'new-pack',
      spec: { id: 'new-flow', nodes: [] }, config: config(), author, createOnly: true }, async (p, push) => { await push(); return p; });
    expect(prepared.commit).toHaveLength(40);
    expect(remoteGit('rev-parse', 'refs/heads/wf/new-flow')).toBe(prepared.commit);
    expect(remoteGit('show', '-s', '--format=%P', prepared.commit)).toBe('');
    expect(calls.some(args => args.includes('fetch'))).toBe(false);
    assertWfOnly(calls);
  });
  it.each(['v0', 'v1', 'http-v2'])('accepts explicit inbound %s capabilities without using any advertised tree', async protocol => {
    const execute = execution.executeGit; const calls: string[][] = [];
    vi.spyOn(execution, 'executeGit').mockImplementation((args, options) => {
      calls.push(args);
      const tracePath = options?.env?.GIT_TRACE_PACKET;
      if (tracePath) {
        const packet = protocol === 'http-v2' ? `packet: git< version 2\npacket: git< object-format=${format}\npacket: git< 0000\n`
          : (protocol === 'v1' ? 'packet: ls-remote< version 1\n' : '')
            + `packet: ls-remote< ${'0'.repeat(base.length)} capabilities^{}\\0object-format=${format}\npacket: ls-remote< 0000\n`;
        writeFileSync(tracePath, packet);
        return Promise.resolve(Buffer.alloc(0));
      }
      return execute(args, options);
    });
    const prepared = await withPreparedWorkflowSpec({ workflowId: 'new-flow', packId: 'new-pack',
      spec: { id: 'new-flow', nodes: [] }, config: config(), author, createOnly: true }, async (p, push) => { await push(); return p; });
    expect(prepared.commit).toHaveLength(base.length);
    expect(remoteGit('show', '-s', '--format=%P', prepared.commit)).toBe('');
    assertWfOnly(calls);
  });
  it('prepares and pushes YAML-only spec using the observed wf object format, preserving the full Pack', async () => {
    const calls = recordGit();
    const result = await withPreparedWorkflowSpec({ workflowId: 'flow', packId: 'pack', spec: { id: 'flow', title: 'after', nodes: [] },
      config: config(), author, expectedSavedHead: base }, async (prepared, push) => {
      expect(prepared.commit).toHaveLength(base.length); expect(prepared.sourceCommit).toBe(base); return push();
    });
    expect(remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(result.commit);
    for (const file of ['workflow.pack.yaml', 'scripts/run.sh', 'asset.bin']) expect(remoteGit('ls-tree', result.commit, file)).toBe(remoteGit('ls-tree', base, file));
    const message = remoteGit('show', '-s', '--format=%B', result.commit);
    expect(JSON.parse(message.slice('ClawMind saved candidate v1\n'.length)).packDigest).toBe(remoteGit('rev-parse', `${result.commit}^{tree}`));
    if (format === 'sha256') expect(calls.some(args => args[0] === 'init' && args.includes('--object-format=sha256'))).toBe(true);
    assertWfOnly(calls);
  });
  it('prepares binary Pack changes and uses the matching wf lease', async () => {
    const calls = recordGit();
    const result = await withPreparedPackCandidate({ workflowId: 'flow', sourceVersion: 'draft', sourceCommit: base, expectedSavedHead: base,
      config: config(), changes: [{ path: 'new.bin', operation: 'create', contentBase64: Buffer.from([0, 255]).toString('base64'), mode: '100644' }] }, async (_p, push) => push());
    expect(result.commit).toHaveLength(base.length);
    expect(remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(result.commit);
    expect(remoteGit('ls-tree', result.commit, 'scripts/run.sh')).toBe(remoteGit('ls-tree', base, 'scripts/run.sh'));
    expect(calls.find(args => args[0] === 'push')).toContain(`--force-with-lease=refs/heads/wf/flow:${base}`);
    assertWfOnly(calls);
  });
  it.each([false, true])('resumes a verified journal in its OID format (already applied=%s)', async alreadyApplied => {
    const prepared = await preparedJournal();
    if (alreadyApplied) git(seed, 'push', remote, `${prepared.commit}:refs/heads/wf/flow`);
    const calls = recordGit();
    expect(await resumePreparedPackCandidate(prepared, config())).toEqual({ branch: 'wf/flow', commit: prepared.commit });
    expect(remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(prepared.commit);
    expect(calls.filter(args => args[0] === 'push')).toHaveLength(alreadyApplied ? 0 : 1);
    if (format === 'sha256') expect(calls.find(args => args[0] === 'init')).toContain('--object-format=sha256');
    assertWfOnly(calls);
  });
  it('uses a validated initial prepared OID when no wf exists to advertise a format', async () => {
    const prepared = await preparedJournal(true); git(seed, 'push', remote, ':refs/heads/wf/flow');
    const calls = recordGit();
    expect(await resumePreparedPackCandidate(prepared, config())).toMatchObject({ commit: prepared.commit });
    expect(remoteGit('show', '-s', '--format=%P', prepared.commit)).toBe('');
    if (format === 'sha256') expect(calls.find(args => args[0] === 'init')).toContain('--object-format=sha256');
    assertWfOnly(calls);
  });
  it('never reinitializes a caller-owned existing repository', async () => {
    const calls = recordGit();
    await expect(initSavedWorkflowRepo(seed, 'flow', config())).rejects.toThrow('empty temporary directory');
    expect(calls).toEqual([]); expect(git(seed, 'rev-parse', 'HEAD')).toBe(base);
    expect(git(seed, 'rev-parse', '--show-object-format')).toBe(format);
  });
  it('keeps the observed wf lease when the remote advances during format initialization/fetch', async () => {
    const next = git(seed, 'commit-tree', `${base}^{tree}`, '-p', base, '-m', 'concurrent wf');
    const dir = path.join(root, 'new-save'); mkdirSync(dir);
    const execute = execution.executeGit; let advanced = false, observations = 0;
    vi.spyOn(execution, 'executeGit').mockImplementation((args, options) => {
      if (args[0] === 'ls-remote') observations++;
      if (!advanced && (format === 'sha256' ? args[0] === 'init' && args.includes('--object-format=sha256') : args[0] === 'cat-file')) {
        git(seed, 'push', remote, `${next}:refs/heads/wf/flow`); advanced = true;
      }
      return execute(args, options);
    });
    expect(await initSavedWorkflowRepo(dir, 'flow', config())).toEqual({ commit: base, ref: 'refs/heads/wf/flow', wfHead: base });
    expect(advanced).toBe(true); expect(observations).toBe(1);
    expect(remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(next);
  });
  it('does not adopt or fetch a wf created during the missing-wf capability probe', async () => {
    const dir = path.join(root, 'initial-save'); mkdirSync(dir);
    const execute = execution.executeGit; const calls: string[][] = []; let advanced = false;
    vi.spyOn(execution, 'executeGit').mockImplementation((args, options) => {
      calls.push(args);
      if (options?.env?.GIT_TRACE_PACKET) {
        git(seed, 'push', remote, `${base}:refs/heads/wf/new-flow`); advanced = true;
      }
      return execute(args, options);
    });
    expect(await initSavedWorkflowRepo(dir, 'new-flow', config())).toEqual({ commit: null, ref: null, wfHead: null });
    expect(advanced).toBe(true);
    expect(git(dir, 'rev-parse', '--show-object-format')).toBe(format);
    expect(calls.filter(args => args.includes('ls-remote'))).toHaveLength(2);
    expect(calls.some(args => args.includes('fetch'))).toBe(false);
    expect(remoteGit('rev-parse', 'refs/heads/wf/new-flow')).toBe(base);
    assertWfOnly(calls);
  });
  it('rejects mixed-format prepared identities before initializing or reading a remote', async () => {
    const prepared = await preparedJournal(); const calls = recordGit();
    await expect(resumePreparedPackCandidate({ ...prepared, sourceCommit: 'b'.repeat(format === 'sha1' ? 64 : 40) }, config())).rejects.toThrow('Invalid prepared candidate');
    expect(calls).toEqual([]); expect(remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(base);
  });
});
