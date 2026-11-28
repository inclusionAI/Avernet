import assert from 'node:assert/strict';
import { test } from 'node:test';
import { setCachedConfig } from '../src/config/config-cache.js';
import { defaults } from '../src/config/types.js';
import type { FlowState } from '../src/types.js';
import type { FlowRunRow, IFlowRunRepository } from '../src/db/repositories/types.js';
setCachedConfig({ database: { type: 'sqlite', sqlitePath: ':memory:' }, app: structuredClone(defaults) });
const { reapStaleRunningFlows, setFlowRunRepository, setNodeExecutionRepository } = await import('../src/controller.js');
const old = () => Math.floor(Date.now() / 1000) - 121 * 60;
const state = (minutes: unknown): FlowState => ({ workflowId: 'long', effectiveFlowTimeoutMinutes: minutes, retryEpoch: 7, currentPhase: 'P2', nodeStates: {}, params: {}, workflowData: {}, actionOutputs: {}, flowHooks: {}, auditLog: [], activeNodes: [] } as FlowState);
function fixture(s: FlowState) {
  let row = { flow_id: 'flow', workflow_id: 'long', started_at: old(), current_phase: 'P2', status: 'running', retry_epoch: 7 } as FlowRunRow;
  const calls: string[] = [];
  const claims: unknown[] = [];
  const repo = {
    findStaleRunning: async () => { calls.push('list'); return [{ ...row, state_json: undefined }]; },
    findByFlowId: async () => { calls.push('detail'); return { ...row, state_json: JSON.stringify(s) }; },
    markFailedIfRunning: async (_id: string, fields: { retryEpoch?: number }) => {
      calls.push('claim'); claims.push(fields);
      if (row.status !== 'running' || row.retry_epoch !== fields.retryEpoch) return false;
      row = { ...row, status: 'failed' }; return true;
    },
    updateCompletion: async () => { calls.push('completion'); return true; },
  } as unknown as IFlowRunRepository;
  setFlowRunRepository(repo);
  setNodeExecutionRepository({ reconcileStaleRunning: async (...args: unknown[]) => { calls.push(`reconcile:${args[3]}`); return 1; } } as never);
  return { repo, calls, claims, setRow: (next: Partial<FlowRunRow>) => { row = { ...row, ...next }; } };
}
test('list candidates are hydrated: persisted 8200/zero survive a 120 minute global watchdog', async () => {
  try {
    for (const minutes of [8200, 0]) {
      const f = fixture(state(minutes));
      assert.equal(await reapStaleRunningFlows(120 * 60_000), 0);
      assert.deepEqual(f.calls, ['list', 'detail']);
    }
  } finally { setFlowRunRepository(null); setNodeExecutionRepository(null); }
});
test('corrupt policy defers just that run instead of falling back to 120', async () => {
  try { const f = fixture(state('bad')); assert.equal(await reapStaleRunningFlows(120 * 60_000), 0); assert.equal(f.claims.length, 0); }
  finally { setFlowRunRepository(null); setNodeExecutionRepository(null); }
});
test('a winner claims before TaskFlow/reconcile; no second completion and epoch from scan', async () => {
  try {
    const f = fixture(state(120));
    assert.equal(await reapStaleRunningFlows(120 * 60_000, 100, async () => state(120), async () => { f.calls.push('taskflow-fail'); return true; }), 1);
    assert.deepEqual(f.calls.slice(0, 4), ['list', 'detail', 'claim', 'taskflow-fail']);
    assert.ok(f.calls.includes('reconcile:7')); assert.ok(!f.calls.includes('completion'));
    assert.equal((f.claims[0] as { retryEpoch: number }).retryEpoch, 7);
  } finally { setFlowRunRepository(null); setNodeExecutionRepository(null); }
});
test('terminal or new attempt winning after the detail snapshot has no losing side effects', async () => {
  try {
    for (const change of [{ status: 'succeeded' }, { retry_epoch: 8 }]) {
      const f = fixture(state(120));
      const claim = f.repo.markFailedIfRunning.bind(f.repo);
      f.repo.markFailedIfRunning = async (...args) => { f.setRow(change); return claim(...args); };
      assert.equal(await reapStaleRunningFlows(120 * 60_000, 100, async () => state(120), async () => { throw new Error('loser must not fail TaskFlow'); }), 0);
      assert.deepEqual(f.calls, ['list', 'detail', 'claim']);
    }
  } finally { setFlowRunRepository(null); setNodeExecutionRepository(null); }
});
test('sweeps cannot overlap even while a candidate lookup is pending, and unlock after failure', async () => {
  let release!: () => void;
  try {
    const f = fixture(state(120));
    const gate = new Promise<void>(resolve => { release = resolve; });
    f.repo.findStaleRunning = async () => { f.calls.push('list'); await gate; throw new Error('temporary list error'); };
    const first = reapStaleRunningFlows(120 * 60_000);
    await Promise.resolve();
    assert.equal(await reapStaleRunningFlows(120 * 60_000), 0);
    assert.deepEqual(f.calls, ['list']); release(); assert.equal(await first, 0);
    f.repo.findStaleRunning = async () => [];
    assert.equal(await reapStaleRunningFlows(120 * 60_000), 0);
  } finally { release?.(); setFlowRunRepository(null); setNodeExecutionRepository(null); }
});
