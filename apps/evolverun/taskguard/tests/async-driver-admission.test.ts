import assert from 'node:assert/strict';
import { test } from 'node:test';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setCachedConfig } from '../src/config/config-cache.js';
import { defaults } from '../src/config/types.js';
setCachedConfig({ database: { type: 'sqlite', sqlitePath: ':memory:' }, app: structuredClone(defaults) });
const controller = await import('../src/controller.js');
const { createActionRegistry } = await import('../src/actions/registry.js');
const workflow = { id: 'driver', title: 'Driver', version: 1, nodes: [] };
function state(epoch = 7) { return { workflowId: 'driver', workflowVersion: 1, params: {}, executionMode: 'private', businessStatus: 'INIT',
  currentPhase: 'P2', activeNodes: [], nodeStates: {}, workflowData: {}, actionOutputs: {}, flowHooks: {}, auditLog: [], retryEpoch: epoch }; }
function gate() { let open!: (value?: any) => void; const promise = new Promise<any>(resolve => { open = resolve; }); return { promise, open }; }
for (const mode of ['completion-false', 'completion-409', 'ordinary-completion', 'handler-throws', 'release-throws', 'ordinary-release-throws', 'redrive-sync-throws']) {
  test(`detached real driver ${mode} is observed under strict unhandled rejection and keeps rejected outcome`, async t => {
    const home = await mkdtemp(join(tmpdir(), 'taskguard-driver-')); t.after(() => rm(home, { recursive: true, force: true }));
    const { stdout } = await promisify(execFile)(process.execPath, ['--unhandled-rejections=strict', '--import', 'tsx',
      fileURLToPath(new URL('./fixtures/async-driver-failure.ts', import.meta.url)), mode], {
      cwd: process.cwd(), env: { ...process.env, HOME: home }, timeout: 10000,
    });
    const line = stdout.split('\n').find(line => line.startsWith('DRIVER_RESULT ')); assert.ok(line, stdout);
    const result = JSON.parse(line.slice('DRIVER_RESULT '.length));
    assert.equal(result.reported, 2, 'synchronous/async reporter failures must also be supervised');
    assert.equal(result.rejected, true, 'observer must not convert failed driver into resolved success');
    assert.deepEqual(result.resources, { active: false, workflowSpec: false, chatInject: false, verbosity: false });
    if (mode === 'redrive-sync-throws') { assert.deepEqual(result.calls, []); assert.match(result.error, /redrive launch threw/); }
    else if (mode === 'handler-throws') assert.deepEqual(result.calls, ['native-fail']);
    else if (!mode.endsWith('release-throws')) {
      assert.deepEqual(result.calls, mode === 'ordinary-completion' ? ['completion:7'] : ['native-fail', 'completion:7']);
    } else {
      assert.ok(result.calls.indexOf('completion:7') < result.calls.indexOf('reconcile'));
      assert.ok(result.calls.indexOf('reconcile') < result.calls.indexOf('release'));
      assert.match(result.error, /release handler threw/);
      assert.equal(result.calls.filter((call: string) => call.startsWith('completion:')).length, 1);
      if (mode === 'ordinary-release-throws') assert.equal(result.calls.includes('native-fail'), false);
    }
  });
}
for (const boundary of ['native-fail', 'completion', 'completion-accepted']) {
  test(`old driver settling at ${boundary} cannot clean, release or publish replacement generation`, async t => {
    const reached = gate(), settleOld = gate(), settleNew = gate(); const calls: string[] = [];
    controller.setFlowRunRepository({ updateCompletion: async (_id: string, fields: { retryEpoch: number }) => {
      calls.push(`completion:${fields.retryEpoch}`); reached.open(); await settleOld.promise;
      if (boundary === 'completion-accepted') return true;
      throw Object.assign(new Error('completion409'), { status: 409 });
    } } as never);
    controller.setNodeExecutionRepository({ reconcileStaleRunning: async () => { calls.push('reconcile'); return 0; } } as never);
    t.after(() => { settleOld.open(); settleNew.open(); controller.setFlowRunRepository(null); controller.setNodeExecutionRepository(null); });
    const deps = { actionRegistry: createActionRegistry(), sessionKey: 'replacement', skillRoot: '/tmp',
      chatInject: async () => { calls.push('old-chat'); }, executeNode: async () => { throw new Error('unused'); },
      createFanoutRuntime: () => { throw new Error('driver crash'); },
      flowControl: { releaseAllForFlow: () => { calls.push('old-release'); } },
      boundTaskFlow: { fail: async () => {
        calls.push('native-fail:7'); if (boundary === 'native-fail') { reached.open(); await settleOld.promise; }
        return { applied: true };
      } },
    };
    const old = controller.__launchAsyncExecutionForTest(deps as never, workflow, state() as never, 'driver', 1);
    const oldFailure = assert.rejects(old.promise); await reached.promise;
    assert.equal(controller.abortAsyncExecutionForFlow('driver'), true);
    const replacement = controller.__launchAsyncExecutionForTest({ ...deps,
      chatInject: async () => { calls.push('new-chat'); },
      flowControl: { releaseAllForFlow: () => { calls.push('new-release'); } },
      boundTaskFlow: { fail: async () => { await settleNew.promise; throw new Error('new handler failure'); } },
    } as never, workflow, state(8) as never, 'driver', 2);
    const newFailure = assert.rejects(replacement.promise);
    settleOld.open(); await oldFailure;
    assert.deepEqual(controller.__getAsyncExecutionResourceStateForTest('driver'),
      { active: true, workflowSpec: true, chatInject: true, verbosity: true });
    assert.equal(replacement.abortController.signal.aborted, false);
    assert.deepEqual(calls, boundary === 'native-fail' ? ['native-fail:7'] : ['native-fail:7', 'completion:7']);
    settleNew.open(); await newFailure;
    assert.deepEqual(controller.__getAsyncExecutionResourceStateForTest('driver'),
      { active: false, workflowSpec: false, chatInject: false, verbosity: false });
  });
}
test('state-conflict re-drive cannot adopt a newer TaskFlow execution epoch', async t => {
  let entries = 0, releases = 0;
  controller.setFlowRunRepository(null); controller.setNodeExecutionRepository(null);
  const handle = controller.__launchAsyncExecutionForTest({ actionRegistry: createActionRegistry(), sessionKey: 'epoch-conflict', skillRoot: '/tmp',
    chatInject: async () => {}, executeNode: async () => { throw new Error('unused'); },
    createFanoutRuntime: () => { entries++; throw new Error('状态更新冲突，请重试'); },
    flowControl: { releaseAllForFlow: () => { releases++; } },
    boundTaskFlow: { get: async () => ({ status: 'running', retry_epoch: 8, revision: 2, stateJson: JSON.stringify(state(8)) }) },
  } as never, workflow, state() as never, 'driver', 1);
  await assert.rejects(handle.promise); await new Promise(resolve => setImmediate(resolve));
  assert.equal(entries, 1); assert.equal(releases, 0);
});
test('same-epoch revision conflict keeps bounded redrive without an old-driver release or second completion', async t => {
  let entries = 0, writes = 0, releases = 0, nativeFailures = 0;
  controller.setFlowRunRepository({ findByFlowId: async () => ({ status: 'running', retry_epoch: 7 }),
    updateCurrentPhase: async () => true,
    updateCompletion: async (_id: string, fields: { retryEpoch: number }) => { assert.equal(fields.retryEpoch, 7); writes++; return true; },
  } as never);
  t.after(() => controller.setFlowRunRepository(null));
  const handle = controller.__launchAsyncExecutionForTest({ actionRegistry: createActionRegistry(), sessionKey: 'same-epoch-redrive', skillRoot: '/tmp',
    chatInject: async () => {}, executeNode: async () => { throw new Error('unused'); },
    createFanoutRuntime: () => { if (++entries === 1) throw new Error('状态更新冲突，请重试'); return undefined; },
    flowControl: { releaseAllForFlow: () => { releases++; } },
    boundTaskFlow: { get: async () => ({ status: 'running', retry_epoch: 7, revision: 2, stateJson: JSON.stringify(state()) }),
      finish: async () => ({ applied: true }), fail: async () => { nativeFailures++; return { applied: true }; } },
  } as never, workflow, state() as never, 'driver', 1);
  await assert.rejects(handle.promise); await new Promise(resolve => setImmediate(resolve));
  assert.equal(entries, 2); assert.equal(writes, 1); assert.equal(releases, 1); assert.equal(nativeFailures, 0);
  assert.deepEqual(controller.__getAsyncExecutionResourceStateForTest('driver'),
    { active: false, workflowSpec: false, chatInject: false, verbosity: false });
});

for (const superseded of [false, true]) {
  test(`nested sync child uses its own epoch and cannot admit parent closeout (parent superseded=${superseded})`, async t => {
    const { claimAsyncDriver } = await import('../src/recovery/async-driver.js');
    const parentId = `parent-context-${superseded}`, childId = `child-context-${superseded}`;
    const parent = claimAsyncDriver(parentId, 7);
    const replacement = superseded ? claimAsyncDriver(parentId, 8) : undefined;
    const calls: string[] = [];
    controller.setFlowRunRepository({ findByFlowId: async () => ({ status: 'running', retry_epoch: 0 }),
      updateCurrentPhase: async (_id: string, _phase: string, epoch: number) => { assert.equal(epoch, 0); return true; },
      updateCompletion: async (id: string, fields: { retryEpoch: number }) => {
        assert.equal(id, childId); assert.equal(fields.retryEpoch, 0); calls.push('child-completion:0'); return true;
      },
    } as never);
    controller.setNodeExecutionRepository({ reconcileStaleRunning: async (id: string, _status: string, _error: string | undefined, epoch: number) => {
      assert.equal(id, childId); assert.equal(epoch, 0); calls.push('child-reconcile:0'); return 0;
    } } as never);
    t.after(() => { parent.finish(() => {}); replacement?.finish(() => {});
      controller.setFlowRunRepository(null); controller.setNodeExecutionRepository(null); });
    const outcome = await parent.run(() => controller.executeWorkflowForTest({
      deps: { actionRegistry: createActionRegistry(), skillRoot: '/tmp', sessionKey: 'nested-child',
        chatInject: async () => {}, executeNode: async () => { throw new Error('no nodes'); },
        boundTaskFlow: { finish: async () => ({ applied: true }), get: async () => null },
      } as never, workflow, state: state(0) as never, flowId: childId, revision: 1,
    }));
    assert.equal(outcome.status, 'finished');
    assert.deepEqual(calls, ['child-completion:0', 'child-reconcile:0']);
    assert.equal(parent.closeoutAdmitted(), false, 'child admission is not parent admission');
    assert.equal(replacement?.closeoutAdmitted() ?? false, false);
  });
}
