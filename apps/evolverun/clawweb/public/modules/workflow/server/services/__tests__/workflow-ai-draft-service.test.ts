import { afterEach, describe, expect, it, vi } from 'vitest';
import { createHash } from 'node:crypto';
import { createWorkflowAiDraftService } from '../workflow-ai-draft-service.js';
import { WorkflowAiDraftDispatchRejectedError, WorkflowAiDraftInvalidResultError } from '../../contracts/workflow-ai-draft.js';
import { WorkflowAiDraftRepository } from '../../repositories/workflow-ai-draft-repository.js';
import { aiDraftFixture, draftRequest, overlayYaml, targetBotId } from './workflow-ai-draft-fixtures.js';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
const closing: Array<() => Promise<void>> = [];
afterEach(async () => { for (const close of closing.splice(0)) await close(); });
async function fixture() {
  const f = await aiDraftFixture(); closing.push(() => f.db.close());
  let clock = 1_000_000, allowed = true;
  const options = { db: f.db, execution: f.execution, now: () => clock, canEdit: vi.fn(async () => allowed) };
  return { ...f, options, service: createWorkflowAiDraftService(options), advance: (ms: number) => { clock += ms; }, revoke: () => { allowed = false; } };
}
describe('Workflow YAML draft service conformance', () => {
  it('freezes the complete Bot identity and exact envelope once without editing or deploying the workflow', async () => {
    const f = await fixture(), request = draftRequest();
    const a = await f.service.create('owner', 'flow', request);
    expect(a.status).toBe('running');
    vi.mocked(f.execution.resolveTarget).mockResolvedValue({ botId: 'replacement:another-owner' });
    const b = await createWorkflowAiDraftService(f.options).create('owner', 'flow', { ...request, requestId: request.requestId.toUpperCase() });
    expect(b).toEqual(a); expect(f.execution.dispatch).toHaveBeenCalledTimes(1);
    expect(f.execution.resolveTarget).toHaveBeenCalledTimes(1);
    const call = vi.mocked(f.execution.dispatch).mock.calls[0][0];
    expect(call).toEqual({ actorId: 'owner', envelope: {
      schemaVersion: 'workflow-yaml-draft/v1', taskType: 'workflow_yaml_draft', taskId: a.draftId, target: { botId: targetBotId },
      input: { schemaVersion: 'workflow-yaml-draft-input/v1', workflowId: 'flow', mode: request.mode, prompt: request.prompt,
        workingCopy: { yaml: overlayYaml, sha256: createHash('sha256').update(overlayYaml).digest('hex') } },
    } });
    const row = await new WorkflowAiDraftRepository(f.db).get(a.draftId);
    expect(JSON.parse(row!.envelope_json)).toEqual(call.envelope);
    await expect(f.service.create('owner', 'flow', { ...request, prompt: 'different' })).rejects.toMatchObject({ code: 'IDEMPOTENCY_CONFLICT' });
    const other = await f.service.create('other-editor', 'flow', request);
    expect(other.draftId).not.toBe(a.draftId);
    expect(f.raw.prepare('SELECT spec_json FROM workflow_specs').get()).toEqual({ spec_json: '{}' });
    expect(f.raw.prepare('SELECT COUNT(*) AS n FROM workflow_deploy_history').get()).toEqual({ n: 0 });
    f.setResult({ yaml: overlayYaml, summary: 'Unchanged is a valid candidate' }); f.advance(16_000);
    expect(await f.service.get('owner', 'flow', a.draftId)).toMatchObject({ status: 'completed' });
    expect(f.execution.poll).toHaveBeenCalledWith({ ...call, jobId: 'job' });
  });
  it('is available for DB-only workflows without Pack metadata or deployment history', async () => {
    const f = await fixture();
    f.raw.exec('UPDATE workflow_specs SET pack_id = NULL');
    expect(await f.service.capabilities('owner', 'flow')).toEqual({ available: true });
    expect(f.execution.resolveTarget).toHaveBeenCalledWith();
    expect(await f.service.create('owner', 'flow', draftRequest())).toMatchObject({ status: 'running' });
  });
  it('keeps an unknown dispatch uncertain across retries, reads and restarts, then times out without polling or resubmitting', async () => {
    const f = await fixture(), request = draftRequest();
    vi.mocked(f.execution.dispatch).mockRejectedValue(new Error('credential-bearing provider error'));
    const task = await f.service.create('owner', 'flow', request);
    expect(task.status).toBe('dispatch_uncertain');
    expect(await f.service.create('owner', 'flow', request)).toEqual(task);
    f.setResult({ yaml: overlayYaml, summary: 'Cannot be fetched without a job ID' });
    f.advance(16_000);
    const restarted = createWorkflowAiDraftService(f.options);
    expect(await restarted.reconcile()).toBe(1);
    expect(await restarted.get('owner', 'flow', task.draftId)).toMatchObject({ status: 'dispatch_uncertain' });
    f.advance(30 * 60_000);
    expect(await restarted.get('owner', 'flow', task.draftId)).toMatchObject({ status: 'failed', error: 'DISPATCH_UNCERTAIN_TIMEOUT' });
    expect(await restarted.create('owner', 'flow', request)).toMatchObject({ draftId: task.draftId, status: 'failed' });
    expect(f.execution.dispatch).toHaveBeenCalledTimes(1);
    expect(f.execution.poll).not.toHaveBeenCalled();
    expect(JSON.stringify(f.raw.prepare('SELECT * FROM workflow_ai_drafts').all())).not.toContain('credential-bearing');
  });
  it.each(['AUTO_HEAL_BOT_UNAVAILABLE', 'BAAS_AUTH_FAILED', 'BAAS_REQUEST_REJECTED'] as const)(
    'terminally records a positive dispatch rejection and never retries it: %s', async code => {
      const f = await fixture(), request = draftRequest();
      vi.mocked(f.execution.dispatch).mockRejectedValue(new WorkflowAiDraftDispatchRejectedError(code));
      const task = await f.service.create('owner', 'flow', request);
      expect(task.status).toBe('failed');
      expect(await f.service.get('owner', 'flow', task.draftId)).toMatchObject({ status: 'failed', error: code });
      f.advance(31 * 60_000); await f.service.reconcile();
      expect(await f.service.create('owner', 'flow', request)).toEqual(task);
      expect(f.execution.dispatch).toHaveBeenCalledTimes(1); expect(f.execution.poll).not.toHaveBeenCalled();
    });
  it.each(['', 'x'.repeat(256), 'invalid\njob', '\ud800'])(
    'treats a malformed acknowledgement as uncertain, never as permission to resubmit', async jobId => {
      const f = await fixture(), request = draftRequest();
      vi.mocked(f.execution.dispatch).mockResolvedValue({ jobId });
      const task = await f.service.create('owner', 'flow', request);
      expect(task.status).toBe('dispatch_uncertain');
      f.advance(16_000); await f.service.get('owner', 'flow', task.draftId); await f.service.create('owner', 'flow', request);
      expect(f.execution.poll).not.toHaveBeenCalled(); expect(f.execution.dispatch).toHaveBeenCalledTimes(1);
    });
  it('requires owner and live workflow edit permission on every operation and background poll', async () => {
    const f = await fixture(), task = await f.service.create('owner', 'flow', draftRequest());
    await expect(f.service.get('other-editor', 'flow', task.draftId)).rejects.toMatchObject({ code: 'NOT_FOUND' });
    await expect(f.service.cancel('other-editor', 'flow', task.draftId)).rejects.toMatchObject({ code: 'NOT_FOUND' });
    await expect(f.service.cancel('owner', 'other-workflow', task.draftId)).rejects.toMatchObject({ code: 'NOT_FOUND' });
    f.revoke();
    for (const operation of [() => f.service.get('owner', 'flow', task.draftId), () => f.service.cancel('owner', 'flow', task.draftId),
      () => f.service.capabilities('owner', 'flow'), () => f.service.create('owner', 'flow', draftRequest())]) {
      await expect(operation()).rejects.toMatchObject({ code: 'FORBIDDEN' });
    }
    f.advance(16_000); await f.service.reconcile();
    expect(f.raw.prepare('SELECT status, error_code FROM workflow_ai_drafts').get()).toEqual({ status: 'failed', error_code: 'EDIT_PERMISSION_REVOKED' });
    expect(f.execution.poll).not.toHaveBeenCalled();
  });
  it('rechecks edit permission after target resolution, before storing or dispatching', async () => {
    const f = await fixture();
    vi.mocked(f.execution.resolveTarget).mockImplementation(async () => { f.revoke(); return { botId: targetBotId }; });
    await expect(f.service.create('owner', 'flow', draftRequest())).rejects.toMatchObject({ code: 'FORBIDDEN' });
    expect(f.raw.prepare('SELECT COUNT(*) AS n FROM workflow_ai_drafts').get()).toEqual({ n: 0 });
    expect(f.execution.dispatch).not.toHaveBeenCalled();
  });
  it('cancels locally and never polls or revives a cancelled task', async () => {
    const f = await fixture(), request = draftRequest(), task = await f.service.create('owner', 'flow', request);
    expect(await f.service.cancel('owner', 'flow', task.draftId)).toMatchObject({ status: 'cancelled' });
    f.setResult({ yaml: overlayYaml, summary: 'Late' }); f.advance(16_000);
    expect(await f.service.get('owner', 'flow', task.draftId)).toMatchObject({ status: 'cancelled' });
    expect(await f.service.create('owner', 'flow', request)).toMatchObject({ status: 'cancelled' });
    expect(await f.service.reconcile()).toBe(0);
    expect(f.execution.poll).not.toHaveBeenCalled(); expect(f.execution.dispatch).toHaveBeenCalledTimes(1);
  });
  it('returns a complete candidate only from a completed provider result', async () => {
    const f = await fixture(), task = await f.service.create('owner', 'flow', draftRequest());
    f.advance(16_000);
    expect(await f.service.get('owner', 'flow', task.draftId)).toMatchObject({ status: 'running' });
    f.setResult({ yaml: overlayYaml.replace('Unsaved', 'Generated'), summary: 'Improved' }); f.advance(16_000);
    expect(await f.service.get('owner', 'flow', task.draftId)).toMatchObject({ status: 'completed', result: { summary: 'Improved' } });
    expect(await f.service.cancel('owner', 'flow', task.draftId)).toMatchObject({ status: 'completed' });
  });
  it.each(['BAAS_EXECUTION_FAILED', 'secret=hidden'])(
    'maps provider failure without fabricating YAML or exposing raw diagnostics: %s', async error => {
      const f = await fixture(), task = await f.service.create('owner', 'flow', draftRequest());
      vi.mocked(f.execution.poll).mockResolvedValue({ status: 'failed', error }); f.advance(16_000);
      const detail = await f.service.get('owner', 'flow', task.draftId);
      expect(detail).toMatchObject({ status: 'failed', error: error === 'secret=hidden' ? 'GENERATION_FAILED' : error });
      expect(detail.result).toBeUndefined();
    });
  it('retries transient poll failures within the execution deadline without resubmitting', async () => {
    const f = await fixture(), task = await f.service.create('owner', 'flow', draftRequest());
    vi.mocked(f.execution.poll).mockRejectedValue(new Error('token=secret')); f.advance(16_000);
    expect(await f.service.get('owner', 'flow', task.draftId)).toMatchObject({ status: 'running' });
    f.advance(30 * 60_000);
    expect(await f.service.get('owner', 'flow', task.draftId)).toMatchObject({ status: 'failed', error: 'EXECUTION_TIMEOUT' });
    expect(f.execution.poll).toHaveBeenCalledTimes(1); expect(f.execution.dispatch).toHaveBeenCalledTimes(1);
  });
  it('terminally rejects invalid provider result evidence', async () => {
    const f = await fixture(), task = await f.service.create('owner', 'flow', draftRequest());
    vi.mocked(f.execution.poll).mockRejectedValue(new WorkflowAiDraftInvalidResultError('private diagnostic')); f.advance(16_000);
    expect(await f.service.get('owner', 'flow', task.draftId)).toMatchObject({ status: 'failed', error: 'INVALID_GENERATION_RESULT' });
  });
  it('keeps full UTF8 YAML beyond 8KB and enforces exact byte/prompt/schema limits', async () => {
    const f = await fixture();
    const large = overlayYaml + 'description: ' + '大'.repeat(20_000) + '\n';
    const task = await f.service.create('owner', 'flow', { ...draftRequest(), currentYaml: large });
    f.setResult({ yaml: large, summary: 'Full YAML' }); f.advance(16_000);
    expect((await f.service.get('owner', 'flow', task.draftId)).result?.yaml).toBe(large);
    for (const change of [{ currentYaml: overlayYaml + 'description: ' + '大'.repeat(40_000) }, { prompt: 'x'.repeat(4001) },
      { currentYaml: overlayYaml.replace('id: flow', 'id: other') }, { currentYaml: 'id: flow\nnodes: []' }, { requestId: 'not-a-uuid' },
      { currentYaml: overlayYaml + '\ud800' }]) await expect(f.service.create('owner', 'flow', { ...draftRequest(), ...change })).rejects.toBeDefined();
  });
  it.each([
    { yaml: overlayYaml.replace('version: 7', 'version: 8'), summary: 'Changed version' },
    { yaml: overlayYaml.replace('version: 7', 'version: "7"'), summary: 'Changed version type' },
    { yaml: overlayYaml.replace('id: flow', 'id: other'), summary: 'Changed identity' },
    { yaml: 'id: flow\nversion: 7\nnodes: []\n', summary: 'Invalid schema' },
    { yaml: overlayYaml, summary: '\ud800' }, { yaml: overlayYaml, summary: 'x'.repeat(4001) },
    { yaml: overlayYaml, summary: 'nul\0summary' },
  ])('rejects invalid output or summary: $summary', async result => {
    const f = await fixture(), task = await f.service.create('owner', 'flow', draftRequest());
    f.setResult(result); f.advance(16_000);
    expect(await f.service.get('owner', 'flow', task.draftId)).toMatchObject({ status: 'failed', error: 'INVALID_GENERATION_RESULT' });
  });
  it('reports missing provider or Bot binding honestly and does not store or dispatch', async () => {
    const f = await fixture();
    const disabled = createWorkflowAiDraftService({ ...f.options, execution: undefined, unavailableReason: 'AUTO_HEAL_DISABLED' });
    expect(await disabled.capabilities('owner', 'flow')).toEqual({ available: false, reason: 'AUTO_HEAL_DISABLED' });
    await expect(disabled.create('owner', 'flow', draftRequest())).rejects.toMatchObject({ code: 'CAPABILITY_UNAVAILABLE' });
    vi.mocked(f.execution.resolveTarget).mockRejectedValue(new Error('https://private?token=secret'));
    expect(await f.service.capabilities('owner', 'flow')).toEqual({ available: false, reason: 'AUTO_HEAL_BOT_UNAVAILABLE' });
    await expect(f.service.create('owner', 'flow', draftRequest())).rejects.toMatchObject({ code: 'CAPABILITY_UNAVAILABLE', message: 'AUTO_HEAL_BOT_UNAVAILABLE' });
    expect(f.raw.prepare('SELECT COUNT(*) AS n FROM workflow_ai_drafts').get()).toEqual({ n: 0 });
    expect(f.execution.dispatch).not.toHaveBeenCalled();
  });
  it.each(['', ' ', ' bot:owner', 'bot:owner\n', '\ud800'])(
    'does not advertise or dispatch an invalid resolved Bot binding', async botId => {
      const f = await fixture(); vi.mocked(f.execution.resolveTarget).mockResolvedValue({ botId });
      expect(await f.service.capabilities('owner', 'flow')).toEqual({ available: false, reason: 'AUTO_HEAL_BOT_UNAVAILABLE' });
      await expect(f.service.create('owner', 'flow', draftRequest())).rejects.toMatchObject({ code: 'CAPABILITY_UNAVAILABLE' });
      expect(f.execution.dispatch).not.toHaveBeenCalled();
    });
  it('refuses generation when durable storage is unavailable', async () => {
    const f = await fixture();
    const service = createWorkflowAiDraftService({ ...f.options, db: { ...f.db, dbType: 'noop' } as IDatabase });
    expect(await service.capabilities('owner', 'flow')).toEqual({ available: false, reason: 'AI_DRAFT_STORAGE_UNAVAILABLE' });
    expect(f.execution.resolveTarget).not.toHaveBeenCalled();
  });
  it('propagates persistence errors instead of returning successful terminal state', async () => {
    const f = await fixture(), task = await f.service.create('owner', 'flow', draftRequest());
    const broken = { ...f.db, dbType: f.db.dbType, dialect: f.db.dialect, query: f.db.query.bind(f.db),
      exec: vi.fn(async (sql: string, params?: unknown[]) => { if (sql.includes('result_json')) throw new Error('write failed'); return f.db.exec(sql, params); }) } as unknown as IDatabase;
    f.setResult({ yaml: overlayYaml, summary: 'Result' }); f.advance(16_000);
    await expect(createWorkflowAiDraftService({ ...f.options, db: broken }).get('owner', 'flow', task.draftId)).rejects.toThrow('write failed');
    expect(f.raw.prepare('SELECT status FROM workflow_ai_drafts').get()).toEqual({ status: 'running' });
  });
  it('bounds acknowledgement CAS conflicts without reopening dispatch', async () => {
    const f = await fixture(), request = draftRequest();
    const update = WorkflowAiDraftRepository.prototype.update;
    const spy = vi.spyOn(WorkflowAiDraftRepository.prototype, 'update').mockImplementation(function (row, patch, at) {
      return Object.hasOwn(patch, 'job_id') ? Promise.resolve(false) : update.call(this, row, patch, at);
    });
    try {
      await expect(f.service.create('owner', 'flow', request)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
      expect(f.execution.dispatch).toHaveBeenCalledTimes(1);
      expect(spy.mock.calls.filter(([, patch]) => Object.hasOwn(patch, 'job_id'))).toHaveLength(8);
      expect(await f.service.create('owner', 'flow', request)).toMatchObject({ status: 'dispatch_uncertain' });
      expect(f.execution.dispatch).toHaveBeenCalledTimes(1);
    } finally { spy.mockRestore(); }
  });
  it('bounds cancel CAS conflicts without falsely claiming terminal', async () => {
    const f = await fixture(), task = await f.service.create('owner', 'flow', draftRequest());
    const spy = vi.spyOn(WorkflowAiDraftRepository.prototype, 'update').mockResolvedValue(false);
    try {
      await expect(f.service.cancel('owner', 'flow', task.draftId)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
      expect(spy).toHaveBeenCalledTimes(8);
      expect(f.raw.prepare('SELECT status FROM workflow_ai_drafts').get()).toEqual({ status: 'running' });
    } finally { spy.mockRestore(); }
    expect(await f.service.cancel('owner', 'flow', task.draftId)).toMatchObject({ status: 'cancelled' });
  });
});
