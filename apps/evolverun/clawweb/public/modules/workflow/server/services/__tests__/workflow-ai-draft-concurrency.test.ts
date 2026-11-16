import { describe, expect, it, vi } from 'vitest';
import { createWorkflowAiDraftService } from '../workflow-ai-draft-service.js';
import { createWorkflowAiDraftReceipts } from '../workflow-ai-draft-receipts.js';
import { WorkflowAiDraftPollUnavailableError, type WorkflowAiDraftPollResult } from '../../contracts/workflow-ai-draft.js';
import { coreFixture, deferred, draftRequest, overlayYaml, receiptSecret } from './workflow-ai-draft-core-fixtures.js';
const completed: WorkflowAiDraftPollResult = { status: 'completed', result: { yaml: overlayYaml, summary: 'Candidate' } };
describe('Stateless receipt/native-ID concurrency', () => {
  it('prepares stable jobs across independent signers, instances, and clocks', async () => {
    const f = coreFixture(), request = draftRequest();
    const prepared = await Promise.all(Array.from({ length: 10 }, (_, i) => createWorkflowAiDraftService({ ...f.options,
      receipts: createWorkflowAiDraftReceipts(receiptSecret), now: () => 1_000_000 + i,
    }).prepare('owner', 'flow', { ...request, requestId: i % 2 ? request.requestId.toUpperCase() : request.requestId })));
    expect(new Set(prepared.map(p => p.draftId)).size).toBe(1); expect(f.execution.dispatch).not.toHaveBeenCalled();
    expect((await f.service.prepare('owner', 'flow', { ...request, prompt: request.prompt + ' ' })).draftId).not.toBe(prepared[0].draftId);
  });
  it('uses the same native ID on explicit retries; BaaS owns deduplication, not a process-local fence', async () => {
    const f = coreFixture(), { request, prepared } = await f.prepare();
    const results = await Promise.all(Array.from({ length: 10 }, () => createWorkflowAiDraftService(f.options).create('owner', 'flow', request)));
    expect(results.every(r => r.status === 'running' && r.draftId === prepared.draftId)).toBe(true);
    expect(f.execution.dispatch).toHaveBeenCalledTimes(10);
    expect(new Set(vi.mocked(f.execution.dispatch).mock.calls.map(([r]) => r.envelope.taskId))).toEqual(new Set([prepared.draftId]));
  });
  it('never dispatches from GET/cancel/reconcile, even when prepare had no create', async () => {
    const f = coreFixture(), { prepared, context } = await f.prepare();
    await Promise.all(Array.from({ length: 5 }, async () => {
      const service = createWorkflowAiDraftService(f.options);
      expect(await service.get('owner', 'flow', prepared.draftId, context)).toMatchObject({ status: 'running' });
      expect(await service.cancel('owner', 'flow', prepared.draftId, { receipt: context.receipt })).toMatchObject({ status: 'cancelled' });
      expect(await service.reconcile(10)).toBe(0);
    }));
    expect(f.execution.dispatch).not.toHaveBeenCalled(); expect(f.execution.poll).toHaveBeenCalledTimes(5);
  });
  it('does not pretend local cancel suppresses an in-flight GET on another instance', async () => {
    const f = coreFixture(), { prepared, context } = await f.prepare(), reply = deferred<WorkflowAiDraftPollResult>();
    vi.mocked(f.execution.poll).mockImplementationOnce(() => reply.promise);
    const reading = f.service.get('owner', 'flow', prepared.draftId, context);
    await vi.waitFor(() => expect(f.execution.poll).toHaveBeenCalledOnce());
    expect(await createWorkflowAiDraftService(f.options).cancel('owner', 'flow', prepared.draftId, { receipt: context.receipt })).toMatchObject({ status: 'cancelled' });
    reply.resolve(completed); expect(await reading).toMatchObject({ status: 'completed' });
    expect(f.execution.dispatch).not.toHaveBeenCalled();
  });
  it('rechecks live ACL before accepting an asynchronous poll result', async () => {
    const f = coreFixture(), { prepared, context } = await f.prepare(), reply = deferred<WorkflowAiDraftPollResult>();
    vi.mocked(f.execution.poll).mockImplementationOnce(() => reply.promise);
    const reading = f.service.get('owner', 'flow', prepared.draftId, context), outcome = expect(reading).rejects.toMatchObject({ code: 'FORBIDDEN' });
    await vi.waitFor(() => expect(f.execution.poll).toHaveBeenCalledOnce()); f.revoke(); reply.resolve(completed); await outcome;
  });
  it('rejects valid output arriving after receipt expiry', async () => {
    const f = coreFixture(), { prepared, context } = await f.prepare(), reply = deferred<WorkflowAiDraftPollResult>();
    vi.mocked(f.execution.poll).mockImplementationOnce(() => reply.promise);
    const reading = f.service.get('owner', 'flow', prepared.draftId, context);
    await vi.waitFor(() => expect(f.execution.poll).toHaveBeenCalledOnce()); f.advance(30 * 60_000); reply.resolve(completed);
    const detail = await reading; expect(detail).toMatchObject({ status: 'failed', error: 'EXECUTION_TIMEOUT' }); expect(detail.result).toBeUndefined();
  });
  it('does not accept a slow dispatch acknowledgement after expiry', async () => {
    const f = coreFixture(), { request } = await f.prepare(), reply = deferred<{ jobId: string }>();
    vi.mocked(f.execution.dispatch).mockImplementationOnce(() => reply.promise); const creating = f.service.create('owner', 'flow', request);
    await vi.waitFor(() => expect(f.execution.dispatch).toHaveBeenCalledOnce()); f.advance(30 * 60_000); reply.resolve({ jobId: 'late-ack' });
    expect(await creating).toMatchObject({ status: 'failed', error: 'EXECUTION_TIMEOUT' });
  });
  it('does not expose a slow dispatch acknowledgement to a revoked owner', async () => {
    const f = coreFixture(), { request, prepared } = await f.prepare(), reply = deferred<{ jobId: string }>();
    vi.mocked(f.execution.dispatch).mockImplementationOnce(() => reply.promise);
    const creating = f.service.create('owner', 'flow', request), outcome = expect(creating).rejects.toMatchObject({ code: 'FORBIDDEN' });
    await vi.waitFor(() => expect(f.execution.dispatch).toHaveBeenCalledOnce()); f.revoke(); reply.resolve({ jobId: prepared.draftId }); await outcome;
  });
});

it.each(['BAAS_AUTH_FAILED', 'BAAS_POLL_UNAVAILABLE'] as const)('preserves original native ID after operational poll error %s', async code => {
  const f = coreFixture(), { request, prepared, context } = await f.prepare();
  await f.service.create('owner', 'flow', request);
  vi.mocked(f.execution.poll).mockRejectedValueOnce(new WorkflowAiDraftPollUnavailableError(code)).mockResolvedValueOnce(completed);
  expect(await f.service.get('owner', 'flow', prepared.draftId, context)).toMatchObject({ status: 'running', error: code });
  expect(await createWorkflowAiDraftService(f.options).get('owner', 'flow', prepared.draftId, context)).toMatchObject({ status: 'completed' });
  expect(f.execution.dispatch).toHaveBeenCalledOnce();
  expect(vi.mocked(f.execution.poll).mock.calls.map(([input]) => input.jobId)).toEqual([prepared.draftId, prepared.draftId]);
});
it('does not expose an untrusted operational error code from an injected port', async () => {
  const f = coreFixture(), { prepared, context } = await f.prepare();
  vi.mocked(f.execution.poll).mockResolvedValueOnce({ status: 'running', error: 'PRIVATE_PROVIDER_BODY' });
  expect(await f.service.get('owner', 'flow', prepared.draftId, context)).toMatchObject({ status: 'running', error: 'BAAS_POLL_UNAVAILABLE' });
  expect(f.execution.dispatch).not.toHaveBeenCalled();
});
