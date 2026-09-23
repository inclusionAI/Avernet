// @vitest-environment node
import { afterEach, describe, expect, it } from 'vitest';
import express from 'express';
import type { Server } from 'node:http';
import { createRepairWorkbenchService } from '../../services/repair-batch-service.js';
import { createRepairBatchesRouter } from '../repair-batches.js';
import { repairFixture } from '../../services/__tests__/repair-batch-fixtures.js';
import { digestRepairJson } from '../../contracts/repair-batch.js';

describe('repair workbench HTTP authorization and lifecycle', () => {
  const close: Array<() => Promise<void>> = [];
  afterEach(async () => { for (const fn of close.splice(0)) await fn(); });
  async function setup(enabled = true) {
    const f = await repairFixture();
    const service = createRepairWorkbenchService(f.db, f.sourcePort, enabled ? f.executionPort : undefined);
    const app = express(); app.use(express.json({ limit: '10mb' }));
    app.use('/api/workflow-repairs', createRepairBatchesRouter({ service,
      async authorize(req, workflowId) {
        // A test composition root; production uses the separately tested verified principal resolver.
        const role = req.get('x-test-role');
        if (workflowId !== 'wf-1' || !role || role === 'denied') return null;
        return { actorId: 'verified-human', canEdit: role === 'editor' };
      },
    }));
    const server = await new Promise<Server>(resolve => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
    close.push(() => new Promise<void>(resolve => server.close(() => resolve())), () => f.db.close());
    const port = (server.address() as { port: number }).port;
    async function request(path: string, body?: unknown, role = 'editor') {
      const response = await fetch(`http://127.0.0.1:${port}/api/workflow-repairs${path}`, {
        method: body === undefined ? 'GET' : 'POST', headers: { 'content-type': 'application/json', 'x-test-role': role },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      return { status: response.status, body: await response.json() };
    }
    return { ...f, service, request };
  }
  it('requires view/edit permission and derives the mutation actor from verified authorization', async () => {
    const f = await setup();
    expect((await f.request('/candidates?workflowId=wf-1', undefined, 'denied')).status).toBe(403);
    expect((await f.request('/candidates?workflowId=wf-2')).status).toBe(403);
    const inbox = await f.request('/candidates?workflowId=wf-1', undefined, 'viewer');
    expect(inbox.body.canEdit).toBe(false);
    const input = { workflowId: 'wf-1', inputDigest: inbox.body.inputDigest, itemIds: ['item-0'], instructions: '', requestId: 'new', actorId: 'forged' };
    expect((await f.request('/', input, 'viewer')).status).toBe(403);
    const created = await f.request('/', input);
    expect(created.status).toBe(202);
    expect((await f.db.query('SELECT created_by FROM ce_tasks'))[0]).toEqual({ created_by: 'verified-human' });
    expect((await f.request(`/${created.body.taskId}`, undefined, 'denied')).status).toBe(403);
    expect((await f.request(`/${created.body.taskId}/revisions/1`, undefined, 'viewer')).status).toBe(200);
    expect((await f.request(`/${created.body.taskId}/revisions/999`)).status).toBe(404);
  });
  it('keeps dispositions usable with generation unavailable and cancels with a JSON acknowledgement', async () => {
    const f = await setup(false);
    const inbox = (await f.request('/candidates?workflowId=wf-1')).body;
    const disposition = { workflowId: 'wf-1', inputDigest: inbox.inputDigest, expectedStateVersion: 0, contentRevision: 1, action: 'no_action', reason: '', requestId: 'ignore' };
    expect((await f.request('/items/item-0/disposition', disposition)).body.state).toBe('no_action');
    expect((await f.request('/items/item-0/disposition', { ...disposition, expectedStateVersion: 1, action: 'restore', requestId: 'restore' })).body.state).toBe('pending');
    expect((await f.request('/', { workflowId: 'wf-1', inputDigest: inbox.inputDigest, itemIds: ['item-0'], instructions: '', requestId: 'create' })).status).toBe(503);
    const active = await setup();
    const candidate = (await active.request('/candidates?workflowId=wf-1')).body;
    const created = (await active.request('/', { workflowId: 'wf-1', inputDigest: candidate.inputDigest, itemIds: ['item-0'], instructions: '', requestId: 'create' })).body;
    expect(await active.request(`/${created.taskId}/cancel`, { expectedRevision: 1 })).toEqual({ status: 200, body: { ok: true } });
    expect((await active.request(`/${created.taskId}`)).body.latestAttempt.phase).toBe('cancelled');
  });
  it('maps stale sources, oversized bodies, malformed revision IDs and cross-workflow revision requests', async () => {
    const f = await setup();
    const inbox = (await f.request('/candidates?workflowId=wf-1')).body;
    const input = { workflowId: 'wf-1', inputDigest: inbox.inputDigest, itemIds: ['item-0'], instructions: '', requestId: 'create' };
    expect((await f.request('/', { ...input, inputDigest: '0'.repeat(64) })).status).toBe(409);
    expect((await f.request('/', { ...input, instructions: '界'.repeat(22000) })).status).toBe(413);
    const created = (await f.request('/', input)).body;
    expect((await f.request(`/${created.taskId}/revisions/1.5`)).status).toBe(400);
    expect((await f.request(`/${created.taskId}/revisions`, { ...input, workflowId: 'wf-2', expectedAttemptRevision: 1, parentCandidateCommit: null, feedback: '', requestId: 'r2' })).status).toBe(403);
    expect((await f.request(`/${created.taskId}/revisions/1/diff?base=anything`)).status).toBe(400);
    expect((await f.request(`/${created.taskId}/revisions/1/diff?base=baseline`)).status).toBe(409);
  });
  it('serves exact server-selected diffs, creates feedback revisions and retries only a failed dispatch', async () => {
    const f = await setup();
    const inbox = (await f.request('/candidates?workflowId=wf-1')).body;
    const input = { workflowId: 'wf-1', inputDigest: inbox.inputDigest, itemIds: ['item-0'], instructions: '', requestId: 'create' };
    const created = (await f.request('/', input)).body;
    const draft = { candidateCommit: 'd'.repeat(40), parentCandidateCommit: created.input.baseline.packCommit, packDigest: 'e'.repeat(40),
      itemResults: [{ itemId: 'item-0', status: 'changed', reason: 'Fixed' }] };
    const checks = { schemaVersion: 'workflow-repair-checks/v1', candidateCommit: draft.candidateCommit, packDigest: draft.packDigest,
      candidateDigest: digestRepairJson(draft), static: { status: 'passed' }, mock: { status: 'not_covered' }, coverage: [{ itemId: 'item-0' }] };
    await f.service.report({ workflowId: 'wf-1', taskId: created.taskId, revision: 1, expectedStateVersion: 0, status: 'succeeded', draft, checks });
    const diff = await f.request(`/${created.taskId}/revisions/1/diff?base=baseline&candidateCommit=forged`);
    expect(diff).toMatchObject({ status: 200, body: { candidateCommit: draft.candidateCommit, baseCommit: created.input.baseline.packCommit } });
    f.executionPort.dispatch = async () => { throw new Error('uncertain'); };
    const revised = await f.request(`/${created.taskId}/revisions`, { ...input, requestId: 'revision-2', feedback: 'Improve further', expectedAttemptRevision: 1, parentCandidateCommit: draft.candidateCommit });
    expect(revised).toMatchObject({ status: 202, body: { revision: 2, phase: 'drafting' } });
    expect((await f.request(`/${created.taskId}`)).body.execution.status).toBe('dispatch_failed');
    expect((await f.request(`/${created.taskId}/retry-dispatch`, { expectedRevision: 2 }, 'viewer')).status).toBe(403);
    f.executionPort.dispatch = async () => {};
    expect(await f.request(`/${created.taskId}/retry-dispatch`, { expectedRevision: 2 })).toEqual({ status: 202, body: { ok: true } });
    expect((await f.request(`/${created.taskId}/retry-dispatch`, { expectedRevision: 1 })).status).toBe(409);
  });
});
