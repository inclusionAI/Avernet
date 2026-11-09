// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createLocalRemote, runGit } from './clawflows-git-client-fixture.js';
import * as execution from '@avernet/clawweb-shared/server/services/git-execution';
import { resolveSavedWorkflowRef } from '../workflow-saved-ref.js';
import { withPreparedPackCandidate } from '../pack-candidate-save.js';
import { withPreparedWorkflowSpec } from '../pack-spec-save.js';
vi.setConfig({ testTimeout: 30_000 });
let root: string, remote: string, seed: string;
const git = (dir: string, ...args: string[]) => runGit(dir, args);
const rg = (...args: string[]) => git(root, '--git-dir', remote, ...args);
const config = () => ({ remoteUrl: remote, username: 'test', email: 'test@example.invalid' });
const author = { name: 'test', email: 'test@example.invalid' };
function pack(prefix = '') {
  const dir = path.join(seed, prefix); mkdirSync(path.join(dir, 'workflows'), { recursive: true });
  writeFileSync(path.join(dir, 'workflow.pack.yaml'), 'id: pack\nversion: 1\nworkflows: [flow]\n');
  writeFileSync(path.join(dir, 'workflows/flow.yaml'), 'id: flow\ntitle: wf\nnodes: []\n');
  writeFileSync(path.join(dir, 'notify.sh'), '#!/bin/sh\necho wf\n'); git(seed, 'add', '.'); git(seed, 'update-index', '--chmod=+x', `${prefix ? prefix + '/' : ''}notify.sh`); git(seed, 'commit', '-m', 'wf');
  return git(seed, 'rev-parse', 'HEAD');
}
function observedCommands() {
  const execute = execution.executeGit; const calls: string[][] = [];
  vi.spyOn(execution, 'executeGit').mockImplementation((args, options) => { calls.push(args); return execute(args, options); });
  return () => { expect(calls.some(args => args.includes('clone') || args.includes('merge-base') || args.some(arg => arg.includes('saved/')))).toBe(false); };
}
beforeEach(() => { root = mkdtempSync(path.join(tmpdir(), 'wf-only-')); remote = createLocalRemote(root); seed = path.join(root, 'seed'); });
afterEach(() => { vi.restoreAllMocks(); rmSync(root, { recursive: true, force: true }); });
it('saved-only is absence, even when remote HEAD points at saved; first save never adopts that Pack', async () => {
  const legacy = pack(); git(seed, 'push', remote, `${legacy}:refs/heads/saved/flow`); rg('symbolic-ref', 'HEAD', 'refs/heads/saved/flow');
  const reader = path.join(root, 'reader'); mkdirSync(reader); git(reader, 'init'); git(reader, 'remote', 'add', 'origin', remote);
  const assertCommands = observedCommands();
  expect(await resolveSavedWorkflowRef(reader, 'flow')).toEqual({ commit: null, ref: null, wfHead: null });
  const result = await withPreparedWorkflowSpec({ workflowId: 'flow', packId: 'pack', spec: { id: 'flow', title: 'new', nodes: [] }, config: config(), author, createOnly: true }, async (p, push) => {
    expect(p.sourceCommit).toBeNull(); expect(p.expectedSavedHead).toBeNull(); expect(p.sourceVersion).toBe('initial'); await push(); return p;
  });
  expect(rg('show', '-s', '--format=%P', result.commit)).toBe('');
  expect(rg('ls-tree', '-r', '--name-only', result.commit)).not.toContain('notify.sh');
  expect(rg('rev-parse', 'refs/heads/saved/flow')).toBe(legacy); assertCommands();
});
it.each(['', 'packs/pack'])('saves only the wf full tree in %s despite saved divergence and movement', async prefix => {
  const base = pack(prefix); git(seed, 'push', remote, `${base}:refs/heads/wf/flow`);
  const parent = git(seed, 'rev-parse', `${base}^`);
  const legacy = git(seed, 'commit-tree', `${parent}^{tree}`, '-p', parent, '-m', 'different saved tree');
  git(seed, 'push', remote, `${legacy}:refs/heads/saved/flow`);
  const assertCommands = observedCommands();
  const result = await withPreparedPackCandidate({ workflowId: 'flow', sourceVersion: 'draft', sourceCommit: base,
    expectedSavedHead: base, config: config(), changes: [] }, async (_p, push) => {
      const moved = git(seed, 'commit-tree', `${parent}^{tree}`, '-p', legacy, '-m', 'saved moved independently');
      git(seed, 'push', remote, `${moved}:refs/heads/saved/flow`); return push();
    });
  expect(rg('rev-parse', 'refs/heads/wf/flow')).toBe(result.commit);
  expect(rg('rev-parse', `${result.commit}^{tree}`)).toBe(rg('rev-parse', `${base}^{tree}`));
  expect(rg('ls-tree', result.commit, `${prefix ? prefix + '/' : ''}notify.sh`)).toMatch(/^100755/); assertCommands();
}, 30_000);
it('rejects missing wf before a non-createOnly caller can enter its DB callback', async () => {
  const callback = vi.fn();
  await expect(withPreparedWorkflowSpec({ workflowId: 'flow', packId: 'pack', spec: { id: 'flow', nodes: [] }, config: config(), author }, callback))
    .rejects.toMatchObject({ code: 'SAVE_REQUIRED' });
  expect(callback).not.toHaveBeenCalled();
  expect(git(root, 'ls-remote', remote, 'refs/heads/wf/flow')).toBe('');
});
