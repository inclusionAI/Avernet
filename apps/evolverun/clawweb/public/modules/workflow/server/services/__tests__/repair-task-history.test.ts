// @vitest-environment node
import { afterEach, expect, it } from 'vitest';
import { repairFixture } from './repair-batch-fixtures.js';
import { createRepairWorkbenchService } from '../repair-batch-service.js';
import { projectTaskHistory } from '../repair-task-history.js';
import { createRepairBatchesRouter } from '../../routes/repair-batches.js';
import { RepairTaskRepository } from '../../repositories/repair-task-repository.js';
import express from 'express';
import type { Server } from 'node:http';
const cleanup: Array<() => Promise<unknown>> = [];
afterEach(async () => { for (const close of cleanup.splice(0)) await close(); });
it('keeps an internal task larger than 8MiB and pages its complete frozen history over bounded HTTP', async () => {
  const f = await repairFixture(1); cleanup.push(() => f.db.close());
  const service = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
  const created = await service.create('owner', { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: (await service.candidates('wf-1')).inputDigest, requestId: 'first', instructions: '' });
  for (let revision = 2; revision <= 50; revision++) {
    const input = { ...created.input, revision, items: [{ ...created.input.items[0], context: { evidence: 'x'.repeat(200_000) } }] };
    await f.db.exec(`INSERT INTO workflow_repair_revisions (task_id,revision,workflow_id,phase,request_id,request_digest,request_key,input_json,created_at_ms,updated_at_ms)
      VALUES (?,?,'wf-1','failed',?,'digest',?,?,0,0)`, [created.taskId, revision, `request-${revision}`, `key-${revision}`, JSON.stringify(input)]);
  }
  for (const revision of [25,50]) await new RepairTaskRepository(f.db).append({...created,revision,input:{...created.input,revision}},'owner');
  const task = await service.task(created.taskId);
  expect(Buffer.byteLength(JSON.stringify(task))).toBeGreaterThan(8 * 1024 * 1024);
  expect(task.revisions).toHaveLength(50);
  const one = projectTaskHistory(task, 1, 20), two = projectTaskHistory(task, 2, 20);
  expect(one.revisionPage).toMatchObject({ page: 1, total: 50, totalPages: 3 });
  expect(one.revisions).toHaveLength(20); expect(two.revisions).toHaveLength(20);
  expect(new Set([...one.revisions, ...two.revisions].map(r => r.revision)).size).toBe(40);
  expect(one.latestAttempt).toEqual(task.latestAttempt);
  expect(Buffer.byteLength(JSON.stringify(one))).toBeLessThan(8 * 1024 * 1024);
  const app = express(); app.use('/repairs', createRepairBatchesRouter({ service, authorize: async () => ({ actorId: 'owner', canEdit: true }) }));
  const server = await new Promise<Server>(resolve => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
  cleanup.push(() => new Promise<void>(resolve => server.close(() => resolve())));
  const url = `http://127.0.0.1:${(server.address() as { port: number }).port}/repairs/${created.taskId}?revisionPage=3`;
  const response = await fetch(url), body = await response.json();
  expect(response.status).toBe(200); expect(body.revisionPage).toMatchObject({ page: 3, total: 50 });
  expect(body.revisions).toHaveLength(10); expect(body.revisions.at(-1).revision).toBe(1);
  expect(body.executions[1]).toMatchObject({status:'dispatched',jobId:'job-1',attempt:1});
  expect(Object.keys(body.executions).sort()).toEqual(['1','50']);
  expect(body.latestAttempt.execution).toBeUndefined();
  expect(Buffer.byteLength(JSON.stringify(body))).toBeLessThan(8 * 1024 * 1024);
});
it('returns each stored execution with its own revision and does not substitute a missing historical record',async()=>{
 const f=await repairFixture(1);cleanup.push(()=>f.db.close());
 const service=createRepairWorkbenchService(f.db,f.sourcePort,f.executionPort);
 const first=await service.create('owner',{workflowId:'wf-1',itemIds:['item-0'],inputDigest:(await service.candidates('wf-1')).inputDigest,requestId:'first',instructions:''});
 await service.report({workflowId:'wf-1',taskId:first.taskId,revision:1,expectedStateVersion:first.stateVersion,status:'failed',error:{code:'AIS_FAILED'}});
 f.setRemoteStatus({status:'failed',rawStatus:'failed',errorMessage:null});
 await service.retryGeneration('owner',first.taskId,{expectedRevision:1,requestId:'second'});
 await f.db.exec("UPDATE ce_steps SET bot_run_id='job-history', output_json=? WHERE task_id=? AND round_no=1",[JSON.stringify({attempt:1,executionId:'exec-history',rawStatus:'failed'}),first.taskId]);
 // Keep the existing step-ID binding even when legacy round metadata is stale.
 await f.db.exec("UPDATE ce_steps SET round_no=0, bot_run_id='job-latest', output_json=? WHERE task_id=? AND round_no=2",[JSON.stringify({attempt:1,executionId:'exec-latest',rawStatus:'running'}),first.taskId]);
 const task=await service.task(first.taskId);
 expect(task.executions?.[1]).toMatchObject({status:'failed',jobId:'job-history',executionId:'exec-history'});
 expect(task.executions?.[2]).toMatchObject({jobId:'job-latest',executionId:'exec-latest'});
 expect(task.execution).toEqual(task.executions?.[2]);
 await f.db.exec("UPDATE ce_steps SET output_json='null' WHERE task_id=? AND round_no=1",[first.taskId]);
 expect((await service.task(first.taskId)).executions?.[1]).toMatchObject({status:'failed',executionId:null,rawStatus:null});
 await f.db.exec('DELETE FROM ce_steps WHERE task_id=? AND round_no=1',[first.taskId]);
 expect((await service.task(first.taskId)).executions?.[1]).toBeUndefined();
});
it('splits very large revisions into byte-bounded pages without dropping records', async () => {
  const f = await repairFixture(1); cleanup.push(() => f.db.close());
  const service = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
  const first = await service.create('owner', { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: (await service.candidates('wf-1')).inputDigest, requestId: 'first', instructions: '' });
  const revisions = Array.from({ length: 25 }, (_, i) => ({ ...first, revision: 25 - i, input: { ...first.input, items: [{ ...first.input.items[0], context: { value: 'x'.repeat(800_000) } }] } }));
  const task = { ...await service.task(first.taskId), revisions, latestAttempt: revisions[0] };
  const initial = projectTaskHistory(task);
  const observed: number[] = [];
  for (let page = 1; page <= initial.revisionPage!.totalPages; page++) {
    const projected = projectTaskHistory(task, page);
    expect(Buffer.byteLength(JSON.stringify(projected))).toBeLessThan(8 * 1024 * 1024);
    observed.push(...projected.revisions.map(r => r.revision));
  }
  expect(observed).toEqual(revisions.map(r => r.revision));
});
it('hydrates a bounded group with one full source load instead of reloading per item', async () => {
  const f = await repairFixture(100); cleanup.push(() => f.db.close());
  const original = f.sourcePort.load; const modes: string[] = [];
  f.sourcePort.load = async (...args) => { modes.push(args[2] ?? 'full'); return original(...args); };
  const service = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
  const items = await service.items('wf-1', f.items.map(item => item.itemId).reverse());
  expect(items.map(item => item.itemId)).toEqual(f.items.map(item => item.itemId).reverse());
  expect(modes.filter(mode => mode === 'full')).toHaveLength(1);
  await expect(service.items('wf-1', Array.from({ length: 101 }, (_, i) => `item-${i}`))).rejects.toMatchObject({ code: 'INVALID_INPUT' });
});
