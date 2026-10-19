import { afterEach, expect, it } from 'vitest';
import { repairFixture } from './repair-batch-fixtures.js';
import { createRepairWorkbenchService } from '../repair-batch-service.js';

const cleanups: Array<() => Promise<void>> = [];
afterEach(async () => { for (const close of cleanups.splice(0)) await close(); });
it.each(['awaiting_verification', 'verified', 'ineffective'])('reselects %s into an independent draft without overwriting the previous round', async state => {
  const f = await repairFixture(1); cleanups.push(() => f.db.close());
  // A real source reader only returns selected full inputs, not every source.
  const load = f.sourcePort.load;
  f.sourcePort.load = async (db, workflowId, mode, scope) => (await load(db, workflowId, mode, scope))
    .filter(row => mode !== 'full' || !scope?.itemIds || scope.itemIds.includes(row.item.itemId));
  const service = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
  const request = { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: '0'.repeat(64), instructions: '', requestId: 'round-one' };
  const first = await service.create('human', request);
  // Existing publication/verification records are test setup, never real dispatch.
  await f.db.exec("UPDATE workflow_repair_revisions SET phase = 'published' WHERE task_id = ?", [first.taskId]);
  await f.db.exec('UPDATE workflow_repair_items SET state = ? WHERE item_id = ?', [state, 'item-0']);
  const old = (await f.db.query('SELECT * FROM workflow_repair_items WHERE item_id = ?', ['item-0']))[0];
  f.items[0].sources.push({ ...f.items[0].sources[0], suggestionId: 'additional-evidence' });
  const second = await service.create('human', { ...request, requestId: 'round-two' });
  expect(second.taskId).not.toBe(first.taskId);
  expect(second.revision).toBe(1);
  expect(second.input.items[0]).toMatchObject({ previousItemId: 'item-0', proposal: f.items[0].proposal, sources: f.items[0].sources });
  expect(second.input.items[0].itemId).not.toBe('item-0');
  expect((await f.db.query('SELECT * FROM workflow_repair_items WHERE item_id = ?', ['item-0']))[0]).toEqual(old);
  expect((await service.task(first.taskId)).roundNumber).toBe(1);
  expect((await service.task(second.taskId)).roundNumber).toBe(2);
  expect((await service.create('human', { ...request, requestId: 'round-two' })).taskId).toBe(second.taskId);
  expect(f.calls).toHaveLength(2);
  expect((await service.items('wf-1', [second.input.items[0].itemId]))[0].sourceAvailable).toBe(true);
  await f.db.exec("UPDATE workflow_repair_revisions SET phase = 'published' WHERE task_id = ?", [second.taskId]);
  await f.db.exec("UPDATE workflow_repair_items SET state = 'verified' WHERE item_id = ?", [second.input.items[0].itemId]);
  const third = await service.create('human', { ...request, itemIds: [second.input.items[0].itemId], requestId: 'round-three' });
  expect(third.input.items[0].previousItemId).toBe(second.input.items[0].itemId);
  expect((await service.items('wf-1', [third.input.items[0].itemId]))[0].sourceAvailable).toBe(true);
});
