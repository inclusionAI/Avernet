// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createLocalRemote, runGit } from './clawflows-git-client-fixture.js';
import * as execution from '@avernet/clawweb-shared/server/services/git-execution';
import { resolveSavedWorkflowRef } from '../workflow-saved-ref.js';
let root: string, remote: string, seed: string, repo: string, base: string;
const git = (dir: string, ...args: string[]) => runGit(dir, args);
function commit(parent: string, message = 'ordinary') { return git(seed, 'commit-tree', `${base}^{tree}`, '-p', parent, '-m', message); }
function publish(oid: string, ref: string) { git(seed, 'push', remote, `${oid}:refs/heads/${ref}/flow`); }
const header = 'ClawMind saved candidate v1\n{}';
beforeEach(() => {
  root = mkdtempSync(path.join(tmpdir(), 'saved-ref-')); remote = createLocalRemote(root); seed = path.join(root, 'seed'); base = git(seed, 'rev-parse', 'HEAD');
  repo = path.join(root, 'reader'); mkdirSync(repo); git(repo, 'init'); git(repo, 'remote', 'add', 'origin', remote);
});
afterEach(() => { vi.restoreAllMocks(); rmSync(root, { recursive: true, force: true }); });
it('returns explicit absence without adopting a saved-only ref', async () => {
  publish(base, 'saved');
  expect(await resolveSavedWorkflowRef(repo, 'flow')).toEqual({ commit: null, ref: null, wfHead: null });
});
it('reads only wf, regardless of an ordinary commit or a newer saved ref', async () => {
  const legacy = commit(base); publish(base, 'wf'); publish(legacy, 'saved');
  const execute = execution.executeGit; const calls: string[][] = [];
  vi.spyOn(execution, 'executeGit').mockImplementation((args, options) => { calls.push(args); return execute(args, options); });
  expect(await resolveSavedWorkflowRef(repo, 'flow')).toEqual({ commit: base, ref: 'refs/heads/wf/flow', wfHead: base });
  expect(calls.flat().join(' ')).not.toContain('saved/');
  expect(calls.flat()).not.toContain(legacy); expect(calls.flat()).not.toContain('merge-base');
});
it('does not inspect divergent saved history or deepen a shallow wf', async () => {
  const wf = commit(base, header), legacy = commit(base, 'divergent'); publish(wf, 'wf'); publish(legacy, 'saved');
  git(repo, 'fetch', '--depth=1', '--no-tags', 'origin', 'refs/heads/wf/flow');
  expect(await resolveSavedWorkflowRef(repo, 'flow')).toEqual({ commit: wf, ref: 'refs/heads/wf/flow', wfHead: wf });
  expect(git(repo, 'rev-parse', '--is-shallow-repository')).toBe('true');
});
it('does not interpret a remote read failure as absence', async () => {
  vi.spyOn(execution, 'executeGit').mockRejectedValue(new Error('unavailable'));
  await expect(resolveSavedWorkflowRef(repo, 'flow')).rejects.toThrow();
});
it('preserves trusted SSH/CA transport on every helper read without accepting Git overrides or exposing secrets in argv', async () => {
  publish(base, 'wf');
  vi.stubEnv('GIT_SSH_COMMAND', 'trusted-ssh --identity /private/credential');
  vi.stubEnv('GIT_SSH', '/trusted/ssh'); vi.stubEnv('GIT_SSL_CAINFO', '/trusted/ca.pem');
  vi.stubEnv('GIT_CONFIG_COUNT', '1'); vi.stubEnv('GIT_CONFIG_KEY_0', 'credential.helper'); vi.stubEnv('GIT_CONFIG_VALUE_0', 'unsafe helper');
  vi.stubEnv('GIT_DIR', '/untrusted/git'); vi.stubEnv('GIT_ALTERNATE_OBJECT_DIRECTORIES', '/untrusted/objects');
  const execute = execution.executeGit;
  vi.spyOn(execution, 'executeGit').mockImplementation((args, options) => {
    expect(options?.env).toMatchObject({ GIT_SSH_COMMAND: 'trusted-ssh --identity /private/credential', GIT_SSH: '/trusted/ssh', GIT_SSL_CAINFO: '/trusted/ca.pem' });
    for (const key of ['GIT_CONFIG_COUNT', 'GIT_CONFIG_KEY_0', 'GIT_CONFIG_VALUE_0', 'GIT_DIR', 'GIT_ALTERNATE_OBJECT_DIRECTORIES']) expect(options?.env).not.toHaveProperty(key);
    expect(args.join(' ')).not.toContain('/private/credential');
    return execute(args, options);
  });
  try { expect(await resolveSavedWorkflowRef(repo, 'flow')).toMatchObject({ commit: base }); }
  finally { vi.unstubAllEnvs(); }
});
