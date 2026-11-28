import assert from 'node:assert/strict';
import { test } from 'node:test';
import { freezeFlowTimeoutMinutes, resolveFlowTimeoutPolicy } from '../src/recovery/flow-timeout.js';
import { buildChildFlowState } from '../src/executors/subworkflow.js';
import { setCachedConfig } from '../src/config/config-cache.js';
import { defaults } from '../src/config/types.js';

const row = (state: unknown) => ({ workflow_id: 'long', state_json: JSON.stringify(state) });
test('startup freezes 8200 instead of 120; zero disables; invalid values fail closed', () => {
  assert.equal(freezeFlowTimeoutMinutes({ flowTimeoutMinutes: 8200 }, 120), 8200);
  assert.equal(freezeFlowTimeoutMinutes({ flowTimeoutMinutes: 0 }, 120), 0);
  assert.equal(freezeFlowTimeoutMinutes({}, 120), 120);
  for (const value of [null, undefined, -1, '8200', Infinity, NaN]) {
    assert.throws(() => freezeFlowTimeoutMinutes({ flowTimeoutMinutes: value }, 120));
  }
});
test('persisted 8200/zero owns policy even after global or YAML changes', async () => {
  for (const minutes of [8200, 0]) {
    let resolved = 0;
    const policy = await resolveFlowTimeoutPolicy(row({ effectiveFlowTimeoutMinutes: minutes }), 120 * 60_000, () => { resolved++; return 1; });
    assert.deepEqual(policy, { timeoutMs: minutes * 60_000, source: 'run-state' });
    assert.equal(resolved, 0);
  }
});
test('corrupt effective field, JSON, and snapshots never fall back to shorter global', async () => {
  for (const field of [null, '8200', -1, {}, NaN]) {
    await assert.rejects(resolveFlowTimeoutPolicy(row({ effectiveFlowTimeoutMinutes: field, workflowSnapshot: { id: 'long', flowTimeoutMinutes: 1 } }), 120 * 60_000));
  }
  for (const state of [{ workflowSnapshot: null }, { workflowSnapshot: {} }, { workflowSnapshot: { id: 'other', flowTimeoutMinutes: 1 } }, { workflowSnapshot: { id: 'long', flowTimeoutMinutes: null, timeoutMinutes: 8200 } }, { workflowPin: { workflowId: 'long' } }]) {
    await assert.rejects(resolveFlowTimeoutPolicy(row(state), 120 * 60_000, () => 1));
  }
  await assert.rejects(resolveFlowTimeoutPolicy({ workflow_id: 'long', state_json: '{broken' }, 120 * 60_000));
});
test('historical identity-only snapshots allow canonical/legacy timeout, absence selects global not deployed override', async () => {
  for (const field of ['flowTimeoutMinutes', 'timeoutMinutes']) {
    assert.deepEqual(await resolveFlowTimeoutPolicy(row({ workflowSnapshot: { id: 'long', [field]: 8200 } }), 120 * 60_000), { timeoutMs: 8200 * 60_000, source: 'workflow-snapshot' });
  }
  assert.deepEqual(await resolveFlowTimeoutPolicy(row({ workflowSnapshot: { id: 'long' } }), 120 * 60_000, () => 1), { timeoutMs: 120 * 60_000, source: 'global' });
  assert.deepEqual(await resolveFlowTimeoutPolicy(row({}), 120 * 60_000, () => 8200), { timeoutMs: 8200 * 60_000, source: 'per-workflow' });
  await assert.rejects(resolveFlowTimeoutPolicy(row({}), 120 * 60_000, async () => { throw new Error('unavailable definition'); }));
});
test('child startup freezes its own budget, not the parent budget, including zero', () => {
  setCachedConfig({ database: { type: 'sqlite', sqlitePath: ':memory:' }, app: structuredClone(defaults) });
  const parent = { executionMode: 'private', effectiveFlowTimeoutMinutes: 1 } as never;
  for (const minutes of [8200, 0]) {
    const child = buildChildFlowState({ id: 'child', version: 1, title: 'Child', nodes: [], flowTimeoutMinutes: minutes }, {}, parent, 'node', 'parent', 1);
    assert.equal(child.effectiveFlowTimeoutMinutes, minutes);
    assert.equal(child.retryEpoch, 0);
  }
});

test('real new-run creation persists frozen budget in TaskFlow state before execution', async t => {
  const { handleRun, setFlowRunRepository } = await import('../src/controller.js');
  const { createActionRegistry } = await import('../src/actions/registry.js');
  const { mkdtemp, rm } = await import('node:fs/promises');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const root = await mkdtemp(join(tmpdir(), 'taskguard-startup-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  setFlowRunRepository(null);
  const captured: any[] = [];
  for (const minutes of [8200, 0]) {
    const config = structuredClone(defaults); config.execution.flowTimeoutMinutes = 120;
    setCachedConfig({ database: { type: 'sqlite', sqlitePath: ':memory:' }, app: config });
    const workflow = { id: `new-${minutes}`, title: 'New run', version: 1, identity: { key: 'startup-test', label: 'Startup' }, nodes: [] as any[], flowTimeoutMinutes: minutes };
    const deps = {
      actionRegistry: createActionRegistry(), skillRoot: root, workflowLogDir: root, sessionKey: 'new-test', workflowRegistry: { [workflow.id]: workflow },
      chatInject: async () => {}, executeNode: async () => { throw new Error('empty workflow'); },
      boundTaskFlow: {
        list: async () => [], findLatest: async () => null,
        createManaged: async (params: any) => { captured.push(JSON.parse(params.stateJson)); return { flowId: workflow.id, revision: 1, status: 'running' }; },
        finish: async () => ({ applied: true }), get: async () => null,
      },
    } as any;
    await handleRun(deps, { workflowId: workflow.id, params: {}, executionMode: 'private', debug: true });
    assert.equal(captured.at(-1).effectiveFlowTimeoutMinutes, minutes);
    assert.equal(captured.at(-1).retryEpoch, 0);
  }
});


test('real flow-control queue does not use terminal completion and can resume the same epoch', async t => {
  const { handleRun, setFlowRunRepository } = await import('../src/controller.js');
  const { createActionRegistry } = await import('../src/actions/registry.js');
  const { DatabaseTaskFlowAdapter } = await import('../src/platform/database-taskflow.js');
  const { mkdtemp, rm } = await import('node:fs/promises');
  const { tmpdir } = await import('node:os'); const { join } = await import('node:path');
  const root = await mkdtemp(join(tmpdir(), 'taskguard-queue-'));
  t.after(() => { setFlowRunRepository(null); return rm(root, { recursive: true, force: true }); });
  const workflow = { id: 'queued-test', title: 'Queued', version: 1, identity: { key: 'queue', label: 'Queue' }, nodes: [], flowTimeoutMinutes: 8200 };
  const config = structuredClone(defaults);
  setCachedConfig({ database: { type: 'sqlite', sqlitePath: ':memory:' }, app: config });
  const adapter = new DatabaseTaskFlowAdapter('queue-test'); const mirrors: string[] = [];
  setFlowRunRepository({ insert: async () => true, updateStatus: async (_id: string, status: string, epoch: number) => { assert.equal(epoch, 0); mirrors.push(status); return true; },
    updateCurrentPhase: async (_id: string, _phase: string, epoch: number) => { assert.equal(epoch, 0); return true; },
    updateCompletion: async () => { throw new Error('queue must not settle the run'); },
  } as never);
  const flowId = await handleRun({ actionRegistry: createActionRegistry(), skillRoot: root, workflowLogDir: root, sessionKey: 'queue-test', workflowRegistry: { [workflow.id]: workflow },
    chatInject: async () => {}, boundTaskFlow: adapter, flowControl: { tryAcquire: async () => ({ acquired: false }) },
    executeNode: async () => { throw new Error('queue cannot execute'); },
  } as any, { workflowId: workflow.id, params: {}, executionMode: 'private', debug: true });
  const queued = await adapter.get(flowId);
  assert.equal(queued?.status, 'waiting'); assert.deepEqual(mirrors, ['waiting']);
  assert.equal((await adapter.resume({ flowId, expectedRevision: queued?.revision, status: 'running' })).applied, true);
});
