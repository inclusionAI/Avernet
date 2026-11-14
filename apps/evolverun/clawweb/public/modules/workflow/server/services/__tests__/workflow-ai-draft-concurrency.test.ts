import { afterEach, describe, expect, it, vi } from 'vitest';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { WorkflowAiDraftDispatchRejectedError, type WorkflowAiDraftPollResult } from '../../contracts/workflow-ai-draft.js';
import { WorkflowAiDraftRepository } from '../../repositories/workflow-ai-draft-repository.js';
import { createWorkflowAiDraftService } from '../workflow-ai-draft-service.js';
import { aiDraftFixture, draftRequest, overlayYaml } from './workflow-ai-draft-fixtures.js';
const closing: Array<() => Promise<void>> = [];
afterEach(async () => { for (const close of closing.splice(0)) await close(); });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(yes => { resolve = yes; });
  return { promise, resolve };
}
async function fixture() {
  const f = await aiDraftFixture(); closing.push(() => f.db.close());
  let clock = 1_000_000;
  const options = { db: f.db, execution: f.execution, now: () => clock, canEdit: vi.fn(async () => true) };
  return { ...f, options, service: createWorkflowAiDraftService(options), advance: (ms: number) => { clock += ms; } };
}
const completed: WorkflowAiDraftPollResult = { status: 'completed', result: { yaml: overlayYaml, summary: 'Candidate' } };
describe('Workflow YAML draft durable concurrency fences', () => {
  it('atomically arbitrates concurrent duplicate requests across service instances', async () => {
    const f = await fixture(), request = draftRequest();
    const results = await Promise.all(Array.from({ length: 10 }, () => createWorkflowAiDraftService(f.options).create('owner', 'flow', request)));
    expect(new Set(results.map(x => x.draftId)).size).toBe(1);
    expect(f.execution.dispatch).toHaveBeenCalledTimes(1);
    expect(f.raw.prepare('SELECT COUNT(*) AS n FROM workflow_ai_drafts').get()).toEqual({ n: 1 });
  });
  it('persists the CAS uncertain fence before calling dispatch', async () => {
    const f = await fixture();
    let observed: unknown;
    vi.mocked(f.execution.dispatch).mockImplementation(async () => {
      observed = f.raw.prepare('SELECT status, state_version, job_id FROM workflow_ai_drafts').get();
      return { jobId: 'job' };
    });
    expect(await f.service.create('owner', 'flow', draftRequest())).toMatchObject({ status: 'running' });
    expect(observed).toEqual({ status: 'dispatch_uncertain', state_version: 1, job_id: null });
    expect(f.execution.dispatch).toHaveBeenCalledTimes(1);
  });
  it('never dispatches when the durable fence cannot be persisted', async () => {
    const f = await fixture();
    const spy = vi.spyOn(WorkflowAiDraftRepository.prototype, 'update').mockResolvedValue(false);
    try {
      expect(await f.service.create('owner', 'flow', draftRequest())).toMatchObject({ status: 'pending' });
      expect(f.execution.dispatch).not.toHaveBeenCalled();
    } finally { spy.mockRestore(); }
  });
  it('recovers persisted pending work once with its original frozen target', async () => {
    const f = await fixture(), request = draftRequest();
    const spy = vi.spyOn(WorkflowAiDraftRepository.prototype, 'update').mockResolvedValue(false);
    let task: Awaited<ReturnType<typeof f.service.create>>;
    try { task = await f.service.create('owner', 'flow', request); } finally { spy.mockRestore(); }
    expect(task!.status).toBe('pending');
    vi.mocked(f.execution.resolveTarget).mockResolvedValue({ botId: 'changed:owner' });
    await Promise.all(Array.from({ length: 5 }, () => createWorkflowAiDraftService(f.options).reconcile()));
    expect(f.execution.dispatch).toHaveBeenCalledTimes(1);
    expect(vi.mocked(f.execution.dispatch).mock.calls[0][0].envelope.target).toEqual({ botId: 'auto-heal-bot:bot-owner' });
    expect(f.execution.resolveTarget).toHaveBeenCalledTimes(1);
  });
  it('claims a due poll once across instances and repeated same-clock GETs cannot bypass backoff', async () => {
    const f = await fixture(), task = await f.service.create('owner', 'flow', draftRequest());
    const services = Array.from({ length: 10 }, () => createWorkflowAiDraftService(f.options));
    await Promise.all(services.map(service => service.get('owner', 'flow', task.draftId)));
    expect(f.execution.poll).not.toHaveBeenCalled();
    f.advance(16_000);
    await Promise.all(services.map(service => service.get('owner', 'flow', task.draftId)));
    expect(f.execution.poll).toHaveBeenCalledTimes(1);
    expect(f.raw.prepare('SELECT state_version FROM workflow_ai_drafts').get()).toEqual({ state_version: 2 });
    await Promise.all(services.map(service => service.get('owner', 'flow', task.draftId)));
    expect(f.execution.poll).toHaveBeenCalledTimes(1);
    f.advance(16_000); await f.service.reconcile();
    expect(f.execution.poll).toHaveBeenCalledTimes(2);
  });
  it('does not invalidate an in-flight valid result when a later poll claims the due clock', async () => {
    const f = await fixture(), task = await f.service.create('owner', 'flow', draftRequest());
    const reply = deferred<WorkflowAiDraftPollResult>();
    vi.mocked(f.execution.poll).mockImplementationOnce(() => reply.promise);
    f.advance(16_000);
    const first = f.service.get('owner', 'flow', task.draftId);
    await vi.waitFor(() => expect(f.execution.poll).toHaveBeenCalledTimes(1));
    f.advance(16_000);
    expect(await createWorkflowAiDraftService(f.options).get('owner', 'flow', task.draftId)).toMatchObject({ status: 'running' });
    expect(f.execution.poll).toHaveBeenCalledTimes(2);
    expect(f.raw.prepare('SELECT state_version FROM workflow_ai_drafts').get()).toEqual({ state_version: 2 });
    reply.resolve(completed);
    expect(await first).toMatchObject({ status: 'completed', result: completed.result });
    expect(f.raw.prepare('SELECT state_version FROM workflow_ai_drafts').get()).toEqual({ state_version: 3 });
  });
  it.each(['acknowledged', 'rejected', 'unknown'] as const)(
    'ignores a late dispatch response after local cancellation: %s', async outcome => {
      const f = await fixture(), reply = deferred<void>();
      vi.mocked(f.execution.dispatch).mockImplementation(async () => {
        await reply.promise;
        if (outcome === 'rejected') throw new WorkflowAiDraftDispatchRejectedError('AUTO_HEAL_BOT_UNAVAILABLE');
        if (outcome === 'unknown') throw new Error('token=secret');
        return { jobId: 'late-job' };
      });
      const creating = f.service.create('owner', 'flow', draftRequest());
      await vi.waitFor(() => expect(f.execution.dispatch).toHaveBeenCalledTimes(1));
      const row = f.raw.prepare('SELECT draft_id FROM workflow_ai_drafts').get() as { draft_id: string };
      expect(await f.service.cancel('owner', 'flow', row.draft_id)).toMatchObject({ status: 'cancelled' });
      reply.resolve();
      expect(await creating).toMatchObject({ status: 'cancelled' });
      expect(f.raw.prepare('SELECT status, job_id, error_code, result_json FROM workflow_ai_drafts').get())
        .toEqual({ status: 'cancelled', job_id: null, error_code: null, result_json: null });
    });
  it.each(['completed', 'failed', 'invalid'] as const)('ignores a late poll response after cancellation: %s', async outcome => {
    const f = await fixture(), task = await f.service.create('owner', 'flow', draftRequest());
    const reply = deferred<WorkflowAiDraftPollResult>(); vi.mocked(f.execution.poll).mockImplementation(() => reply.promise);
    f.advance(16_000);
    const reading = f.service.get('owner', 'flow', task.draftId);
    await vi.waitFor(() => expect(f.execution.poll).toHaveBeenCalledTimes(1));
    await f.service.cancel('owner', 'flow', task.draftId);
    reply.resolve(outcome === 'failed' ? { status: 'failed', error: 'BAAS_EXECUTION_FAILED' } : outcome === 'invalid'
      ? { status: 'completed', result: { yaml: 'invalid', summary: 'Invalid' } } : completed);
    expect(await reading).toMatchObject({ status: 'cancelled' });
    expect(f.raw.prepare('SELECT status, error_code, result_json FROM workflow_ai_drafts').get())
      .toEqual({ status: 'cancelled', error_code: null, result_json: null });
  });
  it('cannot revive a task when a dispatch acknowledgement arrives after its uncertain deadline', async () => {
    const f = await fixture(), reply = deferred<{ jobId: string }>();
    vi.mocked(f.execution.dispatch).mockImplementation(() => reply.promise);
    const creating = f.service.create('owner', 'flow', draftRequest());
    await vi.waitFor(() => expect(f.execution.dispatch).toHaveBeenCalledTimes(1));
    f.advance(31 * 60_000); await f.service.reconcile();
    reply.resolve({ jobId: 'too-late' });
    const task = await creating;
    expect(await f.service.get('owner', 'flow', task.draftId)).toMatchObject({ status: 'failed', error: 'DISPATCH_UNCERTAIN_TIMEOUT' });
    expect(f.execution.poll).not.toHaveBeenCalled();
  });
  it('bounds concurrent provider calls while advancing independent drafts', async () => {
    const f = await fixture();
    const tasks = await Promise.all(Array.from({ length: 12 }, () => f.service.create('owner', 'flow', draftRequest())));
    let active = 0, peak = 0;
    const replies: Array<ReturnType<typeof deferred<WorkflowAiDraftPollResult>>> = [];
    vi.mocked(f.execution.poll).mockImplementation(async () => {
      active++; peak = Math.max(peak, active);
      const reply = deferred<WorkflowAiDraftPollResult>(); replies.push(reply);
      try { return await reply.promise; } finally { active--; }
    });
    f.advance(16_000);
    const reconciling = f.service.reconcile(12);
    for (const count of [4, 8, 12]) {
      await vi.waitFor(() => expect(replies).toHaveLength(count));
      expect(active).toBe(4);
      for (const reply of replies.slice(count - 4, count)) reply.resolve(completed);
    }
    expect(await reconciling).toBe(12);
    expect(peak).toBe(4); expect(active).toBe(0);
    for (const task of tasks) expect(await f.service.get('owner', 'flow', task.draftId)).toMatchObject({ status: 'completed' });
  });
  it('lets other drafts complete but surfaces a reconciliation persistence failure', async () => {
    const f = await fixture();
    const tasks = await Promise.all(Array.from({ length: 3 }, () => f.service.create('owner', 'flow', draftRequest())));
    const broken = { ...f.db, dbType: f.db.dbType, dialect: f.db.dialect, query: f.db.query.bind(f.db),
      exec: vi.fn(async (sql: string, params?: unknown[]) => {
        if (sql.includes('result_json') && params?.includes(tasks[0].draftId)) throw new Error('write failed');
        return f.db.exec(sql, params);
      }) } as unknown as IDatabase;
    f.setResult(completed.result); f.advance(16_000);
    await expect(createWorkflowAiDraftService({ ...f.options, db: broken }).reconcile()).rejects.toThrow('AI_DRAFT_RECONCILIATION_FAILED');
    expect(f.raw.prepare('SELECT status FROM workflow_ai_drafts WHERE draft_id = ?').get(tasks[0].draftId)).toEqual({ status: 'running' });
    for (const task of tasks.slice(1)) expect(await f.service.get('owner', 'flow', task.draftId)).toMatchObject({ status: 'completed' });
  });
  it.each([0, -1, 101, 1.5, Number.NaN])('rejects an unbounded or invalid reconciliation limit: %s', async limit => {
    const f = await fixture();
    await expect(f.service.reconcile(limit)).rejects.toMatchObject({ code: 'INVALID_INPUT' });
    expect(f.execution.poll).not.toHaveBeenCalled();
  });
});

describe('Draft authorization and deadlines across asynchronous recovery', () => {
  it.each(['get', 'monitor'] as const)('rejects a valid reply when edit permission is revoked during %s polling', async operation => {
    const f = await fixture(), task = await f.service.create('owner', 'flow', draftRequest());
    const reply = deferred<WorkflowAiDraftPollResult>();
    vi.mocked(f.execution.poll).mockImplementation(() => reply.promise);
    f.advance(16_000);
    const pending = operation === 'get' ? f.service.get('owner', 'flow', task.draftId) : f.service.reconcile();
    const outcome = operation === 'get' ? expect(pending).rejects.toMatchObject({ code: 'FORBIDDEN' }) : expect(pending).resolves.toBe(1);
    await vi.waitFor(() => expect(f.execution.poll).toHaveBeenCalledOnce());
    f.options.canEdit.mockResolvedValue(false);
    reply.resolve(completed); await outcome;
    expect(f.raw.prepare('SELECT status, error_code, result_json FROM workflow_ai_drafts').get())
      .toEqual({ status: 'failed', error_code: 'EDIT_PERMISSION_REVOKED', result_json: null });
  });
  it.each(['get', 'cancel'] as const)('rechecks the live grant before returning an already-completed result through %s', async operation => {
    const f = await fixture(), task = await f.service.create('owner', 'flow', draftRequest());
    vi.mocked(f.execution.poll).mockResolvedValue(completed); f.advance(16_000);
    await f.service.get('owner', 'flow', task.draftId);
    f.options.canEdit.mockResolvedValueOnce(true).mockResolvedValue(false);
    await expect(f.service[operation]('owner', 'flow', task.draftId)).rejects.toMatchObject({ code: 'FORBIDDEN' });
  });
  it.each(['replay', 'monitor'] as const)('expires old pending work before a new BaaS dispatch on %s', async operation => {
    const f = await fixture(), input = draftRequest();
    const spy = vi.spyOn(WorkflowAiDraftRepository.prototype, 'update').mockResolvedValue(false);
    let task: Awaited<ReturnType<typeof f.service.create>>;
    try { task = await f.service.create('owner', 'flow', input); } finally { spy.mockRestore(); }
    expect(task!.status).toBe('pending'); f.advance(31 * 60_000);
    if (operation === 'replay') expect(await f.service.create('owner', 'flow', input)).toMatchObject({ status: 'failed' });
    else await f.service.reconcile();
    expect(await f.service.get('owner', 'flow', task!.draftId)).toMatchObject({ status: 'failed', error: 'EXECUTION_TIMEOUT' });
    expect(f.execution.dispatch).not.toHaveBeenCalled(); expect(f.execution.poll).not.toHaveBeenCalled();
  });
});

it('does not accept a reply that completes after the task deadline during polling', async () => {
  const f = await fixture(), task = await f.service.create('owner', 'flow', draftRequest());
  const reply = deferred<WorkflowAiDraftPollResult>(); vi.mocked(f.execution.poll).mockImplementation(() => reply.promise);
  f.advance(16_000); const pending = f.service.get('owner', 'flow', task.draftId);
  await vi.waitFor(() => expect(f.execution.poll).toHaveBeenCalledOnce());
  f.advance(31 * 60_000); reply.resolve(completed);
  expect(await pending).toMatchObject({ status: 'failed', error: 'EXECUTION_TIMEOUT' });
  expect(f.raw.prepare('SELECT result_json FROM workflow_ai_drafts').get()).toEqual({ result_json: null });
});
