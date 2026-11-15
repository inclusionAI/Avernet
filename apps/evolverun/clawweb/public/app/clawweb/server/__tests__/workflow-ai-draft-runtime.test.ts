import { afterEach, describe, expect, it, vi } from 'vitest';
import express from 'express';
import { once } from 'node:events';
import type { Server } from 'node:http';
import { aiDraftFixture, draftRequest } from './workflow-ai-draft-db-fixture.js';
import { createWorkflowAiDraftRuntime } from '../workflow-ai-draft-runtime.js';
const closing: Array<() => Promise<void>> = [];
afterEach(async () => { for (const close of closing.splice(0).reverse()) await close(); });
describe('Reusable workflow AI runtime assembly', () => {
  it('mounts browser routes, accepts only verified principals, enforces live edit ACL and owner binding', async () => {
    const f = await aiDraftFixture(); closing.push(() => f.db.close());
    let principal: { actorId: string } | null = { actorId: 'owner' };
    const runtime = createWorkflowAiDraftRuntime(f.db, { principal: async () => principal, execution: f.execution });
    const app = express(); app.use(express.json()); app.use('/api/workflows', runtime.router);
    const server: Server = app.listen(0, '127.0.0.1'); await once(server, 'listening');
    closing.push(() => new Promise((resolve, reject) => server.close(e => e ? reject(e) : resolve())));
    const url = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/workflows/flow/ai-drafts`;
    expect(await (await fetch(url + '/capabilities')).json()).toEqual({ available: true });
    const response = await fetch(url, { method: 'POST', headers: { 'content-type': 'application/json', 'x-pack-version-csrf': '1' }, body: JSON.stringify(draftRequest()) });
    expect(response.status).toBe(202);
    const task = await response.json() as { draftId: string };
    principal = { actorId: 'viewer' };
    expect((await fetch(url + '/' + task.draftId)).status).toBe(403);
    f.raw.exec("UPDATE bot_workflow_permissions SET bot_owner_id = '*' WHERE workflow_id = 'flow'");
    expect((await fetch(url + '/' + task.draftId)).status).toBe(404);
    principal = null;
    expect((await fetch(url + '/capabilities', { headers: { 'x-user-id': 'owner', 'x-admin': 'true' } })).status).toBe(401);
    expect(runtime.monitor.reconcile).toBeTypeOf('function');
  });
  it('honors host-owned verified-admin policy and rechecks it on reconciliation', async () => {
    const f = await aiDraftFixture(); closing.push(() => f.db.close());
    let allowed = true;
    const policy = vi.fn(async (_workflowId: string, actorId: string) => actorId === 'verified-admin' && allowed);
    const runtime = createWorkflowAiDraftRuntime(f.db, { principal: async () => ({ actorId: 'verified-admin' }), canEdit: policy, execution: f.execution });
    expect(await runtime.service.capabilities('verified-admin', 'flow')).toEqual({ available: true });
    const task = await runtime.service.create('verified-admin', 'flow', draftRequest());
    allowed = false;
    await expect(runtime.service.get('verified-admin', 'flow', task.draftId)).rejects.toMatchObject({ code: 'FORBIDDEN' });
    f.raw.exec('UPDATE workflow_ai_drafts SET next_poll_at_ms = 0');
    await runtime.monitor.reconcile();
    expect(f.raw.prepare('SELECT status FROM workflow_ai_drafts').get()).toEqual({ status: 'failed' });
  });
  it('keeps missing AutoHeal/BaaS configuration an explicit capability, with no implicit execution', async () => {
    const f = await aiDraftFixture(); closing.push(() => f.db.close());
    const runtime = createWorkflowAiDraftRuntime(f.db, { principal: async () => ({ actorId: 'owner' }), unavailableReason: 'AUTO_HEAL_BAAS_NOT_CONFIGURED' });
    expect(await runtime.service.capabilities('owner', 'flow')).toEqual({ available: false, reason: 'AUTO_HEAL_BAAS_NOT_CONFIGURED' });
    expect(await runtime.monitor.reconcile()).toBe(0);
  });
});
