// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import * as gitExecution from '@avernet/clawweb-shared/server/services/git-execution';
import { resolveActiveDeploymentCommit } from '../active-deployment-git.js';
import { createLocalRemote, runGit } from './clawflows-git-client-fixture.js';

let root: string, remote: string, seed: string, repo: string, production: string, draft: string;
const tag = 'deploy/flow/#1';
const git = (dir: string, ...args: string[]) => runGit(dir, args);
const remoteGit = (...args: string[]) => git(root, '--git-dir', remote, ...args);
beforeEach(() => {
  root = mkdtempSync(path.join(tmpdir(), 'active-tag-read-')); remote = createLocalRemote(root); seed = path.join(root, 'seed');
  production = git(seed, 'rev-parse', 'HEAD'); git(seed, 'tag', '-a', tag, production, '-m', 'production');
  writeFileSync(path.join(seed, 'draft.txt'), 'newer unpublished draft'); git(seed, 'add', '.'); git(seed, 'commit', '-m', 'draft'); draft = git(seed, 'rev-parse', 'HEAD');
  git(seed, 'push', remote, `refs/tags/${tag}`, `${draft}:refs/heads/wf/flow`);
  repo = path.join(root, 'empty-reader'); mkdirSync(repo); git(repo, 'init'); git(repo, 'remote', 'add', 'origin', remote);
});
afterEach(() => { vi.restoreAllMocks(); rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); });

describe('authorized active history tag Git resolution', () => {
  it('returns null only for an explicitly absent active tag', async () => {
    await expect(resolveActiveDeploymentCommit(repo, 'flow', null)).resolves.toBeNull();
  });
  it('fetches and peels the active tag without interpreting wf as production or changing refs', async () => {
    const refs = remoteGit('show-ref'); const localRefs = git(repo, 'for-each-ref');
    expect(await resolveActiveDeploymentCommit(repo, 'flow', tag)).toBe(production);
    expect(production).not.toBe(draft); expect(remoteGit('show-ref')).toBe(refs); expect(git(repo, 'for-each-ref')).toBe(localRefs);
    expect(git(repo, 'cat-file', '-t', production)).toBe('commit');
  });
  it('does not honor configured tag refmaps that would install local tag refs', async () => {
    git(repo, 'config', '--add', 'remote.origin.fetch', '+refs/tags/*:refs/tags/*');
    const before = git(repo, 'for-each-ref');
    await expect(resolveActiveDeploymentCommit(repo, 'flow', tag)).resolves.toBe(production);
    expect(git(repo, 'for-each-ref')).toBe(before);
  });
  it('supports a historical lightweight commit tag', async () => {
    git(seed, 'tag', 'deploy/flow/#2', production); git(seed, 'push', remote, 'refs/tags/deploy/flow/#2');
    await expect(resolveActiveDeploymentCommit(repo, 'flow', 'deploy/flow/#2')).resolves.toBe(production);
  });
  it.each(['', 'deploy/other/#1', 'deploy/flow/#0', 'deploy/flow/#01', 'refs/tags/deploy/flow/#1', 'deploy/flow/#1\n', '--upload-pack=x'])('rejects invalid tag identity %j', async name => {
    await expect(resolveActiveDeploymentCommit(repo, 'flow', name)).rejects.toThrow();
  });
  it('fails closed on missing active tag, even though wf exists', async () => {
    await expect(resolveActiveDeploymentCommit(repo, 'flow', 'deploy/flow/#99')).rejects.toThrow();
  });
  it('rejects a tag targeting a tree', async () => {
    git(seed, 'tag', '-a', 'deploy/flow/#3', `${production}^{tree}`, '-m', 'not a workflow commit');
    git(seed, 'push', remote, 'refs/tags/deploy/flow/#3');
    await expect(resolveActiveDeploymentCommit(repo, 'flow', 'deploy/flow/#3')).rejects.toThrow();
  });
  it('fails closed if the remote tag is replaced during the read', async () => {
    const execute = gitExecution.executeGit; let changed = false;
    vi.spyOn(gitExecution, 'executeGit').mockImplementation(async (args, options) => {
      const result = await execute(args, options);
      if (args[0] === 'ls-remote' && !changed) {
        changed = true; remoteGit('update-ref', `refs/tags/${tag}`, draft);
      }
      return result;
    });
    await expect(resolveActiveDeploymentCommit(repo, 'flow', tag)).rejects.toThrow(); expect(changed).toBe(true);
    expect(remoteGit('rev-parse', 'refs/heads/wf/flow')).toBe(draft);
  });
});
