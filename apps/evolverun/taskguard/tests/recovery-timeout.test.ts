import assert from 'node:assert/strict';
import { test } from 'node:test';
import { setCachedConfig } from '../src/config/config-cache.js';
import { defaults } from '../src/config/types.js';
import type { FlowState, WorkflowSpec } from '../src/types.js';
import type { ControllerDeps } from '../src/controller.js';
setCachedConfig({ database: { type: 'sqlite', sqlitePath: ':memory:' }, app: structuredClone(defaults) });
const { recoverOrphanedFlows, reapStaleRunningFlows, setFlowRunRepository, setNodeExecutionRepository } = await import('../src/controller.js');
const { createActionRegistry } = await import('../src/actions/registry.js');
const { loadRunningSnapshot } = await import('../src/recovery/scan.js');

for (const minutes of [8200, 0, 120, 'corrupt']) {
  test(`restart recovery honors frozen ${minutes} over current global 120`, async t => {
    const config = structuredClone(defaults); config.execution.flowTimeoutMinutes = 120; config.execution.asyncRun = false;
    setCachedConfig({ database: { type: 'sqlite', sqlitePath: ':memory:' }, app: config });
    const workflow: WorkflowSpec = { id: 'recovery', title: 'Recovery', version: 1, nodes: [] };
    const state = { workflowId: 'recovery', workflowVersion: 1, params: {}, executionMode: 'private', businessStatus: 'INIT', currentPhase: 'P2', activeNodes: [], nodeStates: {}, workflowData: {}, actionOutputs: {}, flowHooks: {}, auditLog: [], workflowSnapshot: workflow, effectiveFlowTimeoutMinutes: minutes, retryEpoch: 7 } as FlowState;
    let row = { flow_id: 'flow', workflow_id: 'recovery', status: 'running', current_phase: 'P2', retry_epoch: 7, state_json: JSON.stringify(state), started_at: Math.floor(Date.now() / 1000) - 121 * 60 };
    const calls: string[] = [];
    setFlowRunRepository({
      findRunningByOrigin: async () => { calls.push('list'); return [{ flow_id: 'flow' }]; },
      findByFlowId: async () => { calls.push('detail'); return { ...row }; },
      markFailedIfRunning: async (_id: string, fields: { retryEpoch: number }) => { assert.equal(fields.retryEpoch, 7); calls.push('claim'); if (row.status !== 'running') return false; row.status = 'failed'; return true; },
      updateCompletion: async (_id: string, fields: { retryEpoch: number }) => { assert.equal(fields.retryEpoch, 7); calls.push('completion'); row.status = 'succeeded'; return true; },
      updateCurrentPhase: async () => true, updateStatus: async () => true,
    } as never);
    setNodeExecutionRepository({ reconcileStaleRunning: async (...args: unknown[]) => { calls.push(`reconcile:${args[3]}`); return 0; } } as never);
    t.after(() => { setFlowRunRepository(null); setNodeExecutionRepository(null); });
    const deps = { actionRegistry: createActionRegistry(), skillRoot: '/tmp', sessionKey: 'recovery-test', chatInject: async () => {}, executeNode: async () => { throw new Error('empty workflow'); }, boundTaskFlow: {
      get: async () => ({ flowId: 'flow', stateJson: JSON.stringify(state), status: 'running', revision: 3, retry_epoch: 7 }),
      fail: async (fields: { expectedRevision: number }) => { assert.equal(fields.expectedRevision, 3); calls.push('taskflow-fail'); return { applied: true }; },
      finish: async () => ({ applied: true }),
    } } as unknown as ControllerDeps;
    await recoverOrphanedFlows(deps, 'bot', 'engine');
    if (minutes === 'corrupt') assert.deepEqual(calls, ['list', 'detail']);
    else if (minutes === 120) assert.deepEqual(calls, ['list', 'detail', 'claim', 'taskflow-fail', 'reconcile:7']);
    else { assert.ok(!calls.includes('claim')); assert.ok(calls.includes('completion')); assert.ok(calls.includes('reconcile:7')); }
  });
}

test('recovery and watchdog share a scan gate', async t => {
  let release!: () => void; const gate = new Promise<void>(resolve => { release = resolve; });
  let watchdogLists = 0;
  setFlowRunRepository({ findRunningByOrigin: async () => { await gate; return []; }, findStaleRunning: async () => { watchdogLists++; return []; } } as never);
  t.after(() => { release(); setFlowRunRepository(null); });
  const recovery = recoverOrphanedFlows({} as never, 'bot', 'engine');
  assert.equal(await reapStaleRunningFlows(120 * 60_000), 0); assert.equal(watchdogLists, 0);
  release(); await recovery;
  assert.equal(await reapStaleRunningFlows(120 * 60_000), 0); assert.equal(watchdogLists, 1);
});
test('historical embedded TaskFlow state is hydrated, not mistaken for plain workflow params', async () => {
  const row = { flow_id: 'flow', workflow_id: 'recovery', status: 'running', retry_epoch: 0,
    params_json: JSON.stringify({ _clawmind_state: JSON.stringify({ workflowId: 'recovery', effectiveFlowTimeoutMinutes: 8200 }) }) };
  const snapshot = await loadRunningSnapshot({ findByFlowId: async () => row } as never, 'flow');
  assert.equal(snapshot?.state?.effectiveFlowTimeoutMinutes, 8200);
});

// Do not execute a Mind journal-owned run through the legacy restart path either.
test('restart recovery recognizes Mind epoch2 but leaves online-clock execution with its owner', async t => {
  const state = { workflowId: 'mind', recoveryEpoch: 2, recoveryPolicy: 'checkpoint-v1',
    effectiveFlowTimeoutMinutes: 1, currentPhase: 'P2' };
  const calls: string[] = [];
  setFlowRunRepository({
    findRunningByOrigin: async () => [{ flow_id: 'mind' }],
    findByFlowId: async () => ({ flow_id: 'mind', workflow_id: 'mind', status: 'running', retry_epoch: 2,
      state_json: JSON.stringify(state), started_at: Math.floor(Date.now() / 1000) - 600 }),
    markFailedIfRunning: async () => { calls.push('claim'); return true; },
    updateCompletion: async () => { calls.push('completion'); return true; },
  } as never);
  t.after(() => setFlowRunRepository(null));
  await recoverOrphanedFlows({ boundTaskFlow: {
    get: async () => ({ flowId: 'mind', retry_epoch: 2, stateJson: JSON.stringify(state), status: 'running', revision: 1 }),
    fail: async () => { calls.push('fail'); }, resume: async () => { calls.push('resume'); },
  }, executeNode: async () => { calls.push('execute'); } } as never, 'bot', 'engine');
  assert.deepEqual(calls, []);
});
