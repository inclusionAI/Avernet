import assert from 'node:assert/strict';
import { test } from 'node:test';
import { setCachedConfig } from '../src/config/config-cache.js';
import { defaults } from '../src/config/types.js';
import { CompletionPersistenceError, persistBeforePublish } from '../src/recovery/completion.js';
import { admitManualRetry } from '../src/recovery/manual-retry.js';
import { captureStateAttempt } from '../src/recovery/attempt.js';
import type { FlowState, WorkflowSpec } from '../src/types.js';
import type { ControllerDeps } from '../src/controller.js';
setCachedConfig({ database: { type: 'sqlite', sqlitePath: ':memory:' }, app: structuredClone(defaults) });
const controller = await import('../src/controller.js');
const { createActionRegistry } = await import('../src/actions/registry.js');
const { HttpCallbackDispatcher } = await import('../src/alerts/http-callback-dispatcher.js');
const { DatabaseTaskFlowAdapter } = await import('../src/platform/database-taskflow.js');

for (const failure of [false, 404, 409]) {
  test(`rejected ${failure} persistence never publishes or retries`, async () => {
    let writes = 0; let published = 0;
    await assert.rejects(persistBeforePublish(async () => {
      writes++;
      if (failure === false) return false;
      throw Object.assign(new Error('permanent'), { status: failure });
    }, async () => { published++; }, { baseDelayMs: 0 }), CompletionPersistenceError);
    assert.equal(writes, 1); assert.equal(published, 0);
  });
}
test('transient persistence retry is bounded and publication follows acknowledgement', async () => {
  let writes = 0; const calls: string[] = [];
  await persistBeforePublish(async () => { writes++; calls.push('write'); if (writes === 1) throw Object.assign(new Error('temporary'), { status: 503 }); return true; }, async () => { calls.push('publish'); }, { baseDelayMs: 0 });
  assert.deepEqual(calls, ['write', 'write', 'publish']);
  writes = 0;
  await assert.rejects(persistBeforePublish(async () => { writes++; throw new Error('offline'); }, async () => { throw new Error('must not publish'); }, { baseDelayMs: 0 }));
  assert.equal(writes, 3);
});

const workflow: WorkflowSpec = { id: 'done', title: 'Done', version: 1, nodes: [] };
function initialState(): FlowState {
  return { workflowId: 'done', workflowVersion: 1, params: {}, executionMode: 'private', businessStatus: 'INIT', currentPhase: 'P2', activeNodes: [], nodeStates: {}, workflowData: {}, actionOutputs: {}, flowHooks: {}, auditLog: [], effectiveFlowTimeoutMinutes: 8200, retryEpoch: 7 };
}
for (const rejected of [false, 404, 409, 'accepted'] as const) {
  test(`real controller ${rejected}: completion gates reconcile/callback/chat and keeps captured epoch`, async t => {
    const calls: string[] = []; const completions: any[] = [];
    const original = HttpCallbackDispatcher.prototype.dispatchWorkflowEvent;
    HttpCallbackDispatcher.prototype.dispatchWorkflowEvent = async () => { calls.push('callback'); };
    t.after(() => { HttpCallbackDispatcher.prototype.dispatchWorkflowEvent = original; controller.setHttpCallbackRepositories(null); controller.setFlowRunRepository(null); controller.setNodeExecutionRepository(null); controller.setGlobalChatInject(null, 'flow'); });
    controller.setFlowRunRepository({
      // A later row is visible to polling/callbacks. The execution must never
      // adopt that row's epoch 99 when persisting/reconciling epoch 7.
      findByFlowId: async () => ({ flow_id: 'flow', workflow_id: 'done', status: 'running', retry_epoch: 99 }),
      updateCurrentPhase: async (_id: string, _phase: string, epoch: number) => { assert.equal(epoch, 7); return true; },
      updateStatus: async () => true,
      updateCompletion: async (_id: string, completion: unknown) => { calls.push('completion'); completions.push(completion); if (rejected === false) return false; if (rejected !== 'accepted') throw Object.assign(new Error('rejected'), { status: rejected }); return true; },
    } as never);
    controller.setNodeExecutionRepository({ reconcileStaleRunning: async (...args: unknown[]) => { calls.push(`reconcile:${args[3]}`); return 0; } } as never);
    controller.setHttpCallbackRepositories({} as never);
    controller.setGlobalChatInject(async () => { calls.push('chat'); }, 'flow');
    const deps = { actionRegistry: createActionRegistry(), skillRoot: '/tmp', sessionKey: 'test', chatInject: async () => { calls.push('chat'); }, executeNode: async () => { throw new Error('no nodes to execute'); }, boundTaskFlow: { finish: async () => ({ applied: true }), get: async () => null } } as unknown as ControllerDeps;
    const run = controller.executeWorkflowForTest({ deps, workflow, state: initialState(), flowId: 'flow', revision: 1 });
    if (rejected === 'accepted') {
      assert.equal((await run).status, 'finished');
      await Promise.resolve();
      assert.deepEqual(calls, ['completion', 'reconcile:7', 'callback', 'chat']);
    } else {
      await assert.rejects(run, CompletionPersistenceError);
      assert.deepEqual(calls, ['completion']);
    }
    assert.equal(completions[0].retryEpoch, 7); assert.equal(completions[0].currentPhase, 'P2');
  });
}

test('manual retry admits snapshot epoch + 1 before local state changes, never status reopening', async () => {
  const state = { retryEpoch: 7 }; const calls: string[] = [];
  const repo = { resumeForRetry: async (id: string, epoch: number, stateJson: string) => { assert.equal(id, 'flow'); assert.equal(epoch, 8); assert.equal(JSON.parse(stateJson).retryEpoch, 8); assert.ok(Number.isSafeInteger(JSON.parse(stateJson).retryStartedAtMs)); assert.equal(state.retryEpoch, 7); calls.push('retry-resume'); return 'resumed'; }, updateStatus: async () => { throw new Error('legacy reopen forbidden'); } } as never;
  assert.equal(await admitManualRetry(repo, 'flow', state), 8);
  assert.equal(state.retryEpoch, 8); assert.deepEqual(calls, ['retry-resume']);
  for (const outcome of ['conflict', 'not_found']) {
    const failedState = { retryEpoch: 7 };
    await assert.rejects(admitManualRetry({ resumeForRetry: async () => outcome } as never, 'flow', failedState));
    assert.equal(failedState.retryEpoch, 7);
  }
});
test('state attempt is captured from execution record; mismatches fail closed', () => {
  const state = { retryEpoch: 8 };
  assert.throws(() => captureStateAttempt({ retry_epoch: 8 }, {}), /mismatch/);
  assert.equal(captureStateAttempt({ retry_epoch: 8 }, state), 8);
  assert.deepEqual(state, { retryEpoch: 8 });
  assert.throws(() => captureStateAttempt({ retry_epoch: 9 }, { retryEpoch: 8 }), /mismatch/);
});
test('TaskFlow fail does not send an unguarded terminal status mirror', async () => {
  const calls: string[] = [];
  const api = { insert: async () => true, updateStatus: async () => { calls.push('status'); return true; } } as never;
  const adapter = new DatabaseTaskFlowAdapter('terminal-test', api);
  const flow = await adapter.createManaged({ stateJson: JSON.stringify(initialState()) });
  await adapter.fail({ flowId: flow.flowId, expectedRevision: 1, stateJson: JSON.stringify(initialState()) });
  assert.deepEqual(calls, []);
});

test('rejected API resume stays rejected and cannot mutate local state or revision', async () => {
  const adapter = new DatabaseTaskFlowAdapter('resume-reject', { insert: async () => true,
    updateStatus: async () => { throw Object.assign(new Error('HTTP 409 rejected'), { status: 409 }); } } as never);
  const flow = await adapter.createManaged({ stateJson: JSON.stringify(initialState()), status: 'waiting' });
  await assert.rejects(adapter.resume({ flowId: flow.flowId, expectedRevision: 1, status: 'running' }), (e: any) => e.status === 409);
  const after = await adapter.get(String(flow.flowId));
  assert.equal(after?.status, 'waiting'); assert.equal(after?.revision, 1);
});
test('in-memory terminal resume also requires epoch+1 and normalized cancellations never reopen', async () => {
  for (const status of ['succeeded', 'failed', ' Cancelled ', 'CANCELED']) {
    const adapter = new DatabaseTaskFlowAdapter(`terminal-resume-${status}`);
    const before = initialState(); before.retryEpoch = 0;
    const flow = await adapter.createManaged({ stateJson: JSON.stringify(before), status });
    await assert.rejects(adapter.resume({ flowId: flow.flowId, expectedRevision: 1, status: 'running' }));
    const next = { ...before, retryEpoch: 1 };
    const run = adapter.resume({ flowId: flow.flowId, expectedRevision: 1, status: 'running', stateJson: JSON.stringify(next) });
    if (status.trim().toLowerCase().startsWith('cancel')) await assert.rejects(run);
    else assert.equal((await run).applied, true);
  }
});
