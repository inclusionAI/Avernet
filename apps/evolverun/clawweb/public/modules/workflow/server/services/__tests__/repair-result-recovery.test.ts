// @vitest-environment node
import { afterEach, expect, it, vi } from 'vitest';
import { createRepairWorkbenchService } from '../repair-batch-service.js';
import { RepairTaskRepository } from '../../repositories/repair-task-repository.js';
import { digestRepairJson } from '../../contracts/repair-batch.js';
import { repairFixture } from './repair-batch-fixtures.js';

const fixtures: Awaited<ReturnType<typeof repairFixture>>[] = [];
afterEach(async () => { vi.restoreAllMocks(); for (const f of fixtures.splice(0)) await f.db.close(); });
async function setup() {
  const f = await repairFixture(1); fixtures.push(f);
  const service = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
  const inbox = await service.candidates('wf-1');
  const created = await service.create('human', { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: inbox.inputDigest, instructions: '', requestId: 'once' });
  const draft = { candidateCommit: 'd'.repeat(40), parentCandidateCommit: created.input.baseline.packCommit, packDigest: 'e'.repeat(40),
    itemResults: [{ itemId: 'item-0', status: 'changed', reason: 'Fixed' }] };
  f.setGeneratedResult({ status: 'succeeded', draft, checks: { schemaVersion: 'workflow-repair-checks/v1',
    candidateCommit: draft.candidateCommit, packDigest: draft.packDigest, candidateDigest: digestRepairJson(draft),
    static: { status: 'passed' }, mock: { status: 'not_covered' }, coverage: [{ itemId: 'item-0' }] } });
  return { ...f, service, created };
}
it('polls running AIS only, ingests success once, and never revisits a settled execution', async () => {
  const f = await setup();
  const status = vi.spyOn(f.executionPort, 'status'), read = vi.spyOn(f.executionPort, 'readResult');
  expect(await f.service.reconcile()).toBe(0); expect(read).not.toHaveBeenCalled();
  f.setRemoteStatus({ status: 'success', rawStatus: 'success', errorMessage: null });
  expect(await f.service.reconcile()).toBe(1);
  expect(await f.service.reconcile()).toBe(0);
  expect(status).toHaveBeenCalledTimes(2); expect(read).toHaveBeenCalledTimes(1);
});
it('resumes persisted success without polling AIS again after restart', async () => {
  const f = await setup();
  const active = (await new RepairTaskRepository(f.db).active(1))[0];
  await new RepairTaskRepository(f.db).updateRemoteStatus(active.taskId, 1, active.identity, 'success', true);
  const status = vi.spyOn(f.executionPort, 'status').mockRejectedValue(new Error('must not poll'));
  expect(await f.service.reconcile()).toBe(1); expect(status).not.toHaveBeenCalled();
});
it.each(['failed', 'stopped'] as const)('settles %s AIS without fetching Git', async status => {
  const f = await setup(); f.setRemoteStatus({ status, rawStatus: status, errorMessage: 'remote failed' });
  const read = vi.spyOn(f.executionPort, 'readResult');
  expect(await f.service.reconcile()).toBe(1); expect(read).not.toHaveBeenCalled();
  expect((await f.service.task(f.created.taskId)).latestAttempt.error?.code).toBe(status === 'failed' ? 'AIS_FAILED' : 'AIS_STOPPED');
});
it('ends automatic ingestion on an unavailable result and retains explicit same-job recovery', async () => {
  const f = await setup(); f.setRemoteStatus({ status: 'success', rawStatus: 'success', errorMessage: null });
  const read = vi.spyOn(f.executionPort, 'readResult').mockRejectedValueOnce(new Error('private token must not be logged'));
  const log = vi.spyOn(console, 'log').mockImplementation(() => {});
  expect(await f.service.reconcile()).toBe(1);
  expect((await f.service.task(f.created.taskId)).latestAttempt.error?.code).toBe('GIT_RESULT_UNAVAILABLE');
  expect(await f.service.reconcile()).toBe(0); expect(read).toHaveBeenCalledTimes(1);
  await f.service.retryResult('human', f.created.taskId, 1);
  expect((await f.service.task(f.created.taskId)).latestAttempt.phase).toBe('review');
  expect(f.calls).toHaveLength(1);
  expect(JSON.stringify(log.mock.calls)).not.toContain('private token');
});
it('allows only one replica to read a result while another reconciles the same execution', async () => {
  const f = await setup(); f.setRemoteStatus({ status: 'success', rawStatus: 'success', errorMessage: null });
  const other = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
  const original = f.executionPort.readResult;
  let release!: () => void, entered!: () => void;
  const started = new Promise<void>(resolve => { entered = resolve; });
  const pending = new Promise<void>(resolve => { release = resolve; });
  const read = vi.spyOn(f.executionPort, 'readResult').mockImplementation(async request => { entered(); await pending; return original(request); });
  const first = f.service.reconcile(); await started;
  try { expect(await other.reconcile()).toBe(0); expect(read).toHaveBeenCalledTimes(1); }
  finally { release(); }
  expect(await first).toBe(1);
});
it('does not refetch Git after an interrupted result read lease expires', async () => {
  const f = await setup();
  const row = (await f.db.query<{ output_json: string }>('SELECT output_json FROM ce_steps'))[0];
  await f.db.exec('UPDATE ce_steps SET output_json = ?', [JSON.stringify({ ...JSON.parse(row.output_json),
    successObservedAtMs: Date.now() - 1_000_000, resultReadAttemptedAtMs: Date.now() - 1_000_000, resultReadUntilMs: Date.now() - 1 })]);
  const read = vi.spyOn(f.executionPort, 'readResult'), status = vi.spyOn(f.executionPort, 'status');
  expect(await f.service.reconcile()).toBe(1); expect(read).not.toHaveBeenCalled(); expect(status).not.toHaveBeenCalled();
  expect((await f.service.task(f.created.taskId)).latestAttempt.error?.code).toBe('GIT_RESULT_UNAVAILABLE');
});
it('does not claim task success or refetch when the settlement transaction fails', async () => {
  const f = await setup(); f.setRemoteStatus({ status: 'success', rawStatus: 'success', errorMessage: null });
  f.raw.exec("CREATE TRIGGER reject_review BEFORE UPDATE ON workflow_repair_revisions WHEN NEW.phase = 'review' BEGIN SELECT RAISE(FAIL, 'write failed'); END");
  const read = vi.spyOn(f.executionPort, 'readResult');
  await expect(f.service.reconcile()).rejects.toThrow();
  expect((await f.service.task(f.created.taskId)).latestAttempt.phase).toBe('drafting');
  expect(await f.service.reconcile()).toBe(0); expect(read).toHaveBeenCalledTimes(1);
});
it('ends malformed result ingestion explicitly rather than waiting and refetching', async () => {
  const f = await setup(); f.setRemoteStatus({ status: 'success', rawStatus: 'success', errorMessage: null });
  const read = vi.spyOn(f.executionPort, 'readResult').mockResolvedValue({ status: 'succeeded', draft: {}, checks: {} });
  expect(await f.service.reconcile()).toBe(1);
  expect((await f.service.task(f.created.taskId)).latestAttempt.error?.code).toBe('GIT_RESULT_UNAVAILABLE');
  expect(await f.service.reconcile()).toBe(0); expect(read).toHaveBeenCalledTimes(1);
});
it('identifies repeated AIS status failures without logging every poll or provider secrets', async () => {
  const f = await setup();
  vi.spyOn(f.executionPort, 'status').mockRejectedValue(new Error('secret provider payload'));
  const log = vi.spyOn(console, 'info').mockImplementation(() => {});
  await f.service.reconcile(); await f.service.reconcile(); await f.service.reconcile();
  const lines = log.mock.calls.map(([line]) => String(line));
  expect(lines.filter(line => line.includes('AIS_STATUS_UNAVAILABLE'))).toHaveLength(1);
  expect(lines.join('\n')).toContain(f.created.taskId);
  expect(lines.join('\n')).not.toContain('secret provider payload');
});
it('does not time out a stale replica snapshot after another replica persisted success and claimed the result', async () => {
  const f = await setup();
  const row = (await f.db.query<{ output_json: string }>('SELECT output_json FROM ce_steps'))[0];
  await f.db.exec('UPDATE ce_steps SET output_json = ?', [JSON.stringify({ ...JSON.parse(row.output_json), requestedAtMs: Date.now() - 2_401_000 })]);
  let captured!: () => void, resume!: () => void, entered!: () => void, finish!: () => void;
  const capture = new Promise<void>(resolve => { captured = resolve; }), gate = new Promise<void>(resolve => { resume = resolve; });
  const reading = new Promise<void>(resolve => { entered = resolve; }), resultGate = new Promise<void>(resolve => { finish = resolve; });
  const originalTask = f.service.task.bind(f.service), originalRead = f.executionPort.readResult;
  const snapshot = await new RepairTaskRepository(f.db).active(1);
  vi.spyOn(f.service, 'task').mockImplementationOnce(async taskId => {
    captured(); await gate; return originalTask(taskId);
  });
  const first = f.service.reconcile(); await capture;
  await new RepairTaskRepository(f.db).updateRemoteStatus(snapshot[0].taskId, 1, snapshot[0].identity, 'success', true);
  vi.spyOn(f.executionPort, 'readResult').mockImplementation(async request => { entered(); await resultGate; return originalRead(request); });
  const other = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
  const second = other.reconcile().catch(error => error); await reading;
  resume();
  try {
    expect(await first).toBe(0); expect(f.stops).toHaveLength(0);
    expect((await f.service.task(f.created.taskId)).latestAttempt.phase).toBe('drafting');
  } finally { finish(); await second; }
  expect((await f.service.task(f.created.taskId)).latestAttempt.phase).toBe('review');
});
it('logs the committed failure result and distinct read/settlement phases, not AIS success', async () => {
  const f = await setup(); f.setRemoteStatus({ status: 'success', rawStatus: 'success', errorMessage: null });
  f.setGeneratedResult({ status: 'failed', error: { code: 'CANDIDATE_WRITE_FAILED' } });
  const log = vi.spyOn(console, 'info').mockImplementation(() => {});
  expect(await f.service.reconcile()).toBe(1);
  const events = log.mock.calls.flatMap(([line]) => String(line).startsWith('[workflow-background] ')
    ? [JSON.parse(String(line).slice('[workflow-background] '.length))] : []);
  expect(events).toContainEqual(expect.objectContaining({ job: 'repair_result', event: 'settled', outcome: 'failed', status: 'failed', errorCode: 'CANDIDATE_WRITE_FAILED' }));
  expect(events).toContainEqual(expect.objectContaining({ event: 'start', phase: 'result_read', trigger: 'manual' }));
  expect(events).toContainEqual(expect.objectContaining({ event: 'end', phase: 'db_settlement', outcome: 'succeeded' }));
});
it('fences late AIS success after timeout is claimed and resumes a crashed timeout claimant', async () => {
  const f = await setup(), now = Date.now();
  const row = (await f.db.query<{ output_json: string }>('SELECT output_json FROM ce_steps'))[0];
  await f.db.exec('UPDATE ce_steps SET output_json = ?', [JSON.stringify({ ...JSON.parse(row.output_json), requestedAtMs: now - 2_401_000 })]);
  const repo = new RepairTaskRepository(f.db), active = (await repo.active(1))[0];
  expect(await repo.claimTimeout(active.taskId, active.identity)).toBe(true);
  expect(await repo.updateRemoteStatus(active.taskId, 1, active.identity, 'success', true)).toBe(false);
  expect(await f.service.reconcile()).toBe(0);
  vi.spyOn(Date, 'now').mockReturnValue(now + 121_000);
  expect(await f.service.reconcile()).toBe(1);
  expect(f.stops).toHaveLength(1);
  expect((await f.service.task(f.created.taskId)).latestAttempt.error?.code).toBe('AIS_TIMEOUT');
});
