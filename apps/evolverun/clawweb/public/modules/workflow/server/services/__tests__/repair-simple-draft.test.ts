import { afterEach, expect, it, vi } from 'vitest';
import { repairFixture } from './repair-batch-fixtures.js';
import { createRepairWorkbenchService } from '../repair-batch-service.js';

const cleanups: Array<() => Promise<void>> = [];
afterEach(async () => { for (const cleanup of cleanups.splice(0)) await cleanup(); });
it('generates a manual draft from selected originals without source-digest, baseline or integration prerequisites', async () => {
  const f = await repairFixture(); cleanups.push(() => f.db.close());
  f.items.forEach((item, index) => { item.proposal = { summary: `Goal ${index}`, baseSpecDigest: 'f'.repeat(64),
    operations: [{ op: 'replace', nodeId: 'fetch', path: '/executor/timeoutMs', value: index ? 600 : 120 }] }; });
  const prepare = vi.fn(async () => { throw new Error('integration not ready'); });
  const coordinator = { prepare, present: async (_wf: string, items: unknown[]) => items } as never;
  const service = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort, coordinator);
  const result = await service.create('human', { workflowId: 'wf-1', itemIds: ['item-0', 'item-1'], inputDigest: '0'.repeat(64),
    selection: { 'item-0': '1'.repeat(64), 'item-1': '2'.repeat(64) }, instructions: 'Keep both goals for draft review', requestId: 'simple' });
  expect(result.input.items.map(item => item.itemId)).toEqual(['item-0', 'item-1']);
  expect(result.input.items[0].proposal).toEqual(f.items[0].proposal);
  expect(result.input.items[1].proposal).toEqual(f.items[1].proposal);
  expect(result.phase).toBe('drafting'); expect(f.calls).toHaveLength(1);
  expect(prepare).not.toHaveBeenCalled();
});
