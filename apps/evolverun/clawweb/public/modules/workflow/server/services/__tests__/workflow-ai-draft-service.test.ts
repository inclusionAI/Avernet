import { describe, expect, it, vi } from 'vitest';
import { createWorkflowAiDraftService } from '../workflow-ai-draft-service.js';
import { WorkflowAiDraftDispatchRejectedError, WorkflowAiDraftInvalidResultError } from '../../contracts/workflow-ai-draft.js';
import { aiDraftSha256 } from '../workflow-ai-draft-validation.js';
import { coreFixture, draftRequest, overlayYaml, targetBotId } from './workflow-ai-draft-core-fixtures.js';

describe('Receipt-backed workflow AI draft service (no DB)', () => {
  it('prepares without dispatch, then freezes exact input/native ID/server Bot across instances', async () => {
    const f = coreFixture(), { request, prepared, context } = await f.prepare();
    expect(prepared.inputSpecDigest).toBe(aiDraftSha256(overlayYaml));
    expect(f.execution.dispatch).not.toHaveBeenCalled();
    vi.mocked(f.execution.resolveTarget).mockResolvedValue({ botId: 'replacement:another-owner' });
    expect(await f.service.create('owner', 'flow', request)).toEqual({ draftId: prepared.draftId, status: 'running' });
    expect(f.execution.dispatch).toHaveBeenCalledWith({ actorId: 'owner', envelope: {
      schemaVersion: 'workflow-yaml-draft/v1', taskType: 'workflow_yaml_draft', taskId: prepared.draftId, target: { botId: targetBotId },
      input: { schemaVersion: 'workflow-yaml-draft-input/v1', workflowId: 'flow', mode: request.mode, prompt: request.prompt,
        workingCopy: { yaml: overlayYaml, sha256: aiDraftSha256(overlayYaml) } },
    } });
    f.setResult({ yaml: overlayYaml, summary: 'Candidate' });
    expect(await createWorkflowAiDraftService(f.options).get('owner', 'flow', prepared.draftId, context)).toMatchObject({ status: 'completed' });
    expect(f.execution.poll).toHaveBeenCalledWith({ actorId: 'owner', jobId: prepared.draftId, envelope: {
      schemaVersion: 'workflow-yaml-draft/v1', taskType: 'workflow_yaml_draft', taskId: prepared.draftId, target: { botId: targetBotId },
    } });
    expect(f.execution.resolveTarget).toHaveBeenCalledTimes(1);
    expect(f.options.canEdit).toHaveBeenCalledWith('flow', 'owner');
    expect(f.execution.dispatch).toHaveBeenCalledTimes(1);
  });
  it('never accesses even a legacy database property', async () => {
    const f = coreFixture(), db = new Proxy({}, { get() { throw new Error('private database access'); } });
    const options = { ...f.options, db }, service = createWorkflowAiDraftService(options), request = draftRequest();
    expect(await service.capabilities('owner', 'flow')).toEqual({ available: true });
    const p = await service.prepare('owner', 'flow', request);
    await service.create('owner', 'flow', { ...request, receipt: p.receipt });
    await service.get('owner', 'flow', p.draftId, { receipt: p.receipt, currentYaml: request.currentYaml });
    await service.cancel('owner', 'flow', p.draftId, { receipt: p.receipt });
    expect(await service.reconcile()).toBe(0);
  });
  it('polls the expected native ID after lost acknowledgement without another create', async () => {
    const f = coreFixture(), { request, prepared, context } = await f.prepare();
    vi.mocked(f.execution.dispatch).mockRejectedValue(new Error('private provider token=secret'));
    expect(await f.service.create('owner', 'flow', request)).toEqual({ draftId: prepared.draftId, status: 'dispatch_uncertain' });
    f.setResult({ yaml: overlayYaml, summary: 'Accepted remotely' });
    expect(await createWorkflowAiDraftService(f.options).get('owner', 'flow', prepared.draftId, context)).toMatchObject({ status: 'completed' });
    expect(f.execution.poll).toHaveBeenCalledWith(expect.objectContaining({ jobId: prepared.draftId }));
    expect(f.execution.dispatch).toHaveBeenCalledTimes(1);
  });
  it.each(['AUTO_HEAL_BOT_UNAVAILABLE', 'BAAS_AUTH_FAILED', 'BAAS_REQUEST_REJECTED'] as const)(
    'returns safe explicit rejection %s without pretending durable failure', async code => {
      const f = coreFixture(), { request, prepared, context } = await f.prepare();
      vi.mocked(f.execution.dispatch).mockRejectedValue(new WorkflowAiDraftDispatchRejectedError(code));
      expect(await f.service.create('owner', 'flow', request)).toEqual({ draftId: prepared.draftId, status: 'failed', error: code });
      expect(await f.service.get('owner', 'flow', prepared.draftId, context)).toMatchObject({ status: 'running' });
    });
  it.each(['', 'different-id', 'bad\njob', '\ud800'])('requires ack to equal native ID: %j', async jobId => {
    const f = coreFixture(), { request, prepared, context } = await f.prepare();
    vi.mocked(f.execution.dispatch).mockResolvedValue({ jobId });
    expect(await f.service.create('owner', 'flow', request)).toEqual({ draftId: prepared.draftId, status: 'dispatch_uncertain' });
    await f.service.get('owner', 'flow', prepared.draftId, context);
    expect(f.execution.poll).toHaveBeenCalledWith(expect.objectContaining({ jobId: prepared.draftId }));
    expect(f.execution.dispatch).toHaveBeenCalledTimes(1);
  });
  it('requires a valid receipt on create/get/cancel before calling provider', async () => {
    const f = coreFixture(), { request, prepared, context } = await f.prepare();
    for (const receipt of [undefined, '', prepared.receipt + 'x', 'x'.repeat(4097)]) {
      await expect(f.service.create('owner', 'flow', { ...request, receipt })).rejects.toMatchObject({ code: 'INVALID_INPUT' });
      await expect(f.service.get('owner', 'flow', prepared.draftId, { ...context, receipt: receipt! })).rejects.toMatchObject({ code: 'INVALID_INPUT' });
      await expect(f.service.cancel('owner', 'flow', prepared.draftId, { receipt: receipt! })).rejects.toMatchObject({ code: 'INVALID_INPUT' });
    }
    expect(f.execution.dispatch).not.toHaveBeenCalled(); expect(f.execution.poll).not.toHaveBeenCalled();
  });
  it.each([{ prompt: 'Changed' }, { mode: 'create' as const }, { requestId: 'a32f818e-a44f-46ac-9396-9ed9fda1ca6b' },
    { currentYaml: overlayYaml.replace('Unsaved', 'Changed') }])('binds create to exact input: %j', async change => {
    const f = coreFixture(), { request } = await f.prepare();
    await expect(f.service.create('owner', 'flow', { ...request, ...change })).rejects.toMatchObject({ code: 'IDEMPOTENCY_CONFLICT' });
    expect(f.execution.dispatch).not.toHaveBeenCalled();
  });
  it('rejects substituted poll YAML or draft ID', async () => {
    const f = coreFixture(), { prepared, context } = await f.prepare();
    await expect(f.service.get('owner', 'flow', prepared.draftId, { ...context, currentYaml: overlayYaml + '\n' })).rejects.toMatchObject({ code: 'IDEMPOTENCY_CONFLICT' });
    await expect(f.service.get('owner', 'flow', draftRequest().requestId, context)).rejects.toMatchObject({ code: 'NOT_FOUND' });
    expect(f.execution.poll).not.toHaveBeenCalled();
  });
  it('isolates actor/workflow and checks live permission on every operation', async () => {
    const f = coreFixture(), { request, prepared, context } = await f.prepare();
    for (const [actor, workflow] of [['other-editor', 'flow'], ['owner', 'other-flow']]) {
      await expect(f.service.create(actor, workflow, request)).rejects.toBeDefined();
      await expect(f.service.get(actor, workflow, prepared.draftId, context)).rejects.toMatchObject({ code: 'NOT_FOUND' });
      await expect(f.service.cancel(actor, workflow, prepared.draftId, { receipt: context.receipt })).rejects.toMatchObject({ code: 'NOT_FOUND' });
    }
    f.revoke();
    for (const run of [() => f.service.capabilities('owner', 'flow'), () => f.service.prepare('owner', 'flow', draftRequest()),
      () => f.service.create('owner', 'flow', request), () => f.service.get('owner', 'flow', prepared.draftId, context),
      () => f.service.cancel('owner', 'flow', prepared.draftId, { receipt: context.receipt })]) await expect(run()).rejects.toMatchObject({ code: 'FORBIDDEN' });
    expect(f.execution.dispatch).not.toHaveBeenCalled(); expect(f.execution.poll).not.toHaveBeenCalled();
  });
  it.each(['', ' ', '*', 'unknown', ' owner', 'owner\n', '\ud800'])('rejects invalid actor %j', async actor => {
    const f = coreFixture(); await expect(f.service.prepare(actor, 'flow', draftRequest())).rejects.toMatchObject({ code: 'FORBIDDEN' });
    expect(f.execution.resolveTarget).not.toHaveBeenCalled();
  });
  it('rechecks permission after slow target resolution', async () => {
    const f = coreFixture(); vi.mocked(f.execution.resolveTarget).mockImplementation(async () => { f.revoke(); return { botId: targetBotId }; });
    await expect(f.service.prepare('owner', 'flow', draftRequest())).rejects.toMatchObject({ code: 'FORBIDDEN' });
    expect(f.execution.dispatch).not.toHaveBeenCalled();
  });
  it('cancel is local acknowledgement only; later GET may still complete', async () => {
    const f = coreFixture(), { prepared, context } = await f.prepare();
    expect(await f.service.cancel('owner', 'flow', prepared.draftId, { receipt: context.receipt })).toMatchObject({ status: 'cancelled' });
    expect(f.execution.poll).not.toHaveBeenCalled(); expect(f.execution.dispatch).not.toHaveBeenCalled();
    f.setResult({ yaml: overlayYaml, summary: 'Late' });
    expect(await createWorkflowAiDraftService(f.options).get('owner', 'flow', prepared.draftId, context)).toMatchObject({ status: 'completed' });
  });
  it('times out at receipt deadline without dispatch or poll', async () => {
    const f = coreFixture(), { request, prepared, context } = await f.prepare(); f.advance(30 * 60_000);
    expect(await f.service.create('owner', 'flow', request)).toMatchObject({ status: 'failed', error: 'EXECUTION_TIMEOUT' });
    expect(await f.service.get('owner', 'flow', prepared.draftId, context)).toMatchObject({ status: 'failed', error: 'EXECUTION_TIMEOUT' });
    expect(f.execution.dispatch).not.toHaveBeenCalled(); expect(f.execution.poll).not.toHaveBeenCalled();
  });
  it('treats 404/transport errors as running and later recovers', async () => {
    const f = coreFixture(), { prepared, context } = await f.prepare();
    vi.mocked(f.execution.poll).mockRejectedValueOnce(Object.assign(new Error('private-token'), { status: 404 })).mockRejectedValueOnce(new Error('private-token'));
    for (let i = 0; i < 2; i++) expect(await f.service.get('owner', 'flow', prepared.draftId, context)).toMatchObject({ status: 'running' });
    f.setResult({ yaml: overlayYaml, summary: 'Recovered' });
    expect(await f.service.get('owner', 'flow', prepared.draftId, context)).toMatchObject({ status: 'completed' });
    expect(f.execution.dispatch).not.toHaveBeenCalled();
  });
  it.each(['BAAS_AUTH_FAILED', 'BAAS_GENERATION_FAILED', 'BAAS_PROVIDER_REJECTED', 'BAAS_EXECUTION_FAILED', 'SECRET_UPPERCASE', 'secret=hidden'])(
    'sanitizes provider failure: %s', async error => {
      const f = coreFixture(), { prepared, context } = await f.prepare(); vi.mocked(f.execution.poll).mockResolvedValue({ status: 'failed', error });
      const detail = await f.service.get('owner', 'flow', prepared.draftId, context);
      expect(detail).toMatchObject({ status: 'failed', error: error.startsWith('BAAS_') ? error : 'GENERATION_FAILED' });
      expect(detail.result).toBeUndefined();
    });
  it('sanitizes invalid-result exceptions and malformed provider statuses', async () => {
    const f = coreFixture(), { prepared, context } = await f.prepare();
    vi.mocked(f.execution.poll).mockRejectedValueOnce(new WorkflowAiDraftInvalidResultError('private-secret'));
    expect(await f.service.get('owner', 'flow', prepared.draftId, context)).toMatchObject({ status: 'failed', error: 'INVALID_GENERATION_RESULT' });
    vi.mocked(f.execution.poll).mockResolvedValue({ status: 'unknown' } as never);
    expect(await f.service.get('owner', 'flow', prepared.draftId, context)).toMatchObject({ status: 'failed', error: 'INVALID_GENERATION_RESULT' });
  });
  it('preserves large UTF8 YAML and enforces byte/prompt/schema limits', async () => {
    const f = coreFixture(), large = overlayYaml + 'description: ' + '大'.repeat(20_000) + '\n';
    const { request, prepared, context } = await f.prepare({ ...draftRequest(), currentYaml: large });
    await f.service.create('owner', 'flow', request); f.setResult({ yaml: large, summary: 'Full YAML' });
    expect((await f.service.get('owner', 'flow', prepared.draftId, context)).result?.yaml).toBe(large);
    for (const change of [{ currentYaml: overlayYaml + 'description: ' + '大'.repeat(40_000) }, { prompt: 'x'.repeat(4001) },
      { currentYaml: overlayYaml.replace('id: flow', 'id: other') }, { currentYaml: 'id: flow\nnodes: []' }, { requestId: 'not-a-uuid' },
      { currentYaml: overlayYaml + '\ud800' }]) await expect(f.service.prepare('owner', 'flow', { ...draftRequest(), ...change })).rejects.toBeDefined();
  });
  it.each([
    { yaml: overlayYaml.replace('version: 7', 'version: 8'), summary: 'Changed version' },
    { yaml: overlayYaml.replace('version: 7', 'version: "7"'), summary: 'Changed version type' },
    { yaml: overlayYaml.replace('id: flow', 'id: other'), summary: 'Changed identity' },
    { yaml: 'id: flow\nversion: 7\nnodes: []\n', summary: 'Invalid schema' },
    { yaml: overlayYaml, summary: '\ud800' }, { yaml: overlayYaml, summary: 'x'.repeat(4001) }, { yaml: overlayYaml, summary: 'nul\0summary' },
  ])('rejects invalid output: $summary', async result => {
    const f = coreFixture(), { prepared, context } = await f.prepare(); f.setResult(result);
    expect(await f.service.get('owner', 'flow', prepared.draftId, context)).toMatchObject({ status: 'failed', error: 'INVALID_GENERATION_RESULT' });
  });
  it('requires receipt/provider wiring and resolves only server readiness, never SQL', async () => {
    const f = coreFixture(), disabled = createWorkflowAiDraftService({ ...f.options, receipts: undefined });
    expect(await disabled.capabilities('owner', 'flow')).toEqual({ available: false, reason: 'AI_DRAFT_RECOVERY_UNAVAILABLE' });
    await expect(disabled.prepare('owner', 'flow', draftRequest())).rejects.toMatchObject({ code: 'CAPABILITY_UNAVAILABLE', message: 'AI_DRAFT_RECOVERY_UNAVAILABLE' });
    const noProvider = createWorkflowAiDraftService({ ...f.options, execution: undefined, unavailableReason: 'AUTO_HEAL_DISABLED' });
    expect(await noProvider.capabilities('owner', 'flow')).toEqual({ available: false, reason: 'AUTO_HEAL_DISABLED' });
    vi.mocked(f.execution.resolveTarget).mockRejectedValue(new Error('https://private?token=secret'));
    expect(await f.service.capabilities('owner', 'flow')).toEqual({ available: false, reason: 'AUTO_HEAL_BOT_UNAVAILABLE' });
    await expect(f.service.prepare('owner', 'flow', draftRequest())).rejects.toMatchObject({ code: 'CAPABILITY_UNAVAILABLE', message: 'AUTO_HEAL_BOT_UNAVAILABLE' });
  });
  it.each(['', ' ', ' bot:owner', 'bot:owner\n', '\ud800'])('refuses invalid server binding %j', async botId => {
    const f = coreFixture(); vi.mocked(f.execution.resolveTarget).mockResolvedValue({ botId });
    expect(await f.service.capabilities('owner', 'flow')).toEqual({ available: false, reason: 'AUTO_HEAL_BOT_UNAVAILABLE' });
    await expect(f.service.prepare('owner', 'flow', draftRequest())).rejects.toMatchObject({ code: 'CAPABILITY_UNAVAILABLE' });
  });
  it('does not honor browser-provided target/authority fields', async () => {
    const f = coreFixture(), request = { ...draftRequest(), botId: 'attacker', target: { botId: 'attacker' } };
    const p = await f.service.prepare('owner', 'flow', request); await f.service.create('owner', 'flow', { ...request, receipt: p.receipt });
    expect(vi.mocked(f.execution.dispatch).mock.calls[0][0].envelope.target).toEqual({ botId: targetBotId });
  });
});

it('never emits signing secrets, provider diagnostics, prompts or YAML to logs/errors', async () => {
  const logs = ['log', 'error', 'warn', 'info', 'debug'].map(method => vi.spyOn(console, method as 'log').mockImplementation(() => {}));
  try {
    const f = coreFixture(), { request, prepared, context } = await f.prepare();
    vi.mocked(f.execution.dispatch).mockRejectedValue(new Error('private-token-secret ' + overlayYaml));
    expect(await f.service.create('owner', 'flow', request)).toEqual({ draftId: prepared.draftId, status: 'dispatch_uncertain' });
    vi.mocked(f.execution.poll).mockRejectedValue(new WorkflowAiDraftInvalidResultError('private-token-secret ' + overlayYaml));
    const detail = await f.service.get('owner', 'flow', prepared.draftId, context);
    expect(detail).toMatchObject({ status: 'failed', error: 'INVALID_GENERATION_RESULT' });
    expect(JSON.stringify(detail)).not.toContain('private-token-secret');
    expect(JSON.stringify(detail)).not.toContain(overlayYaml);
    for (const log of logs) expect(log).not.toHaveBeenCalled();
  } finally { logs.forEach(log => log.mockRestore()); }
});
