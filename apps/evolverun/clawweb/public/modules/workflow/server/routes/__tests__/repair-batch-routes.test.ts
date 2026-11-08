import {repairReleaseMigrations} from '@avernet/clawweb-shared/server/schema-repair-release';
// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest';
import express from 'express';
import type { Server } from 'node:http';
import { gzipSync } from 'node:zlib';
import { createRepairWorkbenchService } from '../../services/repair-batch-service.js';
import { createRepairBatchesRouter } from '../repair-batches.js';
import { repairFixture } from '../../services/__tests__/repair-batch-fixtures.js';
import { createRepairSourcePort } from '../../repositories/repair-source-adapter.js';
import { digestRepairJson } from '../../contracts/repair-batch.js';
import { createRepairReleaseService } from '../../services/repair-release-service.js';

describe('repair workbench HTTP authorization and lifecycle', () => {
  const close: Array<() => Promise<void>> = [];
  afterEach(async () => { for (const fn of close.splice(0)) await fn(); });
  async function setup(enabled = true, withRelease = false) {
    const f = await repairFixture();
    const service = createRepairWorkbenchService(f.db, f.sourcePort, enabled ? f.executionPort : undefined);
    if (withRelease) for (const migration of repairReleaseMigrations) for (const sql of migration.sql) await f.db.exec(f.db.dialect.renderDdl(sql));
    const app = express(); app.use(express.json({ limit: '10mb' }));
    app.use('/api/workflow-repairs', createRepairBatchesRouter({ service,
      ...(withRelease ? { release: createRepairReleaseService({ db: f.db, workbench: service }) } : {}),
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
    async function request(path: string, body?: unknown, role = 'editor', gzip = false) {
      const response = await fetch(`http://127.0.0.1:${port}/api/workflow-repairs${path}`, {
        method: body === undefined ? 'GET' : 'POST', headers: { 'content-type': 'application/json', 'x-test-role': role,
          ...(gzip ? { 'content-encoding': 'gzip' } : {}) },
        ...(body === undefined ? {} : { body: gzip ? gzipSync(JSON.stringify(body)) : JSON.stringify(body) }),
      });
      return { status: response.status, body: await response.json() };
    }
    return { ...f, service, request };
  }
  it('reads frozen task history only once when serving task and release details together', async () => {
    const f = await setup(true, true);
    const inbox = await f.request('/candidates?workflowId=wf-1');
    const created = await f.request('/', { workflowId: 'wf-1', inputDigest: inbox.body.inputDigest,
      itemIds: ['item-0'], instructions: '', requestId: 'one-task-read' });
    const query = vi.spyOn(f.db, 'query');
    const result = await f.request(`/${created.body.taskId}`);
    const reads = query.mock.calls.filter(([sql]) => sql.startsWith('SELECT * FROM workflow_repair_revisions WHERE workflow_id = ? AND task_id = ? ORDER BY revision DESC'));
    query.mockRestore();
    expect(result.status).toBe(200);
    expect(result.body).toMatchObject({ latestAttempt: { revision: 1 }, release: { approval: null, deployments: [] } });
    expect(reads).toHaveLength(1);
  });
  it('supports task-free candidate reads over GET and POST without changing selection identities or authorization', async () => {
    const f = await setup();
    const shown = await f.request('/candidates?workflowId=wf-1');
    await f.request('/', { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: shown.body.inputDigest, instructions: '', requestId: 'light-page' });
    const normal = await f.request('/candidates?workflowId=wf-1');
    expect(normal.body.tasks).toHaveLength(1);
    const reads = vi.spyOn(f.db, 'query');
    for (const result of [await f.request('/candidates?workflowId=wf-1&includeTasks=false', undefined, 'viewer'),
      await f.request('/candidates/query', { workflowId: 'wf-1', includeTasks: false }, 'viewer')]) {
      expect(result.status).toBe(200);
      expect(result.body).toMatchObject({ tasks: [], inputDigest: normal.body.inputDigest, items: normal.body.items, canEdit: false });
    }
    expect(reads.mock.calls.filter(([sql]) => sql.includes('FROM workflow_repair_revisions'))).toHaveLength(0);
    reads.mockRestore();
    expect((await f.request('/candidates?workflowId=wf-1&includeTasks=invalid')).status).toBe(400);
    expect((await f.request('/candidates?workflowId=wf-1&includeTasks=false', undefined, 'denied')).status).toBe(403);
  });
  it('returns a definite pre-creation failure instead of an unknown write result when the deployed pack cannot be read', async () => {
    const f = await setup();
    const inbox = await f.request('/candidates?workflowId=wf-1');
    f.executionPort.resolveBaseline = async () => { throw new Error('private git diagnostic'); };
    const failed = await f.request('/', { workflowId: 'wf-1', inputDigest: inbox.body.inputDigest,
      itemIds: ['item-0', 'item-1'], instructions: 'Keep selection', requestId: 'baseline-read' });
    expect(failed).toMatchObject({ status: 503, body: { code: 'CAPABILITY_UNAVAILABLE',
      reasonCode: 'BASELINE_READ_FAILED', stage: 'baseline', outcome: 'not_created' } });
    expect(JSON.stringify(failed.body)).not.toContain('private git diagnostic');
    expect(f.calls).toHaveLength(0);
    expect((await f.request('/requests/lookup?workflowId=wf-1&requestId=baseline-read')).body.revision).toBeNull();
  });
  it.each([1,110])('reads oversized evidence from %i diagnoses through real GET/POST without applying the generation freeze limit', async count => {
    const f = await repairFixture(0); close.push(() => f.db.close());
    for(const migration of repairReleaseMigrations)for(const sql of migration.sql)await f.db.exec(f.db.dialect.renderDdl(sql));
    const source = { diagnosisId:'d',analysisId:'a',flowId:'run',sourceId:'s',completedAtMs:1,
      failureSignature:'timeout',failureMode:'timeout',nodeId:'query',reasoning:'证'.repeat(366000)+'x'.repeat(976),evidenceEventIds:[],
      proposal:{summary:'修复查询',operations:[{op:'replace',nodeId:'query',path:'/prompt',value:'full prompt'}]} };
    const sources=Array.from({length:count},(_,i)=>({...source,diagnosisId:`d-${i}`,analysisId:`a-${i}`,flowId:`run-${i}`,sourceId:`s-${i}`,reasoning:count===1?source.reasoning:'x'.repeat(9600)}));
    const port=createRepairSourcePort({groups:async()=>[{workflowId:'wf-1',signature:'timeout|step1',inputDigest:'a'.repeat(64),sources}],suggestions:async()=>[],evidence:async()=>[]});
    const service=createRepairWorkbenchService(f.db,port,f.executionPort);
    const app=express();app.use(express.json({limit:'10mb'}));app.use('/api/workflow-repairs',createRepairBatchesRouter({service,authorize:async()=>({actorId:'test',canEdit:true})}));
    const server=await new Promise<Server>(resolve=>{const s=app.listen(0,'127.0.0.1',()=>resolve(s))});close.unshift(()=>new Promise<void>(resolve=>server.close(()=>resolve())));
    const url=`http://127.0.0.1:${(server.address() as {port:number}).port}/api/workflow-repairs`;
    const body={workflowId:'wf-1',previewSignatures:['timeout|step1']};
    for(const post of [false,true]){
      const response=await fetch(url+(post?'/candidates/query':'/candidates?workflowId=wf-1&previewSignature=timeout%7Cstep1'),post?{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)}:undefined);
      const json=await response.json();
      console.log('oversized source HTTP reproduction',JSON.stringify({method:post?'POST':'GET',requestBytes:post?Buffer.byteLength(JSON.stringify(body)):0,status:response.status,responseBytes:Buffer.byteLength(JSON.stringify(json)),code:json.code,limit:json.limit,actualBytes:json.actualBytes}));
      expect(response.status).toBe(200);expect(Buffer.byteLength(JSON.stringify(json))).toBeLessThan(64*1024);
      expect(json.page.total).toBe(1);expect(json.items[0].readSummary).toMatchObject({sourceCount:count,runCount:count,diagnosisCount:count});
      const detail=await fetch(url+`/items/${json.items[0].itemId}?workflowId=wf-1`).then(r=>r.json());
      expect(detail.context.diagnoses).toHaveLength(count);
      expect(detail.context.diagnoses[0].reasoning).toBe(sources[0].reasoning);
      const created=await service.create('test',{workflowId:'wf-1',inputDigest:json.inputDigest,itemIds:[json.items[0].itemId],instructions:'',requestId:`oversize-${post}`});
      expect(created.input.materials).toBeDefined();expect(Buffer.byteLength(JSON.stringify(created.input))).toBeLessThan(1024*1024);
    }
    expect(f.calls).toHaveLength(1);expect(await f.db.query('SELECT * FROM workflow_repair_items')).toHaveLength(1);
  });
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
  it('authorizes frozen material reads and rejects items outside the selected revision',async()=>{
    const f=await setup();const inbox=await f.request('/candidates?workflowId=wf-1');
    const created=await f.request('/',{workflowId:'wf-1',inputDigest:inbox.body.inputDigest,itemIds:['item-0'],instructions:'',requestId:'read-material'});
    const path=`/${created.body.taskId}/revisions/1/materials/item-0`;
    expect((await f.request(path,undefined,'denied')).status).toBe(403);
    const result=await f.request(path,undefined,'viewer');expect(result.status).toBe(200);expect(result.body.itemId).toBe('item-0');
    expect((await f.request(`/${created.body.taskId}/revisions/1/materials/item-1`,undefined,'viewer')).status).toBe(404);
  });
  it('authorizes frozen-input retry and returns the same revision for duplicate requests', async () => {
    const f = await setup();
    const inbox = await f.request('/candidates?workflowId=wf-1');
    const created = await f.request('/', { workflowId: 'wf-1', inputDigest: inbox.body.inputDigest,
      itemIds: ['item-0'], instructions: 'Keep rules', requestId: 'first' });
    f.setRemoteStatus({ status: 'failed', rawStatus: 'failed', errorMessage: 'model failed' });
    await f.service.reconcile();
    const path = `/${created.body.taskId}/retry-generation`;
    const input = { expectedRevision: 1, requestId: 'retry-first' };
    expect((await f.request(path, input, 'viewer')).status).toBe(403);
    const [first, repeated] = await Promise.all([f.request(path, input), f.request(path, input)]);
    expect(first.status).toBe(202); expect(repeated.status).toBe(202);
    expect(first.body.revision).toBe(2); expect(repeated.body).toEqual(first.body);
    expect(f.calls).toHaveLength(2);
  });
  it('validates and forwards server-side inbox pagination', async () => {
    const f = await setup();
    const page = await f.request('/candidates?workflowId=wf-1&state=pending&page=1&pageSize=1');
    expect(page).toMatchObject({ status: 200, body: { page: { page: 1, pageSize: 1, total: 2, totalPages: 2 } } });
    expect(page.body.items).toHaveLength(1);
    expect((await f.request('/candidates?workflowId=wf-1&page=0')).status).toBe(400);
    expect((await f.request('/candidates?workflowId=wf-1&pageSize=201')).status).toBe(400);
    expect((await f.request('/candidates?workflowId=wf-1&state=forged')).status).toBe(400);
    expect((await f.request('/candidates?workflowId=wf-1&includeHistorical=1')).status).toBe(400);
    expect((await f.request('/candidates?workflowId=wf-1&includeHistorical=true')).body.includeHistorical).toBe(true);
  });
  it('returns bounded previews for all visible issues in one authorized read, including empty issues', async () => {
    const f = await setup();
    f.items[0].context = { signature: 'issue-a' };
    f.items[1].context = { signature: 'issue-b' };
    const response = await f.request('/candidates?workflowId=wf-1&pageSize=1&previewSignature=issue-b&previewSignature=empty');
    expect(response.status).toBe(200);
    expect(response.body.issuePreviews).toMatchObject([
      { signature: 'issue-b', total: 1, items: [{ itemId: 'item-1' }] },
      { signature: 'empty', total: 0, items: [] },
    ]);
    expect(response.body.page.total).toBe(2);
    expect((await f.request('/candidates?workflowId=wf-1&previewSignature=issue-b', undefined, 'denied')).status).toBe(403);
    expect((await f.request('/candidates?workflowId=wf-1' + '&previewSignature=a'.repeat(51))).status).toBe(400);
  });
  it('accepts long batch scopes through a view-authorized read-only POST and enforces body/count bounds', async () => {
    const f = await setup();
    const previewSignatures = Array.from({ length: 50 }, (_, i) => `${i}:${'节点'.repeat(250)}`);
    f.items[0].context = { signature: previewSignatures[0] };
    const body = { workflowId: 'wf-1', pageSize: 1, includeHistorical: true, previewSignatures };
    const result = await f.request('/candidates/query', body, 'viewer', true);
    expect(result.status).toBe(200);
    expect(result.body.canEdit).toBe(false);
    expect(result.body.issuePreviews).toHaveLength(50);
    expect(result.body.issuePreviews[0]).toMatchObject({ total: 1, items: [{ itemId: 'item-0' }] });
    expect(result.body.issuePreviews[49]).toMatchObject({ total: 0, items: [] });
    expect(await f.db.query('SELECT * FROM workflow_repair_items')).toEqual([]);
    expect((await f.request('/candidates/query', body, 'denied')).status).toBe(403);
    expect((await f.request('/candidates/query', { ...body, previewSignatures: [...previewSignatures, 'extra'] })).status).toBe(400);
    expect((await f.request('/candidates/query', { ...body, pageSize: 201 })).status).toBe(400);
    const oversized=await f.request('/candidates/query', { ...body, extra: 'x'.repeat(256 * 1024) });
    expect(oversized).toMatchObject({status:413,body:{code:'PAYLOAD_TOO_LARGE',stage:'request_body',reasonCode:'REPAIR_REQUEST_TOO_LARGE',limit:262144}});
    expect(oversized.body.actualBytes).toBeGreaterThan(262144);
  });
  it('keeps dispositions usable with generation unavailable and cancels with a JSON acknowledgement', async () => {
    const f = await setup(false);
    const inbox = (await f.request('/candidates?workflowId=wf-1')).body;
    const disposition = { workflowId: 'wf-1', inputDigest: inbox.inputDigest, expectedStateVersion: 0, contentRevision: 1, action: 'no_action', reason: '', requestId: 'ignore' };
    const ignored = await f.request('/items/item-0/disposition', disposition);
    expect(ignored).toMatchObject({ status: 200, body: { state: 'no_action' } });
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
    expect((await f.request('/', { ...input, inputDigest: '0'.repeat(64), requestId: 'stale-list' })).status).toBe(202);
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
    f.executionPort.dispatch = async () => { throw new Error('AIS executeSnapshot HTTP 400: invalid request'); };
    const revised = await f.request(`/${created.taskId}/revisions`, { ...input, requestId: 'revision-2', feedback: 'Improve further', expectedAttemptRevision: 1, parentCandidateCommit: draft.candidateCommit });
    expect(revised).toMatchObject({ status: 202, body: { revision: 2, phase: 'drafting' } });
    expect((await f.request(`/${created.taskId}`)).body.execution.status).toBe('dispatch_failed');
    expect((await f.request(`/${created.taskId}/retry-dispatch`, { expectedRevision: 2 }, 'viewer')).status).toBe(403);
    f.executionPort.dispatch = async () => {};
    expect(await f.request(`/${created.taskId}/retry-dispatch`, { expectedRevision: 2 })).toEqual({ status: 202, body: { ok: true } });
    expect((await f.request(`/${created.taskId}/retry-dispatch`, { expectedRevision: 1 })).status).toBe(409);
  });
  it('returns a recovery action and rechecks failed results without dispatching before an optional-text revision', async () => {
    const f = await setup();
    const inbox = (await f.request('/candidates?workflowId=wf-1')).body;
    const input = { workflowId: 'wf-1', inputDigest: inbox.inputDigest, itemIds: ['item-0'], instructions: '', requestId: 'first' };
    const created = (await f.request('/', input)).body;
    f.setRemoteStatus({ status: 'failed', rawStatus: 'failed', errorMessage: 'failed' });
    await f.service.reconcile();
    const originalRead = f.executionPort.readResult;
    f.executionPort.readResult = async () => { throw new Error('Git transport unavailable'); };
    const revised = { ...input, requestId: 'revision-two', expectedAttemptRevision: 1, parentCandidateCommit: null, feedback: '' };
    expect(await f.request(`/${created.taskId}/revisions`, revised)).toMatchObject({ status: 409,
      body: { code: 'STATE_CONFLICT', recoveryAction: 'retry-result', reasonCode: 'RESULT_READ_UNKNOWN' } });
    expect(f.calls).toHaveLength(1);
    expect(await f.request(`/${created.taskId}/retry-result`, { expectedRevision: 1 })).toMatchObject({ status: 409,
      body: { recoveryAction: 'retry-result' } });
    f.executionPort.readResult = originalRead;
    expect(await f.request(`/${created.taskId}/retry-result`, { expectedRevision: 1 })).toEqual({ status: 202, body: { ok: true } });
    expect(f.calls).toHaveLength(1);
    expect((await f.service.task(created.taskId)).latestAttempt.phase).toBe('failed');
    expect(await f.request(`/${created.taskId}/revisions`, revised)).toMatchObject({ status: 202,
      body: { revision: 2, input: { instructions: '', feedback: '' } } });
    expect(f.calls).toHaveLength(2);
  });

});
