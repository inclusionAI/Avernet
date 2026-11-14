import { afterEach, describe, expect, it, vi } from 'vitest';
import express from 'express';
import { once } from 'node:events';
import type { Server } from 'node:http';
import { aiDraftFixture, draftRequest } from '../../services/__tests__/workflow-ai-draft-fixtures.js';
import { createWorkflowAiDraftService } from '../../services/workflow-ai-draft-service.js';
import { WorkflowAiDraftRepository } from '../../repositories/workflow-ai-draft-repository.js';
import { createWorkflowAiDraftRouter } from '../workflow-ai-drafts.js';
import { WorkflowAiDraftDispatchRejectedError } from '../../contracts/workflow-ai-draft.js';
const closing: Array<() => Promise<void>> = [];
afterEach(async () => { for (const close of closing.splice(0).reverse()) await close(); });
async function fixture(publicOrigin?: string) {
  const f = await aiDraftFixture(); closing.push(() => f.db.close());
  let principal: { actorId: string } | null = { actorId: 'owner' }, allowed = true;
  const service = createWorkflowAiDraftService({ db: f.db, execution: f.execution, canEdit: async () => allowed });
  const app = express(); app.use(express.json({ limit: '1mb' }));
  app.use('/api/workflows', createWorkflowAiDraftRouter({ service, principal: async () => principal, publicOrigin }));
  const server: Server = app.listen(0, '127.0.0.1'); await once(server, 'listening');
  closing.push(() => new Promise((resolve, reject) => server.close(e => e ? reject(e) : resolve())));
  const base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  const url = `${base}/api/workflows/flow/ai-drafts`;
  const post = (path: string, body: unknown = draftRequest(), headers: Record<string, string> = {}) => fetch(url + path, {
    method: 'POST', headers: { 'content-type': 'application/json', 'x-pack-version-csrf': '1', ...headers }, body: JSON.stringify(body) });
  return { ...f, base, url, post, setPrincipal: (value: typeof principal) => { principal = value; }, revoke: () => { allowed = false; } };
}
describe('Workflow AI draft route security and browser contract', () => {
  it('implements capability/create/read/cancel with no-store and terminal result shape', async () => {
    const f = await fixture();
    const cap = await fetch(f.url + '/capabilities'); expect(cap.status).toBe(200); expect(await cap.json()).toEqual({ available: true });
    expect(cap.headers.get('cache-control')).toBe('no-store');
    const request = draftRequest(), created = await f.post('', request); expect(created.status).toBe(202);
    const task = await created.json() as { draftId: string; status: string };
    expect(task).toEqual({ draftId: expect.any(String), status: 'running' });
    const read = await fetch(f.url + '/' + task.draftId); expect(await read.json()).toMatchObject({ draftId: task.draftId, workflowId: 'flow', status: 'running', inputSpecDigest: expect.stringMatching(/^[a-f0-9]{64}$/) });
    expect(await (await f.post('/' + task.draftId + '/cancel', {})).json()).toMatchObject({ status: 'cancelled' });
    expect(f.execution.poll).not.toHaveBeenCalled();
  });
  it('does not accept forged actor/admin or unsigned headers as a principal', async () => {
    const f = await fixture(); f.setPrincipal(null);
    for (const path of ['/capabilities', '/00000000-0000-4000-8000-000000000000']) {
      expect((await fetch(f.url + path, { headers: { 'x-user-id': 'owner', cookie: 'staff_id=owner', 'x-admin': 'true' } })).status).toBe(401);
    }
    expect((await f.post('', { ...draftRequest(), actorId: 'owner', isAdmin: true })).status).toBe(401);
    expect(f.execution.dispatch).not.toHaveBeenCalled();
  });
  it('enforces CSRF header, exact origin and fetch-site on all mutations and read origins', async () => {
    const f = await fixture();
    expect((await fetch(f.url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(draftRequest()) })).status).toBe(403);
    for (const headers of [{ origin: 'https://evil.example' }, { 'sec-fetch-site': 'cross-site' }]) {
      expect((await f.post('', draftRequest(), headers)).status).toBe(403);
      expect((await fetch(f.url + '/capabilities', { headers })).status).toBe(403);
    }
    expect((await f.post('', draftRequest(), { origin: f.base })).status).toBe(202);
    expect((await f.post('/00000000-0000-4000-8000-000000000000/cancel', {}, { origin: 'https://evil.example' })).status).toBe(403);
  });
  it('binds tasks to workflow and owner and rechecks live edit grants', async () => {
    const f = await fixture(), task = await (await f.post('')).json() as { draftId: string };
    f.setPrincipal({ actorId: 'other' });
    expect((await fetch(f.url + '/' + task.draftId)).status).toBe(404);
    expect((await f.post('/' + task.draftId + '/cancel', {})).status).toBe(404);
    f.setPrincipal({ actorId: 'owner' }); f.revoke();
    expect((await fetch(f.url + '/capabilities')).status).toBe(403);
    expect((await fetch(f.url + '/' + task.draftId)).status).toBe(403);
    expect((await f.post('/' + task.draftId + '/cancel', {})).status).toBe(403);
    expect((await f.post('')).status).toBe(403);
  });
  it('returns idempotency conflicts and validates bytes, prompt, workflow and UUID', async () => {
    const f = await fixture(), request = draftRequest();
    const first = await (await f.post('', request)).json(); expect(await (await f.post('', request)).json()).toEqual(first);
    expect((await f.post('', { ...request, currentYaml: request.currentYaml + '# edited\n' })).status).toBe(409);
    expect((await f.post('', { ...draftRequest(), currentYaml: 'id: other\nnodes: []' })).status).toBe(400);
    expect((await f.post('', { ...draftRequest(), prompt: '大'.repeat(4001) })).status).toBe(413);
    expect((await f.post('', { ...draftRequest(), currentYaml: request.currentYaml + '# ' + '大'.repeat(34_000) })).status).toBe(413);
    expect((await f.post('', { ...draftRequest(), requestId: 'uuid' })).status).toBe(400);
  });
  it('makes post-dispatch CAS exhaustion recoverable on the wire without reopening dispatch', async () => {
    const f = await fixture(), request = draftRequest();
    const update = WorkflowAiDraftRepository.prototype.update;
    const spy = vi.spyOn(WorkflowAiDraftRepository.prototype, 'update').mockImplementation(function (row, patch, at) {
      return Object.hasOwn(patch, 'job_id') ? Promise.resolve(false) : update.call(this, row, patch, at);
    });
    try {
      const uncertain = await f.post('', request);
      expect(uncertain.status).toBe(503);
      expect(await uncertain.json()).toMatchObject({ code: 'STATE_CONFLICT' });
      expect(f.execution.dispatch).toHaveBeenCalledTimes(1);
      const stored = f.raw.prepare('SELECT draft_id, status FROM workflow_ai_drafts').get() as { draft_id: string; status: string };
      expect(stored.status).toBe('dispatch_uncertain');
      const recovery = await f.post('', request);
      expect(recovery.status).toBe(202);
      expect(await recovery.json()).toEqual({ draftId: stored.draft_id, status: 'dispatch_uncertain' });
      expect(f.execution.dispatch).toHaveBeenCalledTimes(1);
      const conflicting = await f.post('', { ...request, prompt: 'different input' });
      expect(conflicting.status).toBe(409);
      expect(await conflicting.json()).toMatchObject({ code: 'IDEMPOTENCY_CONFLICT' });
      expect(f.execution.dispatch).toHaveBeenCalledTimes(1);
    } finally { spy.mockRestore(); }
  });
  it('never exposes raw transport credentials in errors', async () => {
    const f = await fixture(); vi.mocked(f.execution.resolveTarget).mockRejectedValue(new Error('https://private/?token=SECRET'));
    const response = await f.post(''); expect(response.status).toBe(503);
    expect(await response.text()).not.toContain('SECRET');
  });
  it('ignores browser target overrides and reports binding rejection as a terminal draft', async () => {
    const f = await fixture();
    vi.mocked(f.execution.dispatch).mockRejectedValue(new WorkflowAiDraftDispatchRejectedError('AUTO_HEAL_BOT_UNAVAILABLE'));
    const request = { ...draftRequest(), botId: 'forged:owner', target: { botId: 'forged:owner' }, actorId: 'forged', jobId: 'forged' };
    const response = await f.post('', request); expect(response.status).toBe(202);
    const task = await response.json() as { draftId: string; status: string };
    expect(task.status).toBe('failed');
    expect(vi.mocked(f.execution.dispatch).mock.calls[0][0]).toMatchObject({ actorId: 'owner', envelope: { target: { botId: 'auto-heal-bot:bot-owner' } } });
    expect(await (await fetch(f.url + '/' + task.draftId)).json()).toMatchObject({ status: 'failed', error: 'AUTO_HEAL_BOT_UNAVAILABLE' });
    expect(await (await f.post('', request)).json()).toEqual(task);
    expect(f.execution.dispatch).toHaveBeenCalledTimes(1);
  });
  it('supports an explicit HTTPS canonical origin behind HTTP ingress without trusting forwarded headers', async () => {
    const f = await fixture('https://workflow.example.invalid/');
    expect((await f.post('', draftRequest(), { origin: 'https://workflow.example.invalid' })).status).toBe(202);
    expect((await f.post('', draftRequest(), { origin: f.base, 'x-forwarded-proto': 'https', 'x-forwarded-host': 'workflow.example.invalid' })).status).toBe(403);
    expect((await f.post('', draftRequest(), { origin: 'https://evil.example', 'x-forwarded-host': 'workflow.example.invalid' })).status).toBe(403);
    expect((await fetch(f.url + '/capabilities', { headers: { origin: 'https://workflow.example.invalid' } })).status).toBe(200);
    expect((await fetch(f.url, { method: 'POST', headers: { origin: 'https://workflow.example.invalid', 'content-type': 'application/json' }, body: JSON.stringify(draftRequest()) })).status).toBe(403);
  });
  it.each(['https://user:password@example.invalid', 'https://example.invalid/path', 'https://example.invalid/?secret=1', 'https://example.invalid/#hash', 'file:///private'])('rejects invalid host canonical origin: %s', publicOrigin => {
    expect(() => createWorkflowAiDraftRouter({ service: {} as never, principal: async () => null, publicOrigin })).toThrow('PUBLIC_ORIGIN_INVALID');
  });

});
