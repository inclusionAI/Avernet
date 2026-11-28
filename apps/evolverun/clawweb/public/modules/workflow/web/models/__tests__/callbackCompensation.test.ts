import { expect, it } from 'vitest';
import {
  callbackCompensationBatchResolved,
  callbackCompensationCanExecute,
  callbackCompensationDefaultRange,
  callbackCompensationRange,
  callbackCompensationStorageKey,
} from '../callbackCompensation';
import {
  callbackCompensationBatch as batch,
  callbackCompensationCapabilities as capabilities,
  callbackCompensationPreview as preview,
} from '../../components/callback-compensation/__tests__/fixtures';

it('validates absolute date ranges and at most 100 unique run IDs', () => {
  expect(callbackCompensationRange('cfg', '2026-11-27T08:00', '2026-11-28T08:00', 'a, b\na')).toEqual(
    expect.objectContaining({ configId: 'cfg', flowIds: ['a', 'b'] }),
  );
  for (const ids of [Array.from({ length: 101 }, (_, i) => `run-${i}`).join('\n')]) {
    expect(() => callbackCompensationRange('cfg', '2026-11-27T08:00', '2026-11-28T08:00', ids)).toThrow(/100/);
  }
  expect(() => callbackCompensationRange('cfg', 'invalid', '2026-11-28T08:00', '')).toThrow();
  expect(() => callbackCompensationRange('cfg', '2026-11-28T08:00', '2026-11-27T08:00', '')).toThrow();
});
it('binds execution to current server eligibility, capability, preview TTL and reason/acknowledgement', () => {
  const valid = {
    capabilities,
    preview,
    itemIds: ['eligible'],
    reason: '人工核实后只补发通知消息',
    acknowledged: true,
    now: Date.parse('2026-11-28T00:00:00Z'),
  };
  expect(callbackCompensationCanExecute(valid)).toBe(true);
  for (const patch of [
    { capabilities: { ...capabilities, executionEnabled: false } },
    { capabilities: { ...capabilities, blockedReasons: ['幂等未验证'] } },
    { itemIds: ['delivered'] },
    { itemIds: ['review'] },
    { itemIds: ['ineligible'] },
    { itemIds: ['forged'] },
    { itemIds: [] },
    { itemIds: ['eligible', 'eligible'] },
    { reason: '过短' },
    { reason: 'a'.repeat(501) },
    { acknowledged: false },
    { now: Date.parse('2100-01-01T00:00:00Z') },
    { preview: { ...preview, workflowId: 'other' } },
    { preview: { ...preview, environment: 'PROD' } },
  ])
    expect(callbackCompensationCanExecute({ ...valid, ...patch })).toBe(false);
});
it('partitions the batch-only storage key by environment/scope/user/workflow without separator collisions', () => {
  const scope = { environment: 'PRE', scopeKey: 'space:a', userId: 'alice', workflowId: 'wf/a' };
  const key = callbackCompensationStorageKey(scope);
  for (const patch of [{ environment: 'PROD' }, { scopeKey: 'space:b' }, { userId: 'bob' }, { workflowId: 'other' }])
    expect(callbackCompensationStorageKey({ ...scope, ...patch })).not.toBe(key);
});

it('supports mutually exclusive flowIds-only selection, without sending stale time inputs', () => {
  expect(callbackCompensationRange('cfg', '', '', 'r1,r2\nr1', 'ids')).toEqual({
    configId: 'cfg',
    flowIds: ['r1', 'r2'],
  });
  expect(() => callbackCompensationRange('cfg', '', '', '', 'ids')).toThrow(/运行 ID/);
  expect(
    callbackCompensationRange('cfg', '2026-11-27T00:00', '2026-11-28T00:00', 'stale-id', 'time'),
  ).not.toHaveProperty('flowIds');
  expect(() => callbackCompensationRange('cfg', '2026-09-01T00:00', '2026-11-28T00:00', '', 'time')).toThrow(/31/);
});

it('matches the service reason length contract for multi-unit Unicode, not just ASCII', () => {
  expect(
    callbackCompensationCanExecute({
      capabilities,
      preview,
      itemIds: ['eligible'],
      reason: '😀'.repeat(251),
      acknowledged: true,
      now: Date.now(),
    }),
  ).toBe(false);
});

it('permits a new compensation only when a verified terminal batch has no work or audit still pending', () => {
  const resolved = {
    ...batch,
    status: 'completed' as const,
    items: [{ ...batch.items[0], state: 'received' as const }],
    counts: { received: 1 },
  };
  expect(callbackCompensationBatchResolved(resolved)).toBe(true);
  expect(callbackCompensationBatchResolved({ ...resolved, status: 'stopped' })).toBe(true);
  expect(callbackCompensationBatchResolved(null)).toBe(false);
  for (const status of ['queued', 'running'] as const)
    expect(callbackCompensationBatchResolved({ ...resolved, status })).toBe(false);
  for (const state of ['queued', 'sending'] as const)
    expect(
      callbackCompensationBatchResolved({ ...resolved, items: [{ ...resolved.items[0], state }] }),
    ).toBe(false);
  expect(
    callbackCompensationBatchResolved({ ...resolved, items: [{ ...resolved.items[0], auditPending: true }] }),
  ).toBe(false);
});

it('defaults to the previous 24 hours at minute precision with local absolute date inputs', () => {
  const now = Date.parse('2026-11-28T08:10:59.456Z');
  const range = callbackCompensationDefaultRange(now);
  expect(new Date(range.to).getTime()).toBe(Date.parse('2026-11-28T08:10:00Z'));
  expect(new Date(range.to).getTime() - new Date(range.from).getTime()).toBe(86_400_000);
  expect(callbackCompensationRange('cfg', range.from, range.to, '')).toEqual({
    configId: 'cfg', from: '2026-11-27T08:10:00.000Z', to: '2026-11-28T08:10:00.000Z',
  });
});
