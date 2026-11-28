/** Isolated strict-unhandled-rejection probe; no driver promise catch until after two turns. */
import { setCachedConfig } from '../../src/config/config-cache.js';
import { defaults } from '../../src/config/types.js';
setCachedConfig({ database: { type: 'sqlite', sqlitePath: ':memory:' }, app: structuredClone(defaults) });
const controller = await import('../../src/controller.js');
const { createActionRegistry } = await import('../../src/actions/registry.js');
const { HttpCallbackDispatcher } = await import('../../src/alerts/http-callback-dispatcher.js');
const mode = process.argv[2];
const { observeDriverPromise } = await import('../../src/recovery/async-driver.js');
const calls: string[] = [];
const state = { workflowId: 'driver', workflowVersion: 1, params: {}, executionMode: 'private', businessStatus: 'INIT',
  currentPhase: 'P2', activeNodes: [], nodeStates: {}, workflowData: {}, actionOutputs: {}, flowHooks: {}, auditLog: [], retryEpoch: 7 };
controller.setFlowRunRepository({ findByFlowId: async () => ({ status: 'running', retry_epoch: 7 }),
  updateCurrentPhase: async () => true,
  updateCompletion: async (_id: string, fields: { retryEpoch: number }) => {
    calls.push(`completion:${fields.retryEpoch}`);
    if (mode === 'release-throws' || mode === 'ordinary-release-throws') return true;
    if (mode === 'completion-false') return false;
    throw Object.assign(new Error('completion rejected'), { status: 409 });
  },
} as never);
controller.setNodeExecutionRepository({ reconcileStaleRunning: async () => { calls.push('reconcile'); return 0; } } as never);
HttpCallbackDispatcher.prototype.dispatchWorkflowEvent = async () => { calls.push('callback'); };
controller.setHttpCallbackRepositories({} as never);
const deps = { actionRegistry: createActionRegistry(), skillRoot: '/tmp', sessionKey: 'driver-probe',
  chatInject: async () => { calls.push('chat'); }, executeNode: async () => { throw new Error('no node'); },
  ...(mode.startsWith('ordinary-') ? {} : { createFanoutRuntime: () => { throw new Error(mode === 'redrive-sync-throws' ? '状态更新冲突，请重试' : 'driver crashed'); } }),
  boundTaskFlow: { get: async () => ({ status: 'running', retry_epoch: 7, revision: 2, stateJson: JSON.stringify(state) }), finish: async () => ({ applied: true }), fail: async () => {
    calls.push('native-fail'); if (mode === 'handler-throws') throw new Error('native failure handler threw');
    return { applied: true };
  } }, flowControl: { releaseAllForFlow: () => { calls.push('release'); if (mode.endsWith('release-throws')) throw new Error('release handler threw'); } },
};
if (mode === 'redrive-sync-throws') {
  let reads = 0;
  Object.defineProperty(deps, 'abortSignal', { enumerable: true, get() {
    if (++reads > 2) throw new Error('redrive launch threw');
    return undefined;
  } });
}
let reported = 0;
const observed = Promise.reject(new Error('observer original failure')).finally(() => { throw new Error('observer finally failure'); });
observeDriverPromise(observed, () => { reported++; throw new Error('observer reporter threw'); });
const asyncReported = Promise.reject(new Error('async reporter original failure'));
observeDriverPromise(asyncReported, async () => { reported++; throw new Error('observer async reporter rejected'); });
const handle = controller.__launchAsyncExecutionForTest(deps as never, { id: 'driver', title: 'Driver', version: 1, nodes: [] }, state as never, 'driver', 1);
await new Promise(resolve => setImmediate(resolve));
await new Promise(resolve => setImmediate(resolve));
const result = await handle.promise.then(() => ({ rejected: false, error: '' }), error => ({ rejected: true, error: String(error) }));
console.log('DRIVER_RESULT ' + JSON.stringify({ ...result, calls, reported, resources: controller.__getAsyncExecutionResourceStateForTest('driver') }));
