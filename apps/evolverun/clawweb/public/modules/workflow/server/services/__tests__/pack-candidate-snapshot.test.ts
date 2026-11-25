// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { chmodSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createLocalRemote, runGit } from './clawflows-git-client-fixture.js';
import { PackWorkflowContentChangedError, withPreparedPackCandidate, withPreparedPackCandidateWithWorkflow,
  type SavePackCandidateInput } from '../pack-candidate-save.js';

const gate = vi.hoisted(() => ({ wait: undefined as undefined | (() => Promise<void>), clonedConfig: undefined as unknown }));
vi.mock('../clawflows-git-client.js', async importOriginal => {
  const actual = await importOriginal<typeof import('../clawflows-git-client.js')>();
  return { ...actual, initClawflowsReadRepo: async (...args: Parameters<typeof actual.initClawflowsReadRepo>) => {
    await gate.wait?.();
    gate.clonedConfig = args[1];
    return actual.initClawflowsReadRepo(...args);
  } };
});

let root: string, bare: string, seed: string, current: string;
const b64 = (value: string) => Buffer.from(value).toString('base64');
function input(): SavePackCandidateInput {
  return { workflowId: 'flow', sourceVersion: 'current', sourceCommit: current, expectedSavedHead: current,
    config: { remoteUrl: bare, username: 'original', email: 'original@example.invalid', token: 'test-token' },
    preserveWorkflowContent: true,
    changes: [{ path: 'scripts/run', operation: 'replace', contentBase64: b64('original'), mode: '100755' }] };
}
function pauseClone() {
  let entered!: () => void, release!: () => void;
  const started = new Promise<void>(resolve => { entered = resolve; });
  const waiting = new Promise<void>(resolve => { release = resolve; });
  gate.wait = async () => { entered(); await waiting; };
  return { started, release };
}
const refs = () => runGit(root, ['--git-dir', bare, 'show-ref']);
const show = (commit: string, file: string) => runGit(root, ['--git-dir', bare, 'show', `${commit}:${file}`]);
beforeEach(() => {
  root = mkdtempSync(path.join(tmpdir(), 'candidate-snapshot-'));
  bare = createLocalRemote(root); seed = path.join(root, 'seed');
  mkdirSync(path.join(seed, 'packs/pack/workflows'), { recursive: true });
  mkdirSync(path.join(seed, 'packs/pack/scripts'), { recursive: true });
  writeFileSync(path.join(seed, 'packs/pack/workflow.pack.yaml'), 'id: pack\nworkflows: [flow]\n');
  writeFileSync(path.join(seed, 'packs/pack/workflows/flow.yaml'), 'id: flow\n');
  writeFileSync(path.join(seed, 'packs/pack/scripts/run'), 'old');
  chmodSync(path.join(seed, 'packs/pack/scripts/run'), 0o644);
  runGit(seed, ['add', '.']); runGit(seed, ['commit', '-m', 'seed']);
  current = runGit(seed, ['rev-parse', 'HEAD']);
  runGit(seed, ['push', bare, `${current}:refs/heads/wf/flow`]);
});
afterEach(() => { gate.wait = undefined; gate.clonedConfig = undefined; rmSync(root, { recursive: true, force: true }); });

describe('candidate call snapshots', () => {
  it.each(['legacy', 'workflow'] as const)('isolates %s construction, clone configuration and push from caller mutation', async kind => {
    const value = input();
    const config = value.config;
    const change = value.changes[0];
    const barrier = pauseClone();
    const callback = async (prepared: Parameters<Parameters<typeof withPreparedPackCandidate>[1]>[0], push: () => Promise<{ branch: string; commit: string }>) => {
      expect(JSON.stringify(prepared)).not.toContain('secret');
      expect(JSON.stringify(prepared)).not.toContain('test-token');
      await push(); return prepared;
    };
    const promise = kind === 'legacy' ? withPreparedPackCandidate(value, callback)
      : withPreparedPackCandidateWithWorkflow(value, 'id: flow\nname: trusted\n', callback);
    await barrier.started;
    value.workflowId = 'other'; value.sourceVersion = 'draft'; value.sourceCommit = 'f'.repeat(40);
    value.expectedSavedHead = 'e'.repeat(40); value.preserveWorkflowContent = false;
    Object.assign(config, { remoteUrl: path.join(root, 'wrong-secret'), username: 'changed-secret',
      email: 'changed@example.invalid', token: 'dummy' });
    Object.assign(change, { path: 'scripts/evil', operation: 'create', contentBase64: b64('changed'), mode: '100644' });
    value.changes.push({ path: 'extra', operation: 'create', contentBase64: b64('extra'), mode: '100644' });
    value.config = { remoteUrl: 'wrong', username: 'wrong', email: 'wrong' };
    barrier.release();
    const result = await promise;
    expect(gate.clonedConfig).toMatchObject({ token: 'test-token' });
    expect(result.workflowId).toBe('flow'); expect(result.branch).toBe('wf/flow');
    expect(result.sourceCommit).toBe(current); expect(result.expectedSavedHead).toBe(current);
    expect(result.sourceVersion).toBe('current');
    expect(show(result.commit, 'packs/pack/scripts/run')).toBe('original');
    expect(runGit(root, ['--git-dir', bare, 'ls-tree', result.commit, 'packs/pack/scripts/run'])).toMatch(/^100755 blob/);
    expect(runGit(root, ['--git-dir', bare, 'ls-tree', '-r', '--name-only', result.commit])).not.toContain('evil');
    expect(show(result.commit, 'packs/pack/workflows/flow.yaml')).toBe(kind === 'workflow' ? 'id: flow\nname: trusted' : 'id: flow');
    expect(runGit(root, ['--git-dir', bare, 'show', '-s', '--format=%an <%ae>', result.commit])).toBe('original <original@example.invalid>');
    expect(runGit(root, ['--git-dir', bare, 'rev-parse', `${result.commit}^`])).toBe(current);
    expect(refs()).toContain(`${result.commit} refs/heads/wf/flow`);
    expect(refs()).not.toContain('wf/other');
  });

  it('does not let mutation disable workflow preservation before validation or push', async () => {
    const value = input();
    value.changes = [{ path: 'workflows/flow.yaml', operation: 'replace', contentBase64: b64('id: flow\nname: forbidden\n'), mode: '100644' }];
    const before = refs(); const barrier = pauseClone(); let called = false;
    const promise = withPreparedPackCandidate(value, async (_prepared, push) => { called = true; return push(); });
    await barrier.started; value.preserveWorkflowContent = false; value.changes.length = 0; barrier.release();
    await expect(promise).rejects.toBeInstanceOf(PackWorkflowContentChangedError);
    expect(called).toBe(false); expect(refs()).toBe(before);
  });

  it('rejects an invalid trusted workflow before callback and push despite caller changes', async () => {
    const value = input(); const before = refs(); const barrier = pauseClone(); let called = false;
    const promise = withPreparedPackCandidateWithWorkflow(value, 'id: wrong\n', async (_prepared, push) => { called = true; return push(); });
    await barrier.started; value.workflowId = 'wrong'; value.config.token = 'dummy'; barrier.release();
    await expect(promise).rejects.toThrow('Workflow identity mismatch');
    expect(called).toBe(false); expect(refs()).toBe(before);
  });
});
