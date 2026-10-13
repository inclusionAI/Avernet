import { afterEach, describe, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { RepairGitStore, createRepairAisExecution, resolveRepairAisConfiguration } from '../repair-ais-execution.js';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import type { AistudioService } from '@avernet/clawinsight/server/services/aistudio-service';
import { digestRepairJson } from '@avernet/workflow/server/contracts/repair-batch';

const roots: string[] = [];
afterEach(() => { for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true }); });

function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'repair-git-test-'));
  roots.push(root);
  const source = join(root, 'source');
  const remote = join(root, 'remote.git');
  const reader = join(root, 'reader.git');
  mkdirSync(source);
  const git = (cwd: string, ...args: string[]) => execFileSync('git', ['-C', cwd, ...args], { encoding: 'utf8' }).trim();
  git(source, 'init', '-q');
  git(source, 'config', 'user.name', 'test');
  git(source, 'config', 'user.email', 'test@example.com');
  mkdirSync(join(source, 'workflows'));
  writeFileSync(join(source, 'workflow.pack.yaml'), 'id: pack\nworkflows:\n  - id: wf\n    file: workflows/wf.yaml\n');
  const spec = 'id: wf\nnodes: []\n';
  writeFileSync(join(source, 'workflows/wf.yaml'), spec);
  git(source, 'add', '.');
  git(source, 'commit', '-qm', 'baseline');
  git(source, 'tag', 'deploy/wf/#1');
  const baseline = git(source, 'rev-parse', 'HEAD');
  git(root, 'init', '--bare', '-q', remote);
  git(source, 'remote', 'add', 'origin', remote);
  git(source, 'push', '-q', 'origin', 'HEAD:refs/heads/master', 'refs/tags/deploy/wf/#1');
  git(root, 'clone', '--bare', '-q', remote, reader);
  return { source, remote, reader, git, baseline, spec };
}

describe('Workflow Repair Git-backed AIS execution', () => {
  it('rejects partial or invalid repair configuration but permits an unconfigured optional provider', async () => {
    await expect(resolveRepairAisConfiguration('fixture')).resolves.toBeUndefined();
    await expect(resolveRepairAisConfiguration('fixture', 'not-a-number', '/missing/repair-mirror.git'))
      .rejects.toThrow('snapshot');
    await expect(resolveRepairAisConfiguration('fixture', '28720256'))
      .rejects.toThrow('Git');
    await expect(resolveRepairAisConfiguration(undefined, '28720256', '/missing/repair-mirror.git'))
      .rejects.toThrow('token');
    await expect(resolveRepairAisConfiguration('fixture', '28720256', '/missing/repair-mirror.git'))
      .rejects.toThrow('Git');
    const f = fixture();
    await expect(resolveRepairAisConfiguration('fixture', '28720256', f.reader))
      .resolves.toMatchObject({ snapshotId: 28720256 });
  }, 15_000);

  it('agrees with the TaskGuard full-draft canonical digest fixture', () => {
    expect(digestRepairJson({ repoId: 'clawflows', branch: 'repair/example/WR-1',
      candidateCommit: 'a'.repeat(40), parentCandidateCommit: 'b'.repeat(40),
      packDigest: 'c'.repeat(40), summary: '修复建议', itemResults: [] }))
      .toBe('441d2670ec7322879c1dd954da12c3ca2b98ef4c0b58cdb6262c9a621229903c');
  });
  it('resolves a frozen active tag and reads only identity-bound Git result metadata', async () => {
    const f = fixture();
    const store = new RepairGitStore(f.reader);
    await expect(store.verify()).resolves.toBeUndefined();
    await expect(new RepairGitStore(f.source).verify()).rejects.toThrow('bare mirror');
    const baseline = await store.baseline({ workflowId: 'wf', packId: 'pack', version: 4,
      deployNumber: 1, tagName: 'deploy/wf/#1', specJson: f.spec });
    expect(baseline).toMatchObject({ packCommit: f.baseline, releaseRevision: 4,
      specDigest: createHash('sha256').update(f.spec).digest('hex'), specPath: 'workflows/wf.yaml' });

    writeFileSync(join(f.source, 'workflows/wf.yaml'), 'id: wf\nnodes: [fixed]\n');
    f.git(f.source, 'commit', '-qam', 'candidate');
    const candidate = f.git(f.source, 'rev-parse', 'HEAD');
    const tree = f.git(f.source, 'rev-parse', 'HEAD^{tree}');
    const branch = 'repair/wf/WR-1';
    const draft = { repoId: 'clawflows', branch, candidateCommit: candidate,
      parentCandidateCommit: f.baseline, packDigest: tree, summary: 'fixed', itemResults: [] };
    const checks = { candidateCommit: candidate, packDigest: tree };
    const result = { schemaVersion: 'workflow-repair-result/v1', taskId: 'WR-1', stepId: 'WR-1-draft-1',
      revision: 1, attempt: 1, executionId: 'WRX-1', status: 'succeeded', draft, checks };
    const message = `ClawMind workflow-repair-result/v1\n${JSON.stringify(result)}`;
    f.git(f.source, 'commit', '--allow-empty', '-qm', message);
    f.git(f.source, 'push', '-q', 'origin', `HEAD:refs/heads/${branch}`);
    const request = { actorId: '123', identity: { stepId: 'WR-1-draft-1', attempt: 1, executionId: 'WRX-1' },
      input: { taskId: 'WR-1', revision: 1, taskBranch: branch, baseline } } as Parameters<typeof store.result>[0];
    expect(await store.result(request)).toMatchObject({ status: 'succeeded', draft });
    expect(await store.diff({ workflowId: 'wf', taskId: 'WR-1', revision: 1,
      baseCommit: f.baseline, candidateCommit: candidate })).toMatchObject({
        files: [{ path: 'workflows/wf.yaml', status: 'M', before: f.spec, after: 'id: wf\nnodes: [fixed]\n' }],
      truncated: false,
    });
    await expect(store.result({ ...request, identity: { ...request.identity, executionId: 'other' } }))
      .rejects.toThrow('does not match');

    writeFileSync(join(f.source, 'workflows/wf.yaml'), 'id: wf\nnodes: [second]\n');
    f.git(f.source, 'commit', '-qam', 'candidate revision two');
    const secondCandidate = f.git(f.source, 'rev-parse', 'HEAD');
    const secondTree = f.git(f.source, 'rev-parse', 'HEAD^{tree}');
    const secondDraft = { ...draft, candidateCommit: secondCandidate,
      parentCandidateCommit: candidate, packDigest: secondTree };
    const secondResult = { ...result, stepId: 'WR-1-draft-2', revision: 2,
      executionId: 'WRX-2', draft: secondDraft, checks: { candidateCommit: secondCandidate, packDigest: secondTree } };
    f.git(f.source, 'commit', '--allow-empty', '-qm', `ClawMind workflow-repair-result/v1\n${JSON.stringify(secondResult)}`);
    f.git(f.source, 'push', '-q', 'origin', `HEAD:refs/heads/${branch}`);
    expect(await store.result({ ...request, identity: { stepId: 'WR-1-draft-2', attempt: 1,
      executionId: 'WRX-2' }, input: { ...request.input, revision: 2, parentCandidateCommit: candidate } }))
      .toMatchObject({ status: 'succeeded', draft: secondDraft });
    f.git(f.reader, 'gc', '--prune=now');
    expect(await store.diff({ workflowId: 'wf', taskId: 'WR-1', revision: 1,
      baseCommit: f.baseline, candidateCommit: candidate })).toMatchObject({
        files: [{ path: 'workflows/wf.yaml', status: 'M' }], truncated: false,
      });
  }, 20_000);

  it('truncates a valid long-path file list instead of exhausting the Git output buffer', async () => {
    const f = fixture();
    const directory = join('workflows', 'a'.repeat(220), 'b'.repeat(220), 'c'.repeat(220));
    mkdirSync(join(f.source, directory), { recursive: true });
    for (let index = 0; index < 180; index += 1) {
      writeFileSync(join(f.source, directory, `${'d'.repeat(160)}-${String(index).padStart(3, '0')}.txt`), 'x');
    }
    f.git(f.source, 'add', '.');
    f.git(f.source, 'commit', '-qm', 'many valid paths');
    const candidate = f.git(f.source, 'rev-parse', 'HEAD');
    f.git(f.source, 'push', '-q', 'origin', 'HEAD:refs/heads/large-diff');
    f.git(f.reader, 'fetch', '-q', 'origin', '+refs/heads/large-diff:refs/workflow-repair-fixture/large-diff');
    const result = await new RepairGitStore(f.reader).diff({ workflowId: 'wf', taskId: 'WR-1',
      revision: 1, baseCommit: f.baseline, candidateCommit: candidate });
    expect(result).toMatchObject({ truncated: true });
    expect(result.files).toHaveLength(20);
  }, 30_000);

  it('dispatches the frozen v2 envelope as one AIS global parameter', async () => {
    const execute = vi.fn().mockResolvedValue('job-1');
    const adapter = createRepairAisExecution({} as IDatabase, { execute } as unknown as AistudioService,
      {} as RepairGitStore, 28720256);
    const input = { taskId: 'WR-1', revision: 1, excludedSources: { count: 3, digest: 'a'.repeat(64) } };
    expect(await adapter.dispatch({ actorId: '123', identity: { stepId: 'WR-1-draft-1', attempt: 1,
      executionId: 'WRX-1' }, input } as Parameters<typeof adapter.dispatch>[0])).toEqual({ jobId: 'job-1' });
    const [actor, globals, snapshot] = execute.mock.calls[0];
    expect(actor).toBe('123');
    expect(snapshot).toBe(28720256);
    expect(JSON.parse(globals['${clawevolve_params}'])).toMatchObject({ schemaVersion: 'clawevolve-task/v1',
      taskType: 'workflow_repair', input });
  });
});
