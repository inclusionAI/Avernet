import assert from 'node:assert/strict';
import { test } from 'node:test';
import { setCachedConfig } from '../src/config/config-cache.js';
import { defaults } from '../src/config/types.js';
import { createActionRegistry } from '../src/actions/registry.js';
import { CompletionPersistenceError } from '../src/recovery/completion.js';
import type { FlowState, WorkflowNode, WorkflowSpec } from '../src/types.js';
setCachedConfig({ database: { type: 'sqlite', sqlitePath: ':memory:' }, app: structuredClone(defaults) });
const controller = await import('../src/controller.js');

function node(id: string, type = 'done'): WorkflowNode {
  return { id, title: id, phase: 'P2', dependsOn: [], executor: { type } } as WorkflowNode;
}
function state(): FlowState {
  return { workflowId: 'post-closeout', workflowVersion: 1, params: {}, executionMode: 'private',
    businessStatus: 'INIT', currentPhase: 'P2', activeNodes: [], nodeStates: {}, workflowData: {},
    actionOutputs: {}, flowHooks: {}, auditLog: [], retryEpoch: 7 };
}
const cases = ['empty-success', 'node-success', 'single-failure', 'parallel-failure',
  'already-failed', 'all-done-failure', 'loop-finalize-failure', 'crash'] as const;
for (const scenario of cases) {
  test(`real controller ${scenario}: replacement in closeout return microtask retains its slot and bindings`, async t => {
    const flowId = `post-closeout-${scenario}`;
    const snapshot = state();
    const workflow: WorkflowSpec = { id: snapshot.workflowId, title: 'Post-closeout', version: 1,
      nodes: [], messages: { onFinished: 'Old driver must not publish this after replacement' } };
    if (scenario === 'node-success' || scenario === 'single-failure') workflow.nodes = [node('task')];
    if (scenario === 'parallel-failure') workflow.nodes = [node('a', 'subagent'), node('b', 'subagent')];
    if (scenario === 'already-failed' || scenario === 'all-done-failure') {
      workflow.nodes = [node('prior')];
      snapshot.nodeStates.prior = { status: 'failed', error: 'prior failure', completedAt: 1 };
      if (scenario === 'all-done-failure') workflow.nodes.push({ ...node('finalizer'), dependsOn: ['prior'], triggerRule: 'all_done' });
    }
    if (scenario === 'loop-finalize-failure') {
      workflow.nodes = [{ ...node('loop'), executor: { type: 'loop-group', maxIterations: 1,
        iterationVar: 'iteration', body: [node('task')] } }];
      snapshot.nodeStates.loop = { status: 'running' };
      snapshot.nodeStates['loop-body'] = { status: 'failed', error: 'body failure' };
      snapshot.loopGroups = { loop: { loopId: 'loop', status: 'running', currentIteration: 1, maxIterations: 1,
        iterationVar: 'iteration', iterations: { '1': { iteration: 1, status: 'running', nodeIds: { task: 'loop-body' }, startedAt: 1 } } } };
    }
    const calls: string[] = [];
    let settleReplacement!: () => void;
    const replacementGate = new Promise<void>(resolve => { settleReplacement = resolve; });
    let replacement: ReturnType<typeof controller.__launchAsyncExecutionForTest> | undefined;
    controller.setFlowRunRepository({ findByFlowId: async () => ({ status: 'running', retry_epoch: 7 }),
      updateCurrentPhase: async () => true, incrementNodeCount: async () => true,
      updateCompletion: async (_id: string, fields: { retryEpoch: number }) => { calls.push(`completion:${fields.retryEpoch}`); return true; },
    } as never);
    controller.setNodeExecutionRepository({ insert: async () => ({ affectedRows: 1 }), updateCompletionByFlowNode: async () => true,
      reconcileStaleRunning: async (_id: string, _status: string, _error: unknown, epoch: number) => {
      calls.push(`reconcile:${epoch}`); return 0;
    } } as never);
    t.after(async () => {
      settleReplacement(); await replacement?.promise.catch(() => {});
      controller.setFlowRunRepository(null); controller.setNodeExecutionRepository(null);
    });
    const base = { actionRegistry: createActionRegistry(), skillRoot: '/tmp', sessionKey: 'post-closeout',
      executeNode: async (executed: WorkflowNode) => {
        calls.push(`execute:${executed.id}`);
        return scenario.endsWith('failure') && scenario !== 'all-done-failure'
          ? { status: 'failed', error: 'execution failed' } : { status: 'succeeded', result: { ok: true } };
      },
    };
    const old = controller.__launchAsyncExecutionForTest({ ...base,
      ...(scenario === 'crash' ? { createFanoutRuntime: () => { throw new Error('original crash'); } } : {}),
      chatInject: async (_message: string, id: string) => {
        calls.push(`old-chat:${id}`);
        if ((id.endsWith(':flow:succeeded') || id.endsWith(':flow:failed')) && !replacement) queueMicrotask(() => {
          calls.push('replace');
          assert.equal(controller.abortAsyncExecutionForFlow(flowId), true);
          replacement = controller.__launchAsyncExecutionForTest({ ...base,
            chatInject: async () => { calls.push('new-chat'); },
            createFanoutRuntime: () => { throw new Error('replacement paused at native fail'); },
            boundTaskFlow: { fail: async () => { await replacementGate; throw new Error('replacement test cleanup'); } },
            flowControl: { releaseAllForFlow: () => { calls.push('new-release'); } },
          } as never, workflow, { ...state(), retryEpoch: 8 }, flowId, 2);
        });
      },
      boundTaskFlow: { get: async () => null, resume: async () => ({ applied: true, flow: { revision: 2 } }),
        finish: async () => ({ applied: true }), fail: async () => { calls.push('old-native-fail'); return { applied: true }; } },
      flowControl: { releaseAllForFlow: () => { calls.push('old-release'); } },
    } as never, workflow, snapshot, flowId, 1);
    const outcome = await old.promise.then(() => undefined, error => error);
    const expectedExecuted = scenario === 'parallel-failure' ? ['execute:a', 'execute:b']
      : scenario === 'all-done-failure' ? ['execute:finalizer']
        : scenario === 'node-success' || scenario === 'single-failure' ? ['execute:task'] : [];
    assert.deepEqual(calls.filter(call => call.startsWith('execute:')), expectedExecuted, 'exercise the intended real controller branch, not crash fallback');
    assert.ok(replacement, 'replacement must run in the real completion-to-caller microtask window');
    assert.equal(calls.includes('old-release'), false, 'old continuation must not free the replacement slot');
    assert.deepEqual(calls.slice(calls.indexOf('replace') + 1), [], 'no stale business message, crash rewrite or release after replacement');
    assert.deepEqual(calls.filter(call => call.startsWith('completion:')), ['completion:7']);
    assert.deepEqual(calls.filter(call => call.startsWith('reconcile:')), ['reconcile:7']);
    assert.ok(outcome instanceof CompletionPersistenceError, `old handle must remain rejected, got ${String(outcome)}`);
    assert.match(outcome.message, /superseded/);
    assert.equal(replacement.abortController.signal.aborted, false);
    assert.deepEqual(controller.__getAsyncExecutionResourceStateForTest(flowId),
      { active: true, workflowSpec: true, chatInject: true, verbosity: true });
  });
}
