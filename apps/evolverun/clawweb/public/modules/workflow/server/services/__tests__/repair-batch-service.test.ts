// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createRepairWorkbenchService } from '../repair-batch-service.js';
import { digestRepairJson } from '../../contracts/repair-batch.js';
import { repairSignatureKey } from '../../contracts/repair-workbench.js';
import { RepairBatchRepository } from '../../repositories/repair-batch-repository.js';
import { repairFixture } from './repair-batch-fixtures.js';

describe('repair workbench control service', () => {
  const fixtures: Awaited<ReturnType<typeof repairFixture>>[] = [];
  afterEach(async () => { vi.restoreAllMocks(); for (const fixture of fixtures.splice(0)) await fixture.db.close(); });
  async function setup(count = 2, enabled = true) {
    const f = await repairFixture(count); fixtures.push(f);
    const service = createRepairWorkbenchService(f.db, f.sourcePort, enabled ? f.executionPort : undefined);
    const inbox = await service.candidates('wf-1');
    const request = { workflowId: 'wf-1', itemIds: f.items.map(i => i.itemId), inputDigest: inbox.inputDigest, instructions: '', requestId: 'create-1' };
    return { ...f, service, inbox, request };
  }
  it('reconciles all manually selected goals together without adding unselected suggestions', async () => {
    const f = await setup(3);
    const selected = ['item-0', 'item-2'];
    const revision = await f.service.create('human', { ...f.request, itemIds: selected });
    expect(revision.input.integration?.plans).toHaveLength(1);
    expect(revision.input.integration?.plans[0].originalItemIds).toEqual(selected);
    expect(revision.input.items.map(item => item.itemId)).toEqual(selected);
    const legacyInput={...revision.input};delete legacyInput.integration;
    await f.db.exec('UPDATE workflow_repair_revisions SET input_json = ? WHERE task_id = ? AND revision = 1',[JSON.stringify(legacyInput),revision.taskId]);
    await f.service.report({ workflowId: 'wf-1', taskId: revision.taskId, revision: 1,
      expectedStateVersion: revision.stateVersion, status: 'failed', error: { code: 'CANDIDATE_WRITE_FAILED' } });
    f.setRemoteStatus({ status: 'failed', rawStatus: 'failed', errorMessage: null });
    const retry = await f.service.retryGeneration('human', revision.taskId, { expectedRevision: 1, requestId: 'retry-joint' });
    expect(retry.input.integration?.plans[0].originalItemIds).toEqual(selected);
    expect(retry.input.baseline).toEqual(revision.input.baseline);
    expect((await f.service.task(revision.taskId)).revisions.find(row=>row.revision===1)!.input.integration).toBeUndefined();
  });
  it('settles failed AIS from its status without fetching a Git failure branch', async () => {
    const f = await setup(1);
    const revision = await f.service.create('human', f.request);
    const error = { code: 'CANDIDATE_WRITE_FAILED', message: '模型结果不是合法 JSON',
      stage: 'parse_model_output', details: { reasonCode: 'MODEL_OUTPUT_INVALID_JSON', itemIds: ['item-0'], line: 1, column: 2 } };
    f.setRemoteStatus({ status: 'failed', rawStatus: 'failed', errorMessage: 'container exited with code 1' });
    f.setGeneratedResult({ status: 'failed', error });
    const read = vi.spyOn(f.executionPort, 'readResult');
    await f.service.reconcile();
    expect(read).not.toHaveBeenCalled();
    expect((await f.service.task(revision.taskId)).latestAttempt.error).toMatchObject({ code: 'AIS_FAILED', message: 'container exited with code 1' });
  });
  it('groups node overlaps transitively while keeping a multi-node source atomic',async()=>{
    const f=await setup(4);
    const scopes=[['a'],['b'],['a','b'],['c']];
    f.items.forEach((item,index)=>{item.proposal={operations:scopes[index].map(nodeId=>({nodeId,path:'/executor/prompt',op:'replace',value:'selected goal'}))};});
    const inbox=await f.service.candidates('wf-1');
    const revision=await f.service.create('human',{...f.request,inputDigest:inbox.inputDigest});
    const groups=revision.input.integration!.plans.map(plan=>plan.originalItemIds).sort((a,b)=>a[0].localeCompare(b[0]));
    expect(groups).toEqual([['item-0','item-1','item-2'],['item-3']]);
  });
  it('reviews workflow-level constraints together with selected node changes',async()=>{
    const f=await setup(2);
    f.items[0].proposal={operations:[{nodeId:'query',path:'/executor/prompt',op:'replace',value:'goal'}]};
    f.items[1].proposal={operations:[{nodeId:'',path:'/inputs',op:'replace',value:{}}]};
    const inbox=await f.service.candidates('wf-1');
    const revision=await f.service.create('human',{...f.request,inputDigest:inbox.inputDigest});
    expect(revision.input.integration?.plans.map(plan=>plan.originalItemIds)).toEqual([['item-0','item-1']]);
  });
  it('fits large previews within the shared response budget without losing issue totals or full item data', async () => {
    const f = await repairFixture(7); fixtures.push(f);
    f.items.forEach((item, i) => {
      item.context = { signature: `issue-${i}` };
      if (i < 6) item.proposal = { summary: 'large', value: '界'.repeat(300_000) };
    });
    const service = createRepairWorkbenchService(f.db, f.sourcePort);
    const result = await service.candidates('wf-1', { pageSize: 1, previewSignatures: f.items.map((_, i) => `issue-${i}`) });
    expect(Buffer.byteLength(JSON.stringify({ ...result, canEdit: true }))).toBeLessThan(4 * 1024 * 1024);
    expect(result.issuePreviews?.map(preview => preview.total)).toEqual([1, 1, 1, 1, 1, 1, 1]);
    expect(result.issuePreviews?.every(preview => preview.total === preview.items.length)).toBe(true);
    expect(result.issuePreviews?.[0].items[0].readSummary?.originalBytes).toBeGreaterThan(900000);
    expect(result.issuePreviews?.[6].items[0].itemId).toBe('item-6');
    expect((await service.item('wf-1', 'item-5')).proposal).toEqual(f.items[5].proposal);
    expect(result.page.total).toBe(7);
  });
  it('keeps GET read-only, merges lifecycle history, and allows no_action/restore without AIS', async () => {
    const f = await setup(2, false);
    expect(f.inbox.capabilities).toMatchObject({ generation: false, publication: false });
    expect(await f.db.query('SELECT * FROM workflow_repair_items')).toEqual([]);
    await expect(f.service.create('human', f.request)).rejects.toMatchObject({ code: 'CAPABILITY_UNAVAILABLE' });
    const disposition = { workflowId: 'wf-1', itemId: 'item-0', inputDigest: f.inbox.inputDigest, expectedStateVersion: 0,
      contentRevision: 1, action: 'no_action' as const, reason: 'Later', requestId: 'ignore-1' };
    const ignored = await f.service.disposition('human', disposition);
    expect(ignored.state).toBe('no_action');
    expect(await f.service.disposition('human', disposition)).toEqual(ignored);
    f.items.splice(0, 1);
    const historical = await f.service.candidates('wf-1', { includeHistorical: true });
    expect(historical.items.find(i => i.itemId === 'item-0')).toMatchObject({ state: 'no_action', sourceAvailable: false });
    const restored = await f.service.disposition('human', { ...disposition, action: 'restore', inputDigest: historical.inputDigest, expectedStateVersion: 1, requestId: 'restore-1' });
    expect(restored.state).toBe('pending');
  });
  it('paginates and filters inbox items while keeping stable totals and digest', async () => {
    const f = await setup(39, false);
    const first = await f.service.candidates('wf-1', { state: 'pending', page: 1, pageSize: 20 });
    const second = await f.service.candidates('wf-1', { state: 'pending', page: 2, pageSize: 20 });
    expect(first.items).toHaveLength(20);
    expect(second.items).toHaveLength(19);
    expect(first.page).toEqual({ page: 1, pageSize: 20, total: 39, totalPages: 2 });
    expect(first.counts).toMatchObject({ pending: 39, all: 39 });
    expect(second.inputDigest).toBe(first.inputDigest);
    expect(new Set([...first.items, ...second.items].map(item => item.itemId))).toHaveLength(39);
  });
  it('limits each issue preview but keeps its real total and reads sources only once', async () => {
    const f = await repairFixture(8); fixtures.push(f);
    f.items.forEach((item, i) => { item.context = { signature: i < 6 ? 'many' : 'few' }; });
    const load = vi.spyOn(f.sourcePort, 'load');
    const service = createRepairWorkbenchService(f.db, f.sourcePort);
    const result = await service.candidates('wf-1', { page: 2, pageSize: 1, previewSignatures: ['many', 'few', 'empty'] });
    expect(result.page).toMatchObject({ page: 2, total: 8 });
    expect(result.issuePreviews?.map(p => ({ total: p.total, ids: p.items.map(i => i.itemId) }))).toEqual([
      { total: 6, ids: ['item-0', 'item-1', 'item-2'] }, { total: 2, ids: ['item-6', 'item-7'] }, { total: 0, ids: [] },
    ]);
    expect(load).toHaveBeenCalledTimes(1);
    expect(await f.db.query('SELECT * FROM workflow_repair_items')).toEqual([]);
  });
  it('returns complete suggestion facets before filtering and pagination', async () => {
    const f = await setup(25, false);
    f.items.forEach((item, index) => { item.context = { diagnoses: [{
      nodeId: index < 20 ? 'fetch' : 'write', failureMode: index < 20 ? 'timeout' : 'output',
    }] }; });
    const first = await f.service.candidates('wf-1', { pageSize: 20 });
    expect(first.items).toHaveLength(20);
    expect(first.facets).toEqual({ nodes: ['fetch', 'write'], failureModes: ['output', 'timeout'] });
    const filtered = await f.service.candidates('wf-1', { nodeId: 'write', failureMode: 'output' });
    expect(filtered.items).toHaveLength(5);
    expect(filtered.facets).toEqual(first.facets);
    const empty = await f.service.candidates('wf-1', { nodeId: 'missing' });
    expect(empty.items).toHaveLength(0);
    expect(empty.facets).toEqual(first.facets);
  });
  it('applies node, problem type and signature filters before counts and pagination without changing the selection digest', async () => {
    const f = await setup(4, false);
    f.items.forEach((item, i) => { item.context = { signature: `issue-${i}`, diagnoses: [{ nodeId: i < 3 ? 'fetch' : 'write', failureMode: i === 2 ? 'other' : 'timeout' }] }; });
    const all = await f.service.candidates('wf-1');
    const page = await f.service.candidates('wf-1', { nodeId: 'fetch', failureMode: 'timeout', page: 2, pageSize: 1 });
    expect(page.items.map(i => i.itemId)).toEqual(['item-1']);
    expect(page.page.total).toBe(2);
    expect(page.counts.pending).toBe(2);
    expect(page.inputDigest).toBe(all.inputDigest);
    const issue = await f.service.candidates('wf-1', { signature: 'issue-3' });
    expect(issue.items.map(i => i.itemId)).toEqual(['item-3']);
  });
  it('hydrates a single item from its trusted summary without rereading every source', async () => {
    const f = await repairFixture(2); fixtures.push(f);
    let reads = 0;
    const original = f.sourcePort.load;
    f.sourcePort.load = async (...args) => { reads++; return original(...args); };
    f.sourcePort.hydrate = async (_db, _workflow, items) => items.map(item => ({ ...item, context: { evidence: 'hydrated' } }));
    const service = createRepairWorkbenchService(f.db, f.sourcePort);
    expect((await service.item('wf-1', 'item-1')).context).toEqual({ evidence: 'hydrated' });
    expect(reads).toBe(1);
  });
  it('reuses a versioned read snapshot across pages and item reads, but invalidates changed sources', async () => {
    const f = await repairFixture(3); fixtures.push(f);
    let version = 'one'; let reads = 0;
    f.sourcePort.readVersion = async () => version;
    const original = f.sourcePort.load;
    f.sourcePort.load = async (...args) => { reads++; return original(...args); };
    f.sourcePort.hydrate = async (_db, _wf, items) => items;
    const service = createRepairWorkbenchService(f.db, f.sourcePort);
    await service.candidates('wf-1', { pageSize: 1 });
    await service.candidates('wf-1', { page: 2, pageSize: 1 });
    expect((await service.item('wf-1', 'item-1')).itemId).toBe('item-1');
    expect(reads).toBe(1);
    f.items.splice(1); version = 'two';
    expect((await service.candidates('wf-1')).page.total).toBe(1);
    expect(reads).toBe(2);
  });
  it('expires read projections and never uses them as the authority for writes', async () => {
    const f = await repairFixture(2); fixtures.push(f);
    let now = Date.now(); vi.spyOn(Date, 'now').mockImplementation(() => now);
    let reads = 0;
    f.sourcePort.readVersion = async () => 'legacy-same-second';
    const original = f.sourcePort.load;
    f.sourcePort.load = async (...args) => { reads++; return original(...args); };
    const service = createRepairWorkbenchService(f.db, f.sourcePort);
    const first = await service.candidates('wf-1');
    now += 10_001;
    await service.candidates('wf-1');
    expect(reads).toBe(2);
    f.items.splice(1);
    await expect(service.disposition('human', { workflowId: 'wf-1', itemId: 'item-0', inputDigest: first.inputDigest,
      expectedStateVersion: 0, contentRevision: 1, action: 'no_action', reason: 'Later', requestId: 'fresh-only' }))
      .rejects.toMatchObject({ code: 'SOURCE_CHANGED' });
    expect(await f.db.query('SELECT * FROM workflow_repair_items')).toEqual([]);
    expect((await service.candidates('wf-1')).page.total).toBe(1);
  });
  it('returns bounded repair signature keys across every page', async () => {
    const f = await repairFixture(2); fixtures.push(f);
    f.items[0].context = { signature: 'timeout:fetch' };
    const hiddenLongSignature = `retry:${'write'.repeat(20_000)}`;
    f.items[1].context = { signature: hiddenLongSignature };
    const service = createRepairWorkbenchService(f.db, f.sourcePort);
    const first = await service.candidates('wf-1', { page: 1, pageSize: 1 });
    expect(first.items).toHaveLength(1);
    expect(first.repairSignatureKeys).toEqual([repairSignatureKey(hiddenLongSignature), repairSignatureKey('timeout:fetch')].sort());
    expect(first.repairSignatureKeys.every(key => key.length === 32)).toBe(true);
    expect(JSON.stringify(first.repairSignatureKeys).length).toBeLessThan(100);
  });
  it('uses the historical source scope when disposing an item from the historical inbox', async () => {
    const f = await repairFixture(2); fixtures.push(f);
    const [historical, recent] = f.items;
    f.sourcePort.load = async (_db, _workflowId, _mode, scope) => (scope?.includeHistorical ? [historical, recent] : [recent])
      .map(item => ({ item, episodeKey: 'initial' }));
    const service = createRepairWorkbenchService(f.db, f.sourcePort);
    const inbox = await service.candidates('wf-1', { includeHistorical: true });
    await expect(service.disposition('human', { workflowId: 'wf-1', itemId: historical.itemId,
      inputDigest: inbox.inputDigest, expectedStateVersion: 0, contentRevision: 1, action: 'no_action',
      reason: 'Historical issue is no longer actionable', requestId: 'history-ignore', includeHistorical: true }))
      .resolves.toMatchObject({ itemId: historical.itemId, state: 'no_action' });
  });
  it('preserves one persisted lifecycle across proposal-key upgrades and same-action wording changes', async () => {
    const f = await repairFixture(1); fixtures.push(f);
    const operations = [{ op: 'replace', nodeId: 'fetch', path: '/timeoutMs', value: 30_000 }];
    const proposal = (summary: string) => ({ schemaVersion: 'workflow-patch/v1', workflowId: 'wf-1', summary, operations });
    const legacy = (id: string, summary: string) => ({ ...f.items[0], itemId: id, groupKey: 'timeout',
      proposalKey: digestRepairJson({ proposal: proposal(summary), instruction: summary }), proposal: proposal(summary), instruction: summary });
    const oldPending = legacy('legacy-pending', 'Raise timeout');
    const oldIgnored = legacy('legacy-ignored', 'Avoid premature timeout');
    const current = { ...legacy('current', 'Increase timeout safely'),
      proposalKey: digestRepairJson({ schemaVersion: 'workflow-patch/v1', workflowId: 'wf-1', operations }) };
    const repo = new RepairBatchRepository(f.db);
    await f.db.transaction(tx => repo.materializeItem(tx, { workflowId: 'wf-1', episodeKey: 'initial', item: oldPending }));
    await f.db.transaction(tx => repo.materializeItem(tx, { workflowId: 'wf-1', episodeKey: 'initial', item: oldIgnored }));
    await f.db.transaction(tx => repo.setDisposition(tx, { workflowId: 'wf-1', itemId: oldIgnored.itemId,
      expectedStateVersion: 0, action: 'no_action', actorId: 'human', requestId: 'legacy-ignore', reason: 'Later' }));
    f.items.splice(0, 1, current);
    const service = createRepairWorkbenchService(f.db, f.sourcePort);
    const inbox = await service.candidates('wf-1', { includeHistorical: true });
    expect(inbox.items).toHaveLength(1);
    expect(inbox.items[0]).toMatchObject({ itemId: oldIgnored.itemId, state: 'no_action',
      proposal: { summary: 'Avoid premature timeout' }, sourceAvailable: true });
    await expect(service.disposition('human', { workflowId: 'wf-1', itemId: oldIgnored.itemId,
      inputDigest: inbox.inputDigest, expectedStateVersion: 1, contentRevision: 1, action: 'restore',
      reason: 'Reconsider', requestId: 'legacy-restore', includeHistorical: true }))
      .resolves.toMatchObject({ itemId: oldIgnored.itemId, state: 'pending' });
  });
  it.each([39, 100])('freezes %i sources and atomically creates one running task and one draft step', async count => {
    const f = await setup(count);
    const revision = await f.service.create('human', f.request);
    expect(revision.input.items).toHaveLength(count);
    expect(await f.db.query('SELECT task_type, status, created_by FROM ce_tasks')).toEqual([{ task_type: 'workflow_repair', status: 'running', created_by: 'human' }]);
    expect(await f.db.query('SELECT step_type FROM ce_steps')).toEqual([{ step_type: 'workflow_repair_draft' }]);
    expect(f.calls).toHaveLength(1);
    expect(f.calls[0]).toMatchObject({ actorId: 'human', identity: { attempt: 1 }, input: revision.input });
    expect((await f.service.task(revision.taskId)).execution).toMatchObject({ status: 'dispatched', jobId: 'job-1', attempt: 1 });
    expect((await f.service.create('human', f.request)).taskId).toBe(revision.taskId);
    expect(f.calls).toHaveLength(1);
  });
  it('stores a bounded digest for unselected sources instead of copying every source reference', async () => {
    const f = await setup(100);
    const revision = await f.service.create('human', { ...f.request, itemIds: ['item-0'] });
    expect(revision.input.excludedSources).toEqual({ count: 99, digest: expect.stringMatching(/^[a-f0-9]{64}$/) });
    expect(revision.input).not.toHaveProperty('excludedSourceRefs');
    expect(JSON.stringify(revision.input).length).toBeLessThan(20_000);
  });
  it('rejects oversized and cross-workflow requests without using a stale list digest as a manual gate', async () => {
    const f = await setup(101);
    await expect(f.service.create('human', f.request)).rejects.toMatchObject({ code: 'INVALID_INPUT' });
    await expect(f.service.create('human', { ...f.request, itemIds: ['item-0'], instructions: '界'.repeat(22000) })).rejects.toMatchObject({ code: 'PAYLOAD_TOO_LARGE' });
    const other = await f.service.candidates('wf-2');
    await expect(f.service.create('human', { ...f.request, workflowId: 'wf-2', itemIds: ['item-0'], inputDigest: other.inputDigest })).rejects.toMatchObject({ code: 'ITEM_NOT_FOUND' });
    expect(await f.db.query('SELECT * FROM ce_tasks')).toEqual([]);
    const created = await f.service.create('human', { ...f.request, itemIds: ['item-0'], inputDigest: '0'.repeat(64) });
    expect(created.input.items.map(item=>item.itemId)).toEqual(['item-0']);
  });
  it('arbitrates concurrent create attempts and rejects reused requests with changed instructions', async () => {
    const f = await setup();
    const attempts = await Promise.allSettled([f.service.create('human', f.request), f.service.create('human', { ...f.request, requestId: 'create-2' })]);
    expect(attempts.filter(r => r.status === 'fulfilled')).toHaveLength(2);
    expect(f.calls).toHaveLength(1);
    expect(await f.db.query('SELECT * FROM ce_tasks')).toHaveLength(1);
    await expect(f.service.create('human', { ...f.request, instructions: 'changed' })).rejects.toMatchObject({ code: 'IDEMPOTENCY_MISMATCH' });
  });
  it('retains a rejected dispatch as the same frozen input and retries only after explicit rejection', async () => {
    const f = await setup();
    let attempts = 0;
    f.executionPort.dispatch = async () => { if (++attempts === 1) throw new Error('AIS executeSnapshot HTTP 400: invalid request'); return { jobId: 'job-retry' }; };
    const revision = await f.service.create('human', f.request);
    expect(revision.phase).toBe('drafting');
    expect(await f.db.query('SELECT status FROM ce_steps')).toEqual([{ status: 'dispatch_failed' }]);
    f.items.splice(0);
    const retried = await f.service.create('human', f.request);
    expect(retried.input).toEqual(revision.input);
    expect(attempts).toBe(2);
    expect(await f.db.query('SELECT * FROM ce_tasks')).toHaveLength(1);
  });
  it('prevents duplicate dispatch when the first AIS response is uncertain', async () => {
    const f = await setup();
    f.executionPort.dispatch = async () => { throw new Error('unknown outcome'); };
    const revision = await f.service.create('human', f.request);
    expect((await f.service.task(revision.taskId)).execution).toMatchObject({ status: 'dispatch_uncertain', errorCode: 'DISPATCH_UNCERTAIN' });
    await expect(f.service.retryDispatch('human', revision.taskId, 1)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
  });
  it('treats an AIS HTTP 408 as uncertain because the job may already exist', async () => {
    const f = await setup();
    f.executionPort.dispatch = async () => { throw new Error('AIS executeSnapshot HTTP 408: request timeout'); };
    const revision = await f.service.create('human', f.request);
    expect((await f.service.task(revision.taskId)).execution).toMatchObject({ status: 'dispatch_uncertain', errorCode: 'DISPATCH_UNCERTAIN' });
    await expect(f.service.retryDispatch('human', revision.taskId, 1)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
  });
  it('quarantines an interrupted dispatch instead of blindly sending another AIS job', async () => {
    const f = await setup();
    const created = await f.service.create('human', f.request);
    await f.db.exec("UPDATE ce_steps SET status = 'dispatching', gmt_modified = ?", [Math.floor(Date.now() / 1000) - 121]);
    expect(await f.service.reconcile(10)).toBe(0);
    expect((await f.service.task(created.taskId)).execution).toMatchObject({ status: 'dispatch_uncertain', errorCode: 'DISPATCH_UNCERTAIN' });
    await expect(f.service.retryDispatch('human', created.taskId, 1)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
  });
  it('retries an explicitly rejected AIS request without resolving sources again', async () => {
    const f = await setup();
    f.executionPort.dispatch = async () => { throw new Error('AIS executeSnapshot HTTP 400: invalid request'); };
    const revision = await f.service.create('human', f.request);
    expect((await f.service.task(revision.taskId)).execution).toMatchObject({ status: 'dispatch_failed', errorCode: 'DISPATCH_REJECTED' });
    f.executionPort.dispatch = async input => { f.calls.push(input); return { jobId: 'job-retry' }; };
    f.executionPort.resolveBaseline = async () => { throw new Error('must not reload baseline'); };
    f.sourcePort.load = async () => { throw new Error('must not reload sources'); };
    await f.service.retryDispatch('human', revision.taskId, 1);
    expect(f.calls[0]).toMatchObject({ input: revision.input, identity: { attempt: 2 } });
    expect((await f.service.task(revision.taskId)).execution).toMatchObject({ status: 'dispatched', jobId: 'job-retry', attempt: 2 });
    await expect(f.service.retryDispatch('human', revision.taskId, 1)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    expect(await f.db.query('SELECT * FROM workflow_repair_revisions')).toHaveLength(1);
  });
  it('keeps legacy applied sources read-only and blocks an active legacy application', async () => {
    const f = await setup();
    f.sourcePort.load = async () => f.items.map(item => ({ item, episodeKey: 'initial', initialState: 'verified' }));
    const old = await f.service.candidates('wf-1', { includeHistorical: true });
    expect(old.items[0]).toMatchObject({ state: 'verified', sourceAvailable: false });
    await expect(f.service.create('human', { ...f.request, inputDigest: old.inputDigest })).rejects.toMatchObject({ code: 'ITEM_NOT_FOUND' });
    f.sourcePort.load = async () => f.items.map(item => ({ item, episodeKey: 'initial' }));
    await f.db.exec(`INSERT INTO ce_tasks (task_id, task_name, task_type, user_id, bot_id, status, config_json, created_by)
      VALUES ('legacy', 'Old apply', 'suggestion_apply', 'human', 'bot', 'running', ?, 'human')`, [JSON.stringify({ applicationInput: { workflowId: 'wf-1' } })]);
    await expect(f.service.create('human', f.request)).rejects.toMatchObject({ code: 'ACTIVE_TASK_CONFLICT' });
  });
  it('rolls back revision/item ownership when task step persistence fails', async () => {
    const f = await setup();
    f.raw.exec(`CREATE TRIGGER fail_step BEFORE INSERT ON ce_steps BEGIN SELECT RAISE(ABORT, 'step unavailable'); END`);
    await expect(f.service.create('human', f.request)).rejects.toThrow('step unavailable');
    expect(await f.db.query('SELECT * FROM ce_tasks')).toHaveLength(0);
    expect(await f.db.query('SELECT * FROM workflow_repair_revisions')).toHaveLength(0);
    expect(await f.db.query('SELECT * FROM workflow_repair_items')).toHaveLength(0);
    expect(f.calls).toHaveLength(0);
  });
  it('refreshes same-content evidence without undoing no_action or replacing frozen context', async () => {
    const f = await setup(1);
    f.items[0].context = { problem: 'first observation', evidence: ['event-1'] };
    const inbox = await f.service.candidates('wf-1');
    await f.service.disposition('human', { workflowId: 'wf-1', itemId: 'item-0', inputDigest: inbox.inputDigest,
      expectedStateVersion: 0, contentRevision: 1, action: 'no_action', reason: '', requestId: 'ignore' });
    f.items[0].context = { problem: 'more evidence', evidence: ['event-1', 'event-2'] };
    const refreshed = await f.service.candidates('wf-1');
    expect(refreshed.items[0]).toMatchObject({ state: 'no_action', context: f.items[0].context });
    expect(JSON.parse((await f.db.query<{ content_json: string }>('SELECT content_json FROM workflow_repair_items'))[0].content_json).context.evidence).toEqual(['event-1']);
    await f.service.disposition('human', { workflowId: 'wf-1', itemId: 'item-0', inputDigest: refreshed.inputDigest,
      expectedStateVersion: 1, contentRevision: 1, action: 'restore', reason: '', requestId: 'restore' });
    const created = await f.service.create('human', { ...f.request, inputDigest: refreshed.inputDigest });
    expect(created.input.items[0].context?.evidence).toEqual(['event-1', 'event-2']);
    f.items[0].context = { problem: 'third observation', evidence: ['event-3'] };
    await f.service.candidates('wf-1');
    expect((await f.service.task(created.taskId)).latestAttempt.input.items[0].context?.evidence).toEqual(['event-1', 'event-2']);
  });
  it('rejects incomplete or mismatched trusted reports without settling the task', async () => {
    const f = await setup();
    const created = await f.service.create('human', f.request);
    const draft = { candidateCommit: 'd'.repeat(40), parentCandidateCommit: created.input.baseline.packCommit, packDigest: 'e'.repeat(40),
      itemResults: [{ itemId: 'item-0', status: 'changed', reason: 'One only' }] };
    const checks = { schemaVersion: 'workflow-repair-checks/v1', candidateCommit: draft.candidateCommit, packDigest: draft.packDigest,
      candidateDigest: digestRepairJson(draft), static: { status: 'passed' }, mock: { status: 'not_covered' }, coverage: [{ itemId: 'item-0' }] };
    const report = { workflowId: 'wf-1', taskId: created.taskId, revision: 1, expectedStateVersion: 0, status: 'succeeded' as const, draft, checks };
    await expect(f.service.report(report)).rejects.toMatchObject({ code: 'INVALID_INPUT' });
    await expect(f.service.report({ ...report, checks: { ...checks, candidateCommit: 'f'.repeat(40) } })).rejects.toMatchObject({ code: 'INVALID_INPUT' });
    expect((await f.service.task(created.taskId)).latestAttempt.phase).toBe('drafting');
    expect((await f.service.task(created.taskId)).execution?.status).toBe('dispatched');
  });
  const modelInput = (taskId: string, itemId = 'item-0') => ({ itemId, coreInput: 'Exact application prompt', materials: {
    schemaVersion: 'repair-local-materials/v1', taskId, revision: 1, root: '/ais/repair-materials/one',
    files: [{ kind: 'item', path: 'item.json', bytes: 42, sha256: 'a'.repeat(64), purpose: 'Read original goal', itemIds: [itemId] }],
  } });
  const invalidModelInputs: Array<[string, (entry: ReturnType<typeof modelInput>) => unknown]> = [
    ['non-array records', entry => entry],
    ['null records', () => null],
    ['missing item ID', entry => [Object.fromEntries(Object.entries(entry).filter(([key]) => key !== 'itemId'))]],
    ['unknown item ID', entry => [{ ...entry, itemId: 'not-selected' }]],
    ['duplicate item records', entry => [entry, entry]],
    ['unknown model phase', entry => [{ ...entry, phase: 'review' }]],
    ['null model phase', entry => [{ ...entry, phase: null }]],
    ['duplicate generation records', entry => [{ ...entry, phase: 'generation' }, { ...entry, phase: 'generation' }]],
    ['legacy and explicit generation records', entry => [entry, { ...entry, phase: 'generation' }]],
    ['explicit and legacy generation records', entry => [{ ...entry, phase: 'generation' }, entry]],
    ['duplicate planning records', entry => [{ ...entry, phase: 'planning' }, { ...entry, phase: 'planning' }]],
    ['multiple task planning records', entry => [{ ...entry, phase: 'planning' }, { ...entry, phase: 'planning', itemId: 'item-1' }]],
    ['empty core prompt', entry => [{ ...entry, coreInput: '  ' }]],
    ['partially corrupt records', entry => [entry, null]],
    ['null material index', entry => [{ ...entry, materials: null }]],
    ['array material index', entry => [{ ...entry, materials: [] }]],
    ['incomplete material index', entry => [{ ...entry, materials: {} }]],
    ['unknown material schema', entry => [{ ...entry, materials: { ...entry.materials, schemaVersion: 'unknown' } }]],
    ['another task index', entry => [{ ...entry, materials: { ...entry.materials, taskId: 'another-task' } }]],
    ['another revision index', entry => [{ ...entry, materials: { ...entry.materials, revision: 2 } }]],
    ['missing material root', entry => [{ ...entry, materials: { ...entry.materials, root: '' } }]],
    ['non-array file index', entry => [{ ...entry, materials: { ...entry.materials, files: {} } }]],
    ['null file entry', entry => [{ ...entry, materials: { ...entry.materials, files: [null] } }]],
    ['negative material bytes', entry => [{ ...entry, materials: { ...entry.materials, files: [{ ...entry.materials.files[0], bytes: -1 }] } }]],
    ['invalid material hash', entry => [{ ...entry, materials: { ...entry.materials, files: [{ ...entry.materials.files[0], sha256: 'invalid' }] } }]],
    ['unknown material source', entry => [{ ...entry, materials: { ...entry.materials, files: [{ ...entry.materials.files[0], itemIds: ['not-selected'] }] } }]],
    ['duplicate material sources', entry => [{ ...entry, materials: { ...entry.materials, files: [{ ...entry.materials.files[0], itemIds: ['item-0', 'item-0'] }] } }]],
    ['duplicate material paths', entry => [{ ...entry, materials: { ...entry.materials, files: [entry.materials.files[0], entry.materials.files[0]] } }]],
  ];
  it.each(invalidModelInputs)('rejects %s even with a matching candidate digest, leaving the revision unsettled', async (_name, corrupt) => {
    const f = await setup();
    const created = await f.service.create('human', f.request);
    const draft = { candidateCommit: 'd'.repeat(40), parentCandidateCommit: created.input.baseline.packCommit, packDigest: 'e'.repeat(40),
      itemResults: f.items.map(item => ({ itemId: item.itemId, status: 'changed', reason: 'Fixed' })), modelInputs: corrupt(modelInput(created.taskId)) };
    const checks = { schemaVersion: 'workflow-repair-checks/v1', candidateCommit: draft.candidateCommit, packDigest: draft.packDigest,
      candidateDigest: digestRepairJson(draft), static: { status: 'passed' }, mock: { status: 'not_covered' }, coverage: f.items.map(item => ({ itemId: item.itemId })) };
    await expect(f.service.report({ workflowId: 'wf-1', taskId: created.taskId, revision: 1, expectedStateVersion: 0, status: 'succeeded', draft, checks })).rejects.toMatchObject({ code: 'INVALID_INPUT' });
    const task = await f.service.task(created.taskId);
    expect(task.latestAttempt).toMatchObject({ phase: 'drafting', draft: null, checks: null, stateVersion: 0 });
    expect(task.execution?.status).toBe('dispatched');
  });
  it.each(['omitted', 'empty', 'multiple', 'integrated', 'phases', 'planning-legacy', 'generation'])('preserves %s model inputs through report validation and persistence', async mode => {
    const f = await setup();
    const created = await f.service.create('human', f.request);
    const first = modelInput(created.taskId);
    first.materials.files[0].itemIds = ['item-0', 'item-1'];
    first.materials.files.push({ ...first.materials.files[0], kind: 'engine-reference', path: 'engine.json', itemIds: [] });
    const records = mode === 'empty' ? [] : mode === 'multiple' ? [first, modelInput(created.taskId, 'item-1')]
      : mode === 'phases' ? [{ ...first, phase: 'planning' }, { ...first, phase: 'generation' }, { ...modelInput(created.taskId, 'item-1'), phase: 'generation' }]
      : mode === 'planning-legacy' ? [{ ...first, phase: 'planning' }, first]
      : mode === 'generation' ? [{ ...first, phase: 'generation' }] : [first];
    const draft = { candidateCommit: 'd'.repeat(40), parentCandidateCommit: created.input.baseline.packCommit, packDigest: 'e'.repeat(40),
      itemResults: f.items.map(item => ({ itemId: item.itemId, status: 'changed', reason: 'Fixed' })), ...(mode === 'omitted' ? {} : { modelInputs: records }) };
    const checks = { schemaVersion: 'workflow-repair-checks/v1', candidateCommit: draft.candidateCommit, packDigest: draft.packDigest,
      candidateDigest: digestRepairJson(draft), static: { status: 'passed' }, mock: { status: 'not_covered' }, coverage: f.items.map(item => ({ itemId: item.itemId })) };
    await f.service.report({ workflowId: 'wf-1', taskId: created.taskId, revision: 1, expectedStateVersion: 0, status: 'succeeded', draft, checks });
    const task = await f.service.task(created.taskId);
    expect(task.latestAttempt.draft).toEqual(draft);
    expect(task.latestAttempt.checks?.candidateDigest).toBe(digestRepairJson(draft));
    expect(task.latestAttempt.input).toEqual(created.input);
  });
  it('polls the stored AIS job and settles a Git-backed result without a callback', async () => {
    const f = await setup();
    const created = await f.service.create('human', f.request);
    const execution = (await f.service.task(created.taskId)).execution!;
    expect(execution).toMatchObject({ jobId: 'job-1', attempt: 1, rawStatus: null });
    f.setRemoteStatus({ status: 'running', rawStatus: 'executing', errorMessage: null });
    expect(await f.service.reconcile(10)).toBe(0);
    expect((await f.service.task(created.taskId)).execution).toMatchObject({ status: 'running', rawStatus: 'executing' });
    const draft = { candidateCommit: 'd'.repeat(40), parentCandidateCommit: created.input.baseline.packCommit, packDigest: 'e'.repeat(40),
      itemResults: f.items.map(i => ({ itemId: i.itemId, status: 'changed', reason: 'Fixed' })) };
    const checks = { schemaVersion: 'workflow-repair-checks/v1', candidateCommit: draft.candidateCommit, packDigest: draft.packDigest,
      candidateDigest: digestRepairJson(draft), static: { status: 'passed' }, mock: { status: 'not_covered' }, coverage: f.items.map(i => ({ itemId: i.itemId })) };
    f.setGeneratedResult({ status: 'succeeded', draft, checks });
    f.setRemoteStatus({ status: 'success', rawStatus: 'success', errorMessage: null });
    expect(await f.service.reconcile(10)).toBe(1);
    expect((await f.service.task(created.taskId)).latestAttempt.phase).toBe('review');
    expect((await f.service.task(created.taskId)).execution).toMatchObject({ status: 'succeeded', jobId: 'job-1' });
  });
  it('fails explicitly when AIS succeeds but Git has no valid result, without automatic rereads', async () => {
    const f = await setup();
    const created = await f.service.create('human', f.request);
    f.setRemoteStatus({ status: 'success', rawStatus: 'success', errorMessage: null });
    expect(await f.service.reconcile(10)).toBe(1);
    const row = (await f.db.query<{ output_json: string }>('SELECT output_json FROM ce_steps'))[0];
    const output = JSON.parse(row.output_json);
    expect(output.successObservedAtMs).toEqual(expect.any(Number));
    await f.db.exec('UPDATE ce_steps SET output_json = ?', [JSON.stringify({ ...output, successObservedAtMs: Date.now() - 301_000 })]);
    expect(await f.service.reconcile(10)).toBe(0);
    expect((await f.service.task(created.taskId)).latestAttempt).toMatchObject({ phase: 'failed', error: { code: 'GIT_RESULT_UNAVAILABLE' } });
  });
  it('settles a timed-out AIS job and requests remote stop', async () => {
    const f = await setup();
    const created = await f.service.create('human', f.request);
    const row = (await f.db.query<{ output_json: string }>('SELECT output_json FROM ce_steps'))[0];
    await f.db.exec('UPDATE ce_steps SET output_json = ?', [JSON.stringify({
      ...JSON.parse(row.output_json), requestedAtMs: Date.now() - 2_401_000,
    })]);
    expect(await f.service.reconcile(10)).toBe(1);
    expect(f.stops).toHaveLength(1);
    expect((await f.service.task(created.taskId)).latestAttempt).toMatchObject({ phase: 'failed', error: { code: 'AIS_TIMEOUT' } });
  });
  it('settles an AIS no-change result without offering an unchanged Pack for review', async () => {
    const f = await setup();
    const created = await f.service.create('human', f.request);
    const draft = { candidateCommit: 'd'.repeat(40), parentCandidateCommit: created.input.baseline.packCommit, packDigest: 'e'.repeat(40),
      itemResults: f.items.map(i => ({ itemId: i.itemId, status: 'not_needed', reason: 'No Pack edit required' })) };
    const checks = { schemaVersion: 'workflow-repair-checks/v1', candidateCommit: draft.candidateCommit, packDigest: draft.packDigest,
      candidateDigest: digestRepairJson(draft), static: { status: 'passed' }, mock: { status: 'not_covered' }, coverage: f.items.map(i => ({ itemId: i.itemId })) };
    f.setGeneratedResult({ status: 'no_change', draft, checks });
    f.setRemoteStatus({ status: 'success', rawStatus: 'success', errorMessage: null });
    expect(await f.service.reconcile(10)).toBe(1);
    const task = await f.service.task(created.taskId);
    expect(task.latestAttempt.phase).toBe('no_change');
    expect(task.execution?.status).toBe('succeeded');
    expect((await f.service.candidates('wf-1')).items.every(i => i.state === 'pending')).toBe(true);
  });
  it('retains a later revision submitter independently of the task creator and cancelling operator', async () => {
    const f = await setup();
    const first = await f.service.create('task-creator', f.request);
    await f.service.report({ workflowId: 'wf-1', taskId: first.taskId, revision: 1,
      expectedStateVersion: 0, status: 'failed', error: { code: 'MODEL_FAILED' } });
    f.setRemoteStatus({ status: 'failed', rawStatus: 'failed', errorMessage: null });
    await f.service.retryGeneration('retry-operator', first.taskId, { expectedRevision: 1, requestId: 'retry-other-user' });
    await f.service.cancel('cancelling-operator', first.taskId, 2);
    expect(f.calls[1].actorId).toBe('retry-operator');
    expect(f.stops).toHaveLength(1);
    expect(f.stops[0].actorId).toBe('retry-operator');
  });
  it('retains the original submitter when another operator cancels the task', async () => {
    const f = await setup();
    const created = await f.service.create('human', f.request);
    await f.service.cancel('another-operator', created.taskId, 1);
    expect(f.stops).toHaveLength(1);
    expect(f.stops[0]).toMatchObject({ actorId: 'human', jobId: 'job-1' });
  });
  it('persists cancellation before best-effort stopping the exact AIS attempt', async () => {
    const f = await setup();
    const created = await f.service.create('human', f.request);
    await f.service.cancel('human', created.taskId, 1);
    expect((await f.service.task(created.taskId)).latestAttempt.phase).toBe('cancelled');
    expect(f.stops).toHaveLength(1);
    expect(f.stops[0]).toMatchObject({ jobId: 'job-1', identity: { attempt: 1 }, input: created.input });
  });
  it('projects oversized inbox reads without dropping items or changing frozen sources', async () => {
    const f = await setup(13);
    f.items.forEach(item => { item.context = { evidence: 'x'.repeat(350000) }; });
    const outcome = await f.service.candidates('wf-1');
    expect(outcome.items).toHaveLength(13);
    expect(Buffer.byteLength(JSON.stringify(outcome))).toBeLessThan(64 * 1024);
    expect((await f.service.item('wf-1', 'item-0')).context?.evidence).toBe(f.items[0].context?.evidence);
    expect(await f.db.query('SELECT * FROM workflow_repair_items')).toHaveLength(0);
  });
  it('recovers a late Git result on the same revision without dispatching another job', async () => {
    const f = await setup();
    const first = await f.service.create('human', f.request);
    f.setRemoteStatus({ status: 'success', rawStatus: 'success', errorMessage: null });
    await f.service.report({ workflowId: 'wf-1', taskId: first.taskId, revision: 1,
      expectedStateVersion: 0, status: 'failed', error: { code: 'GIT_RESULT_UNAVAILABLE' } });
    const draft = { candidateCommit: 'd'.repeat(40), parentCandidateCommit: first.input.baseline.packCommit,
      packDigest: 'e'.repeat(40), itemResults: f.items.map(i => ({ itemId: i.itemId, status: 'changed', reason: 'Fixed' })) };
    const checks = { schemaVersion: 'workflow-repair-checks/v1', candidateCommit: draft.candidateCommit,
      packDigest: draft.packDigest, candidateDigest: digestRepairJson(draft), static: { status: 'passed' },
      mock: { status: 'not_covered' }, coverage: f.items.map(i => ({ itemId: i.itemId })) };
    f.setGeneratedResult({ status: 'succeeded', draft, checks });
    await f.service.retryResult('human', first.taskId, 1);
    expect((await f.service.task(first.taskId)).latestAttempt).toMatchObject({ revision: 1, phase: 'review', error: { code: 'GIT_RESULT_UNAVAILABLE' } });
    expect(f.calls).toHaveLength(1);
    await f.service.retryResult('human', first.taskId, 1);
    expect(f.calls).toHaveLength(1);
  });
  it('retries a failed first generation with frozen input and one idempotent next revision', async () => {
    const f = await setup();
    const first = await f.service.create('human', { ...f.request, instructions: 'Keep business rules' });
    f.setRemoteStatus({ status: 'failed', rawStatus: 'failed', errorMessage: 'model failed' });
    await f.service.reconcile();
    f.items.push({ ...f.items[0], itemId: 'new-item', proposalKey: 'f'.repeat(64) });
    const retry = { expectedRevision: 1, requestId: 'retry-first' };
    const next = await f.service.retryGeneration('human', first.taskId, retry);
    expect(next.input.items).toEqual(first.input.items);
    expect(next.input.instructions).toBe('Keep business rules');
    expect(next.input.parentCandidateCommit).toBeNull();
    expect(next.revision).toBe(2);
    expect(await f.service.retryGeneration('human', first.taskId, retry)).toEqual(next);
    expect(f.calls).toHaveLength(2);
    await expect(f.service.report({ workflowId: 'wf-1', taskId: first.taskId, revision: 1,
      expectedStateVersion: 0, status: 'failed', error: { code: 'LATE' } })).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
  });
  it.each(['AIS_FAILED', 'AIS_STOPPED', 'AIS_TIMEOUT'])('recovers an already-pushed candidate before retrying %s', async code => {
    const f = await setup();
    const first = await f.service.create('human', f.request);
    await f.service.report({ workflowId: 'wf-1', taskId: first.taskId, revision: 1,
      expectedStateVersion: 0, status: 'failed', error: { code } });
    f.setRemoteStatus({ status: 'failed', rawStatus: 'failed', errorMessage: 'Failed after Git push' });
    const draft = { candidateCommit: 'd'.repeat(40), parentCandidateCommit: first.input.baseline.packCommit,
      packDigest: 'e'.repeat(40), itemResults: f.items.map(i => ({ itemId: i.itemId, status: 'changed', reason: 'Fixed before platform failure' })) };
    f.setGeneratedResult({ status: 'succeeded', draft, checks: { schemaVersion: 'workflow-repair-checks/v1',
      candidateCommit: draft.candidateCommit, packDigest: draft.packDigest, candidateDigest: digestRepairJson(draft),
      static: { status: 'passed' }, mock: { status: 'not_covered' }, coverage: f.items.map(i => ({ itemId: i.itemId })) } });
    const originalRead = f.executionPort.readResult;
    f.executionPort.readResult = vi.fn().mockRejectedValueOnce(new Error('Git transport unavailable')).mockImplementation(originalRead);
    const retry = { expectedRevision: 1, requestId: 'recover-post-push' };
    await expect(f.service.retryGeneration('human', first.taskId, retry)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    expect((await f.service.task(first.taskId)).revisions).toHaveLength(1);
    const recovered = await f.service.retryGeneration('human', first.taskId, retry);
    expect(recovered).toMatchObject({ revision: 1, phase: 'review', error: { code } });
    expect(await f.service.retryGeneration('human', first.taskId, retry)).toEqual(recovered);
    expect((await f.service.task(first.taskId)).revisions).toHaveLength(1);
    expect(f.calls).toHaveLength(1);
  });
  it('does not retry a timed-out generation while the remote job still runs', async () => {
    const f = await setup();
    const first = await f.service.create('human', f.request);
    await f.service.report({ workflowId: 'wf-1', taskId: first.taskId, revision: 1,
      expectedStateVersion: 0, status: 'failed', error: { code: 'AIS_TIMEOUT' } });
    await expect(f.service.retryGeneration('human', first.taskId, { expectedRevision: 1, requestId: 'retry-timeout' }))
      .rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    expect(f.calls).toHaveLength(1);
  });
  it('settles trusted success to review and failed iteration retains success; cancellation releases items', async () => {
    const f = await setup();
    f.executionPort.dispatch = async () => { throw new Error('uncertain dispatch'); };
    const r1 = await f.service.create('human', f.request);
    const draft = { candidateCommit: 'd'.repeat(40), parentCandidateCommit: r1.input.baseline.packCommit, packDigest: 'e'.repeat(40),
      itemResults: f.items.map(i => ({ itemId: i.itemId, status: 'changed', reason: 'Fixed' })) };
    const checks = { schemaVersion: 'workflow-repair-checks/v1', candidateCommit: draft.candidateCommit, packDigest: draft.packDigest,
      candidateDigest: digestRepairJson(draft), static: { status: 'passed' }, mock: { status: 'not_covered' }, coverage: f.items.map(i => ({ itemId: i.itemId })) };
    await f.service.report({ workflowId: 'wf-1', taskId: r1.taskId, revision: 1, expectedStateVersion: 0, status: 'succeeded', draft, checks });
    expect((await f.service.task(r1.taskId)).latestAttempt.phase).toBe('review');
    expect((await f.service.task(r1.taskId)).execution).toMatchObject({ status: 'succeeded', errorCode: null, attempt: 1 });
    expect((await f.db.query<{ status: string }>('SELECT status FROM ce_tasks'))[0].status).toBe('running');
    f.executionPort.resolveBaseline = async () => { throw new Error('feedback must retain frozen baseline after deployment changes'); };
    const r2 = await f.service.revise('human', r1.taskId, { ...f.request, requestId: 'revise-2', expectedAttemptRevision: 1, parentCandidateCommit: draft.candidateCommit, feedback: 'Try more' });
    await f.service.report({ workflowId: 'wf-1', taskId: r2.taskId, revision: 2, expectedStateVersion: 0, status: 'failed', error: { code: 'MODEL_FAILED' } });
    const task = await f.service.task(r1.taskId);
    expect(task.latestSuccessful?.revision).toBe(1);
    expect(task.latestAttempt.phase).toBe('failed');
    expect(JSON.parse((await f.db.query<{ config_json: string }>('SELECT config_json FROM ce_tasks'))[0].config_json)).toMatchObject({ latestAttemptRevision: 2, latestSuccessfulRevision: 1 });
    await f.service.cancel('human', r1.taskId, 2);
    expect((await f.service.candidates('wf-1')).items.every(i => i.state === 'pending')).toBe(true);
    await expect(f.service.report({ workflowId: 'wf-1', taskId: r2.taskId, revision: 2, expectedStateVersion: 1, status: 'failed', error: { code: 'LATE' } })).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
  });
});

it('allows independent scopes and reuses the same frozen scope instead of generating it twice', async () => {
  const f = await repairFixture(2);
  try {
    const service = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
    const inbox = await service.candidates('wf-1');
    const request = { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: inbox.inputDigest, instructions: '', requestId: 'first' };
    const first = await service.create('human', request);
    const reused = await service.create('human', { ...request, requestId: 'same-scope' });
    expect(reused.taskId).toBe(first.taskId); expect(f.calls).toHaveLength(1);
    const independent = await service.create('human', { ...request, itemIds: ['item-1'], requestId: 'independent' });
    expect(independent.taskId).not.toBe(first.taskId); expect(f.calls).toHaveLength(2);
  } finally { await f.db.close(); }
});

it('retains ignored evidence until expiry or materially new high-impact evidence, without rewriting its audit', async () => {
  const f = await repairFixture(1);
  const service = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
  try {
    f.items[0].context = { diagnoses: [{ flowId: 'old', severity: 'high', evidenceEventIds: ['event-old'] }] };
    const inbox = await service.candidates('wf-1');
    const untilMs = Date.now() + 1000;
    await service.disposition('actor', { workflowId: 'wf-1', itemId: 'item-0', inputDigest: inbox.inputDigest,
      contentRevision: 1, expectedStateVersion: 0, action: 'no_action', reason: '等待业务窗口', requestId: 'defer', untilMs });
    expect((await service.candidates('wf-1')).items.some(item => item.previousItemId === 'item-0')).toBe(false);
    f.items[0].context = { diagnoses: [{ flowId: 'old', severity: 'high', evidenceEventIds: ['event-old', 'event-critical-new'] }] };
    const fresh = await service.candidates('wf-1');
    expect(fresh.items.some(item => item.previousItemId === 'item-0' && item.state === 'pending')).toBe(true);
    const now = vi.spyOn(Date, 'now').mockReturnValue(untilMs + 1000);
    const expired = await service.candidates('wf-1');
    expect(expired.items.some(item => item.previousItemId === 'item-0' && item.context?.dispositionResume)).toBe(true);
    now.mockRestore();
    const stored = (await f.db.query<{ disposition_json: string }>('SELECT disposition_json FROM workflow_repair_items WHERE item_id = ?', ['item-0']))[0];
    expect(JSON.parse(stored.disposition_json)).toMatchObject({ reason: '等待业务窗口', untilMs, evidenceEventIds: ['event-old'] });
  } finally { vi.restoreAllMocks(); await f.db.close(); }
});
