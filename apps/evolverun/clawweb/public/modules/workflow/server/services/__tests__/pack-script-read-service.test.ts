// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import * as execution from '@avernet/clawweb-shared/server/services/git-execution';
import * as settings from '../../config/auto-heal-git-config.js';
import * as client from '../clawflows-git-client.js';
import * as snapshot from '../pack-script-snapshot.js';
import { readPackScriptsFromRemote } from '../pack-script-read-service.js';

const db = {} as IDatabase;
const gitConfig = { remoteUrl: '/unused', username: 'user', email: 'user@example.invalid', token: 'secret' };
const gitAuthor = { name: 'Test', email: 'test@example.invalid' };
const input = { db, gitConfig, gitAuthor, workflowId: 'flow', packId: 'pack' };
const result = { workflowId: 'flow', packId: 'pack', baseCommit: 'abc', baseRef: 'origin/wf/flow', files: [] };
afterEach(() => vi.restoreAllMocks());

describe('readPackScriptsFromRemote', () => {
  it('resolves fresh settings and performs the whole read and cleanup in one operation', async () => {
    const order: string[] = []; const configured = { ...gitConfig, remoteUrl: '/configured' };
    vi.spyOn(settings, 'resolveAutoHealGitSettings').mockImplementation(async (...args) => { order.push('settings'); expect(args).toEqual([db, gitConfig, gitAuthor]); return { gitConfig: configured, gitAuthor }; });
    vi.spyOn(execution, 'withGitOperation').mockImplementation(async (_fields, action) => { order.push('operation'); const value = await action(); order.push('release'); return value; });
    vi.spyOn(client, 'createTempRepo').mockImplementation(async () => { order.push('create'); return '/private/repo'; });
    vi.spyOn(client, 'cloneClawflows').mockImplementation(async (dir, config) => { order.push('clone'); expect([dir, config]).toEqual(['/private/repo', configured]); });
    vi.spyOn(snapshot, 'readPackScriptSnapshot').mockImplementation(async (dir, flow, pack) => { order.push('read'); expect([dir, flow, pack]).toEqual(['/private/repo', 'flow', 'pack']); return result; });
    vi.spyOn(client, 'removeTempRepo').mockImplementation(async dir => { order.push('remove'); expect(dir).toBe('/private/repo'); });
    expect(await readPackScriptsFromRemote(input)).toEqual(result);
    expect(order).toEqual(['settings', 'operation', 'create', 'clone', 'read', 'remove', 'release']);
    expect(execution.withGitOperation).toHaveBeenCalledOnce();
  });
  it.each(['current', 'draft'] as const)('passes %s through to the snapshot reader', async version => {
    vi.spyOn(settings, 'resolveAutoHealGitSettings').mockResolvedValue({ gitConfig, gitAuthor });
    vi.spyOn(client, 'createTempRepo').mockResolvedValue('/private/repo');
    vi.spyOn(client, 'cloneClawflows').mockResolvedValue();
    const read = vi.spyOn(snapshot, 'readPackScriptSnapshot').mockResolvedValue(result);
    vi.spyOn(client, 'removeTempRepo').mockResolvedValue();
    await readPackScriptsFromRemote({ ...input, version });
    expect(read).toHaveBeenCalledExactlyOnceWith('/private/repo', 'flow', 'pack', version);
    read.mockRejectedValue(new Error('Workflow snapshot not found'));
    await expect(readPackScriptsFromRemote({ ...input, version })).rejects.toThrow('Workflow snapshot not found');
  });
  it('resolves changed settings on each request', async () => {
    const resolver = vi.spyOn(settings, 'resolveAutoHealGitSettings')
      .mockResolvedValueOnce({ gitConfig, gitAuthor })
      .mockResolvedValueOnce({ gitConfig: { ...gitConfig, remoteUrl: '/new' }, gitAuthor });
    vi.spyOn(client, 'createTempRepo').mockResolvedValue('/private/repo');
    const clone = vi.spyOn(client, 'cloneClawflows').mockResolvedValue();
    vi.spyOn(snapshot, 'readPackScriptSnapshot').mockResolvedValue(result);
    vi.spyOn(client, 'removeTempRepo').mockResolvedValue();
    await readPackScriptsFromRemote(input); await readPackScriptsFromRemote(input);
    expect(resolver).toHaveBeenCalledTimes(2);
    expect(clone).toHaveBeenNthCalledWith(1, '/private/repo', gitConfig);
    expect(clone).toHaveBeenNthCalledWith(2, '/private/repo', { ...gitConfig, remoteUrl: '/new' });
  });
  it('preserves recognized snapshot conflicts but sanitizes cleanup errors', async () => {
    vi.spyOn(settings, 'resolveAutoHealGitSettings').mockResolvedValue({ gitConfig, gitAuthor });
    vi.spyOn(client, 'createTempRepo').mockResolvedValue('/private/repo');
    vi.spyOn(client, 'cloneClawflows').mockResolvedValue();
    const read = vi.spyOn(snapshot, 'readPackScriptSnapshot').mockRejectedValue(new Error('Pack manifest conflict'));
    const cleanup = vi.spyOn(client, 'removeTempRepo').mockResolvedValue();
    await expect(readPackScriptsFromRemote(input)).rejects.toThrow('Pack manifest conflict');
    read.mockResolvedValue(result);
    cleanup.mockRejectedValue(new Error('secret /private/repo'));
    await expect(readPackScriptsFromRemote(input)).rejects.toThrow('Pack script snapshot unavailable');
  });
  it.each(['clone', 'read', 'timeout'] as const)('cleans up after %s failure without exposing paths or credentials', async failure => {
    vi.spyOn(settings, 'resolveAutoHealGitSettings').mockResolvedValue({ gitConfig, gitAuthor });
    vi.spyOn(client, 'createTempRepo').mockResolvedValue('/private/repo');
    vi.spyOn(client, 'cloneClawflows').mockImplementation(async () => { if (failure !== 'read') throw Object.assign(new Error('secret /private/repo'), { code: failure === 'timeout' ? 'GIT_OPERATION_TIMEOUT' : 'OTHER' }); });
    vi.spyOn(snapshot, 'readPackScriptSnapshot').mockImplementation(async () => { throw new Error('secret /private/repo'); });
    const cleanup = vi.spyOn(client, 'removeTempRepo').mockResolvedValue();
    const error = await readPackScriptsFromRemote(input).catch(e => e);
    expect(error).toBeInstanceOf(Error); expect(String(error)).not.toMatch(/secret|\/private\/repo/);
    expect(cleanup).toHaveBeenCalledExactlyOnceWith('/private/repo');
  });
  it('clones a local bare repository and reads the newer of both refs', async () => {
    const root = mkdtempSync(path.join(tmpdir(), 'pack-read-test-')); const work = path.join(root, 'work'); const bare = path.join(root, 'remote.git'); mkdirSync(work);
    const git = (cwd: string, ...args: string[]) => execFileSync('git', args, { cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
    const put = (file: string, contents: string) => { const target = path.join(work, file); mkdirSync(path.dirname(target), { recursive: true }); writeFileSync(target, contents); };
    try {
      git(work, 'init', '-q'); git(work, 'config', 'user.name', 'Test'); git(work, 'config', 'user.email', 'test@example.invalid');
      put('pack/workflow.pack.yaml', 'id: pack\nworkflows:\n  - id: flow\n    file: workflows/flow.yaml\n'); put('pack/workflows/flow.yaml', 'id: flow\nnodes: []\n'); put('pack/scripts/a.py', 'old\n');
      git(work, 'add', '.'); git(work, 'commit', '-qm', 'first'); git(work, 'branch', 'wf/flow');
      put('pack/scripts/a.py', 'new\n'); put('pack/scripts/sub/b.sh', 'echo ok\n'); git(work, 'add', '.'); git(work, 'commit', '-qm', 'second'); const sha = git(work, 'rev-parse', 'HEAD'); git(work, 'branch', 'saved/flow');
      git(root, 'clone', '-q', '--bare', work, bare);
      vi.spyOn(settings, 'resolveAutoHealGitSettings').mockResolvedValue({ gitConfig: { ...gitConfig, remoteUrl: bare }, gitAuthor });
      expect(await readPackScriptsFromRemote(input)).toEqual({ workflowId: 'flow', packId: 'pack', baseCommit: sha, baseRef: 'origin/saved/flow', skippedFiles: { count: 0, paths: [], truncated: false }, files: [
        { path: 'scripts/a.py', content: 'new\n', mode: '100644' }, { path: 'scripts/sub/b.sh', content: 'echo ok\n', mode: '100644' },
      ] });
    } finally { rmSync(root, { recursive: true, force: true }); }
  });
});
