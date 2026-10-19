import { afterEach, expect, it } from 'vitest';
import { createRepairWorkbenchService } from '../repair-batch-service.js';
import { repairFixture } from './repair-batch-fixtures.js';
import { RepairBatchError } from '../../contracts/repair-batch.js';

const close: Array<() => Promise<void>> = [];
afterEach(async () => { for (const fn of close.splice(0)) await fn(); });

it('classifies baseline read failure as not created and permits the same request after repair', async () => {
  const f = await repairFixture(); close.push(() => f.db.close());
  const resolve = f.executionPort.resolveBaseline;
  f.executionPort.resolveBaseline = async () => { throw new Error("fatal: path 'workflow.pack.yaml' does not exist in deployed commit"); };
  const service = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
  const request = { workflowId: 'wf-1', itemIds: ['item-0', 'item-1'], inputDigest: (await service.candidates('wf-1')).inputDigest,
    instructions: 'preserve constraints', requestId: '5b9650ad-7056-406d-a41f-4cbcf27024a4' };
  await expect(service.create('human', request)).rejects.toMatchObject({ code: 'CAPABILITY_UNAVAILABLE',
    details: { reasonCode: 'BASELINE_READ_FAILED', stage: 'baseline', outcome: 'not_created' } });
  expect(f.calls).toHaveLength(0);
  expect(await f.db.query('SELECT * FROM workflow_repair_revisions')).toEqual([]);
  f.executionPort.resolveBaseline = resolve;
  const created = await service.create('human', request);
  expect(created.phase).toBe('drafting'); expect(created.input.items).toHaveLength(2);
  f.executionPort.resolveBaseline = async () => { throw new Error('later read unavailable'); };
  expect((await service.create('human', request)).taskId).toBe(created.taskId);
  expect(f.calls).toHaveLength(1);
});

it('also classifies the final baseline recheck before freezing as not created', async () => {
  const f = await repairFixture(); close.push(() => f.db.close());
  const resolve = f.executionPort.resolveBaseline; let reads = 0;
  f.executionPort.resolveBaseline = async id => { if (++reads === 2) throw new Error('final baseline read failed'); return resolve(id); };
  const service = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
  const snapshot = await service.candidates('wf-1');
  await expect(service.create('human', { workflowId: 'wf-1', itemIds: ['item-0', 'item-1'], inputDigest: snapshot.inputDigest,
    instructions: '', requestId: 'final-check' })).rejects.toMatchObject({ details: { outcome: 'not_created', stage: 'baseline' } });
  expect(await f.db.query('SELECT * FROM workflow_repair_revisions')).toEqual([]);
  expect(f.calls).toHaveLength(0);
});

it('retains typed semantic baseline rejection instead of masking it as transport uncertainty', async () => {
  const f = await repairFixture(); close.push(() => f.db.close());
  const failure = new RepairBatchError('SOURCE_CHANGED', 'mismatch', { reasonCode: 'BASELINE_CONTENT_MISMATCH' });
  f.executionPort.resolveBaseline = async () => { throw failure; };
  const service = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
  await expect(service.create('human', { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: 'a'.repeat(64),
    instructions: '', requestId: 'typed' })).rejects.toBe(failure);
  expect(f.calls).toHaveLength(0);
});
