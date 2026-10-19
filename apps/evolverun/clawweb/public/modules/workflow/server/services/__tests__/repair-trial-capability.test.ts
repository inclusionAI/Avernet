// @vitest-environment node
import { expect, it, vi } from 'vitest';
import { RepairBatchError, digestRepairJson } from '../../contracts/repair-batch.js';
import { createRepairTrialService } from '../repair-trial-service.js';

it.each(['pinned', 'saved', undefined])('requires trusted selected-Bot preparation regardless of browser mode=%s', async mode => {
  const schema = { type: 'object', properties: { query: { type: 'string' } } };
  const target = { id: 'prod:owner:bot', environment: 'prod', botId: 'bot', ownerId: 'owner', label: 'Bot' };
  const prepare = vi.fn(async () => { throw new RepairBatchError('CAPABILITY_UNAVAILABLE', '请升级所选 Bot 的 ClawMind'); });
  const save = vi.fn(), dispatch = vi.fn(), transaction = vi.fn();
  const revision = { revision: 1, stateVersion: 2, phase: 'review', checks: { static: { status: 'passed' } },
    draft: { candidateCommit: 'a'.repeat(40), packDigest: 'b'.repeat(40) }, input: { baseline: { packId: 'pack' } } };
  const service = createRepairTrialService({ db: { query: async () => [], transaction } as never,
    workbench: { task: async () => ({ workflowId: 'wf', latestSuccessful: revision, latestAttempt: revision }) } as never,
    port: { targets: async () => [target], prepare, inspect: async () => ({ inputSchema: schema }), save, dispatch } as never });
  await expect(service.start('actor', 'task', { mode, revision: 1, expectedStateVersion: 2, requestId: 'request',
    targetId: target.id, input: { query: 'hello' }, inputSchemaDigest: digestRepairJson(schema) } as never)).rejects.toMatchObject({ code: 'CAPABILITY_UNAVAILABLE' });
  expect(prepare).toHaveBeenCalledWith(expect.objectContaining({ candidateCommit: 'a'.repeat(40) }), 'actor', target.id);
  expect(save).not.toHaveBeenCalled(); expect(dispatch).not.toHaveBeenCalled(); expect(transaction).not.toHaveBeenCalled();
});
