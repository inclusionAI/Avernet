// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { FanoutStateRepository } from '../fanout-state-repository.js';
import { FlowRunRepository } from '../flow-run-repository.js';
import { childInput, fixture, group } from './fanout-state-fixture.js';

let f: ReturnType<typeof fixture>;
let repo: FanoutStateRepository;
beforeEach(() => { f = fixture(); repo = new FanoutStateRepository(f.db); });
afterEach(async () => { await f.db.close(); });
const save = (g = group(), expectedRevision: number | null = null, parentState?: Record<string, unknown>) =>
  repo.saveGroup('parent', g.groupNodeId, { group: g, expectedRevision, parentState });
const start = (input = childInput(), id = 'child-fanout') => repo.startOrGetChild('parent', id, input);
const stored = () => JSON.parse(f.raw.prepare("SELECT state_json FROM flow_runs WHERE flow_id='parent'").get().state_json);

it('distinguishes an absent group from a missing parent and a corrupt database state', async () => {
  expect(await repo.getGroup('parent', 'fanout')).toEqual({ group: null, parentStatus: 'running' });
  await expect(repo.getGroup('missing', 'fanout')).rejects.toMatchObject({ statusCode: 404 });
  f.raw.exec("UPDATE flow_runs SET state_json = 'not-json'");
  await expect(repo.getGroup('parent', 'fanout')).rejects.toThrow();
});

it('initializes the full empty parent mirror, then merges independent concurrent groups', async () => {
  await save(group(), null, { workflowId: 'parent-wf', nodeStates: { work: { status: 'running' } }, subworkflowFanouts: { fake: group('fake') } });
  expect(stored().nodeStates.work.status).toBe('running');
  expect(stored().subworkflowFanouts.fake).toBeUndefined();
  await Promise.all([save(group('two')), save(group('three'))]);
  expect(Object.keys(stored().subworkflowFanouts).sort()).toEqual(['fanout', 'three', 'two']);
  await save({ ...group(), revision: 1 }, 0, { workflowId: 'wrong', nodeStates: {} });
  expect(stored().workflowId).toBe('parent-wf');
});

it('allows one CAS winner and rejects stale revisions and execution identities', async () => {
  await save();
  const candidates = await Promise.allSettled([save({ ...group(), revision: 1 }, 0), save({ ...group(), revision: 1 }, 0)]);
  expect(candidates.filter(r => r.status === 'fulfilled')).toHaveLength(1);
  expect(candidates.find(r => r.status === 'rejected')).toMatchObject({ reason: { statusCode: 409 } });
  const foreign = { ...group(), revision: 2, groupExecutionId: 'other' };
  foreign.children = foreign.children.map((c: object) => ({ ...c, groupExecutionId: 'other' }));
  await expect(save(foreign, 1)).rejects.toMatchObject({ statusCode: 409 });
  await expect(save({ ...group(), revision: 3 }, 1)).rejects.toMatchObject({ statusCode: 400 });
});

it('retries competing parent mirrors without losing a durable group', async () => {
  await save();
  const mirror = new FlowRunRepository(f.db);
  await Promise.all([save({ ...group(), revision: 1 }, 0), mirror.updateStateJson('parent', JSON.stringify({ currentPhase: 'P2' }))]);
  expect(stored().subworkflowFanouts.fanout.revision).toBe(1);
  expect(stored().currentPhase).toBe('P2');
});

it('reports persistent CAS conflicts instead of pretending to have saved', async () => {
  f.raw.exec('CREATE TRIGGER race BEFORE UPDATE ON flow_runs BEGIN SELECT RAISE(IGNORE); END');
  await expect(save()).rejects.toMatchObject({ statusCode: 409 });
  expect(await repo.getGroup('parent', 'fanout')).toEqual({ group: null, parentStatus: 'running' });
});

it.each(['cancelled', 'succeeded', 'failed', 'timed_out', 'canceled'])('fences running/success writes after parent %s', async status => {
  await save();
  f.raw.prepare('UPDATE flow_runs SET status = ?').run(status);
  await expect(save({ ...group(), revision: 1 }, 0)).rejects.toMatchObject({ statusCode: 409 });
  await expect(save(seal(group(), 'succeeded'), 0)).rejects.toMatchObject({ statusCode: 409 });
  await save({ ...group(), revision: 1, status: 'cancelling', cancelRequestedAt: group().createdAt, cancelDeadlineAt: group().deadlineAt }, 0);
});

it('arbitrates a cancellation between parent read and CAS in SQL', async () => {
  await save();
  // A real SQLite trigger wins the race before the attempted write and aborts that write.
  f.raw.exec(`CREATE TRIGGER cancel_race BEFORE UPDATE OF state_json ON flow_runs
    WHEN OLD.status = 'running' BEGIN UPDATE flow_runs SET status = 'cancelled' WHERE flow_id = OLD.flow_id; SELECT RAISE(IGNORE); END`);
  await expect(save(seal(group(), 'succeeded'), 0)).rejects.toMatchObject({ statusCode: 409 });
  expect(stored().subworkflowFanouts.fanout.status).toBe('running');
});

function seal(g: Record<string, any>, status: string) {
  return { ...g, revision: 1, status, sealedAt: g.deadlineAt,
    result: { schemaVersion: 'fanout.v1', kind: 'subworkflowFanoutResult', parentFlowId: g.parentFlowId,
      groupNodeId: g.groupNodeId, groupExecutionId: g.groupExecutionId, groupRevision: 1,
      childWorkflowId: g.childWorkflow.id, childPublishedVersion: g.childPublishedVersion, status, complete: false, businessOutcome: 'incomplete',
      successPolicy: 'all_done', finishMode: 'wait_all', resultPolicy: 'collect_all',
      total: 1, successCount: 0, failedCount: 0, cancelledCount: 0, timedOutCount: 0, rejectedCount: 0, pendingCount: 1,
      counts: { total: 1, successCount: 0, failedCount: 0, cancelledCount: 0, timedOutCount: 0, rejectedCount: 0, pendingCount: 1 },
      items: g.children, error: null } };
}

it('never overwrites or resurrects a terminal group', async () => {
  await save();
  await save(seal(group(), 'cancelled'), 0);
  await expect(save({ ...group(), revision: 2 }, 1)).rejects.toMatchObject({ statusCode: 409 });
  await expect(save({ ...seal(group(), 'failed'), revision: 2, result: { ...seal(group(), 'failed').result, groupRevision: 2 } }, 1))
    .rejects.toMatchObject({ statusCode: 409 });
  expect(stored().subworkflowFanouts.fanout.status).toBe('cancelled');
});

it.each([
  { schemaVersion: 'fanout.v2' }, { parentFlowId: 'another' }, { groupNodeId: 'wrong' },
  { children: {} }, { children: [{}] }, { config: {} }, { childWorkflow: {} },
  { cleanupPendingChildIds: [15] }, { revision: -1 }, { revision: 1 }, { result: {} },
])('validates v1 snapshots before storage: %j', async invalid => {
  await expect(save({ ...group(), ...invalid })).rejects.toMatchObject({ statusCode: 400 });
});

it('rejects changes to frozen child specs and child launch identity', async () => {
  await save();
  await expect(save({ ...group(), revision: 1, childWorkflow: { ...group().childWorkflow, version: 2 } }, 0)).rejects.toMatchObject({ statusCode: 409 });
  const g = group(); g.children[0].childFlowId = 'different';
  await expect(save({ ...g, revision: 1 }, 0)).rejects.toMatchObject({ statusCode: 409 });
});

it('propagates database read and write failures', async () => {
  f.raw.exec(`CREATE TRIGGER unavailable BEFORE UPDATE ON flow_runs BEGIN SELECT RAISE(ABORT, 'durable write failed'); END`);
  await expect(save()).rejects.toThrow('durable write failed');
  f.raw.exec('DROP TABLE flow_runs');
  await expect(repo.getGroup('parent', 'fanout')).rejects.toThrow('no such table');
});

describe('strict managed children', () => {
  beforeEach(async () => { await save(); });

  it('creates once, returns a duplicate without replacing checkpoint state, and omits credentials', async () => {
    const initial = await start();
    expect(initial.created).toBe(true);
    expect(initial.run).toMatchObject({ flowId: 'child-fanout', status: 'running', workflowId: 'child-wf', revision: 1, sessionKey: 'session-fanout' });
    expect(JSON.stringify(initial)).not.toContain('private-value');
    const updated = await repo.checkpointChild('parent', 'child-fanout', { expectedRevision: 1,
      stateJson: { ...initial.run.stateJson, workflowData: { done: true } }, status: 'running', currentStep: 'work' });
    expect(updated.revision).toBe(2);
    expect((await start())).toEqual({ created: false, run: updated });
    expect(f.raw.prepare("SELECT current_phase FROM flow_runs WHERE flow_id='child-fanout'").get().current_phase).toBe('work');
    await expect(repo.getChild('other-parent', 'child-fanout')).rejects.toMatchObject({ statusCode: 409 });
  });

  it('serializes duplicate starts without swallowing insert failures', async () => {
    const results = await Promise.all([start(), start()]);
    expect(results.map(r => r.created).sort()).toEqual([false, true]);
    expect(f.raw.prepare("SELECT COUNT(*) AS n FROM flow_runs WHERE flow_id='child-fanout'").get().n).toBe(1);
  });

  it('rejects a reused ID for a foreign identity and an invented child ID', async () => {
    await start();
    await expect(start({ ...childInput(), groupExecutionId: 'wrong' })).rejects.toMatchObject({ statusCode: 409 });
    await expect(start(childInput(), 'not-allocated')).rejects.toMatchObject({ statusCode: 409 });
    await expect(start({ ...childInput(), sessionKey: 'foreign' })).rejects.toMatchObject({ statusCode: 409 });
    const g = group('other'); g.children[0].childFlowId = 'child-fanout'; await save(g);
    await expect(start(childInput(g))).rejects.toMatchObject({ statusCode: 409 });
  });

  it('checks full spec, parameters, metadata, and initial-state digest on duplicate requests', async () => {
    await start();
    for (const stateJson of [
      { ...childInput().stateJson, workflowSnapshot: { ...group().childWorkflow, nodes: [] } },
      { ...childInput().stateJson, params: { input: 'changed' } },
      { ...childInput().stateJson, workflowData: { injected: true } },
      { ...childInput().stateJson, subworkflowMeta: { parentFlowId: 'wrong', parentNodeId: 'fanout', depth: 1 } },
    ]) await expect(start({ ...childInput(), stateJson })).rejects.toMatchObject({ statusCode: 409 });
  });

  it('fences creation after cancellation while allowing observation of an existing child', async () => {
    f.raw.exec("UPDATE flow_runs SET status='cancelled' WHERE flow_id='parent'");
    await expect(start()).rejects.toMatchObject({ statusCode: 409 });
    f.raw.exec("UPDATE flow_runs SET status='running' WHERE flow_id='parent'");
    await start();
    f.raw.exec("UPDATE flow_runs SET status='cancelled' WHERE flow_id='parent'");
    expect((await start()).created).toBe(false);
  });

  it('does not create queued or cancelled attempts even if the parent is running', async () => {
    const g = group(); g.children[0].status = 'cancel_requested';
    await save({ ...g, revision: 1 }, 0);
    await expect(start()).rejects.toMatchObject({ statusCode: 409 });
  });

  it('rejects stale checkpoints and any terminal overwrite or resurrection', async () => {
    const { run } = await start();
    const candidates = await Promise.allSettled(['running', 'waiting'].map(status => repo.checkpointChild('parent', run.flowId, {
      expectedRevision: 1, stateJson: run.stateJson, status,
    })));
    expect(candidates.filter(r => r.status === 'fulfilled')).toHaveLength(1);
    const current = await repo.getChild('parent', run.flowId);
    await repo.checkpointChild('parent', run.flowId, { expectedRevision: 2, stateJson: current.stateJson, status: 'succeeded' });
    const terminal = await repo.getChild('parent', run.flowId);
    for (const status of ['running', 'failed']) await expect(repo.checkpointChild('parent', run.flowId,
      { expectedRevision: 3, stateJson: terminal.stateJson, status })).rejects.toMatchObject({ statusCode: 409 });
  });

  it('preserves nested durable fanouts during checkpoints and rejects identity tampering', async () => {
    const { run } = await start();
    const state = { ...run.stateJson, subworkflowFanouts: { nested: { revision: 7 } } };
    f.raw.prepare("UPDATE flow_runs SET state_json = ? WHERE flow_id='child-fanout'").run(JSON.stringify(state));
    const updated = await repo.checkpointChild('parent', run.flowId, { expectedRevision: 1, stateJson: run.stateJson, status: 'running' });
    expect(updated.stateJson.subworkflowFanouts).toEqual({ nested: { revision: 7 } });
    await expect(repo.checkpointChild('parent', run.flowId, { expectedRevision: 2, status: 'running',
      stateJson: { ...updated.stateJson, _fanoutIdentity: { fake: true } } })).rejects.toMatchObject({ statusCode: 409 });
  });

  it('propagates insert and checkpoint storage failures', async () => {
    f.raw.exec(`CREATE TRIGGER refuse_insert BEFORE INSERT ON flow_runs BEGIN SELECT RAISE(ABORT, 'child insert failed'); END`);
    await expect(start()).rejects.toThrow('child insert failed');
    f.raw.exec('DROP TRIGGER refuse_insert');
    const { run } = await start();
    f.raw.exec(`CREATE TRIGGER refuse_checkpoint BEFORE UPDATE ON flow_runs BEGIN SELECT RAISE(ABORT, 'checkpoint failed'); END`);
    await expect(repo.checkpointChild('parent', run.flowId, { expectedRevision: 1, stateJson: run.stateJson, status: 'running' }))
      .rejects.toThrow('checkpoint failed');
  });
});

it('bootstraps an empty mirror even when another group was already durably saved', async () => {
  await save(group('earlier'));
  await save(group(), null, { workflowId: 'parent-wf', nodeStates: { task: { status: 'running' } } });
  expect(stored().nodeStates).toEqual({ task: { status: 'running' } });
  expect(Object.keys(stored().subworkflowFanouts).sort()).toEqual(['earlier', 'fanout']);
});

it('accepts the supported draft workflow version sentinel in frozen specs', async () => {
  const g = group(); g.childWorkflow.version = -1;
  await save(g);
  expect((await repo.getGroup('parent', 'fanout')).group?.childWorkflow).toEqual(g.childWorkflow);
});

it('does not rewind a terminal child snapshot while its group is still running', async () => {
  const g = group(); g.children[0].status = 'succeeded'; g.children[0].result = { answer: 'done' };
  await save(g);
  await expect(save({ ...group(), revision: 1 }, 0)).rejects.toMatchObject({ statusCode: 409 });
});

it('rejects a child state that contradicts optional launch metadata', async () => {
  await save();
  const input = childInput(); input.stateJson.subworkflowMeta.groupExecutionId = 'other-execution';
  await expect(start(input)).rejects.toMatchObject({ statusCode: 409 });
});

it('uses checkpoint CAS to grant one started claim and never allows that flag to rewind', async () => {
  await save();
  const input = childInput(); input.stateJson._fanoutRevision = 1; input.stateJson._fanoutStarted = false;
  const { run } = await start(input);
  expect(run.stateJson._fanoutStarted).toBe(false);
  const claims = await Promise.allSettled([1, 2].map(() => repo.checkpointChild('parent', run.flowId, {
    expectedRevision: 1, stateJson: { ...run.stateJson, _fanoutStarted: true }, status: 'running',
  })));
  expect(claims.filter(r => r.status === 'fulfilled')).toHaveLength(1);
  expect(claims.find(r => r.status === 'rejected')).toMatchObject({ reason: { statusCode: 409 } });
  const claimed = await repo.getChild('parent', run.flowId);
  await expect(repo.checkpointChild('parent', run.flowId, { expectedRevision: 2,
    stateJson: { ...claimed.stateJson, _fanoutStarted: false }, status: 'running' })).rejects.toMatchObject({ statusCode: 409 });
  const { _fanoutStarted: _started, ...withoutFlag } = claimed.stateJson;
  const later = await repo.checkpointChild('parent', run.flowId, { expectedRevision: 2, stateJson: withoutFlag, status: 'waiting' });
  expect(later.stateJson._fanoutStarted).toBe(true);
  expect((await start(input)).created).toBe(false);
});

it('does not allow a child create to smuggle an execution claim or nested fanout records', async () => {
  await save();
  for (const fields of [{ _fanoutStarted: true }, { _fanoutStarted: 'false' }, { subworkflowFanouts: { injected: group() } }]) {
    await expect(start({ ...childInput(), stateJson: { ...childInput().stateJson, ...fields } })).rejects.toMatchObject({ statusCode: 400 });
  }
});

it('fences a not-yet-started child claim after its parent or durable attempt is cancelled', async () => {
  await save();
  const { run } = await start();
  f.raw.exec("UPDATE flow_runs SET status='cancelled' WHERE flow_id='parent'");
  await expect(repo.checkpointChild('parent', run.flowId, { expectedRevision: 1,
    stateJson: { ...run.stateJson, _fanoutStarted: true }, status: 'running' })).rejects.toMatchObject({ statusCode: 409 });
  f.raw.exec("UPDATE flow_runs SET status='running' WHERE flow_id='parent'");
  const g = group(); g.children[0].status = 'cancel_requested'; await save({ ...g, revision: 1 }, 0);
  await expect(repo.checkpointChild('parent', run.flowId, { expectedRevision: 1,
    stateJson: { ...run.stateJson, _fanoutStarted: true }, status: 'running' })).rejects.toMatchObject({ statusCode: 409 });
});

it('keeps cancellation outside the atomic parent-check/child-insert transaction', async () => {
  await save();
  let signalRead!: () => void;
  let releaseRead!: () => void;
  const readReached = new Promise<void>(resolve => { signalRead = resolve; });
  const release = new Promise<void>(resolve => { releaseRead = resolve; });
  // Delay an actual SQLite SELECT, not its result, to schedule cancellation at
  // precisely the dangerous read/insert gap. Every read/write still reaches SQL.
  const gatedDb = new Proxy(f.db, { get(target, key) {
    if (key === 'transaction') return (operation: (tx: typeof f.db) => Promise<unknown>) => target.transaction(tx => {
      let firstRead = true;
      return operation(new Proxy(tx, { get(inner, method) {
        if (method === 'query') return async (sql: string, params: unknown[]) => {
          const rows = await inner.query(sql, params);
          if (firstRead) { firstRead = false; signalRead(); await release; }
          return rows;
        };
        const member = Reflect.get(inner, method);
        return typeof member === 'function' ? member.bind(inner) : member;
      } }) as typeof f.db);
    });
    const member = Reflect.get(target, key);
    return typeof member === 'function' ? member.bind(target) : member;
  } });
  const creating = new FanoutStateRepository(gatedDb).startOrGetChild('parent', 'child-fanout', childInput() as never);
  await readReached;
  const cancelling = f.db.exec("UPDATE flow_runs SET status='cancelled' WHERE flow_id=?", ['parent']);
  releaseRead();
  expect((await creating).created).toBe(true);
  await cancelling;
  expect((await repo.getGroup('parent', 'fanout')).parentStatus).toBe('cancelled');
  expect((await repo.getChild('parent', 'child-fanout')).revision).toBe(1);
  // Cancellation won before the later execution claim, so the just-created child
  // still cannot execute. This also covers the initial _fanoutStarted=false gap.
  const child = await repo.getChild('parent', 'child-fanout');
  await expect(repo.checkpointChild('parent', child.flowId, { expectedRevision: 1,
    stateJson: { ...child.stateJson, _fanoutStarted: true }, status: 'running' })).rejects.toMatchObject({ statusCode: 409 });
});


it('accepts a published snapshot without spec.version and preserves its independent publication pin', async () => {
  const g = group(); delete g.childWorkflow.version;
  g.childPublishedVersion = 7;
  f.raw.exec('UPDATE workflow_specs SET version = 7');
  await save(g);
  const { run } = await start(childInput(g));
  expect(run.stateJson.workflowSnapshot).toEqual(g.childWorkflow);
  expect(run.stateJson.workflowSnapshot).not.toHaveProperty('version');
  await save(seal(g, 'succeeded'), 0);
  const durable = await repo.getGroup('parent', 'fanout');
  expect(durable.group?.childPublishedVersion).toBe(7);
  expect(durable.group?.result).toMatchObject({ childPublishedVersion: 7 });
});

it.each([undefined, null, 0, -1, 1.5, '1', Number.MAX_SAFE_INTEGER + 1])('rejects invalid childPublishedVersion %s', async version => {
  await expect(save({ ...group(), childPublishedVersion: version })).rejects.toMatchObject({ statusCode: 400 });
  expect((await repo.getGroup('parent', 'fanout')).group).toBeNull();
});

it.each([null, -2, 1.5, '1', Number.MAX_SAFE_INTEGER + 1])('still rejects an invalid explicit spec.version %s', async version => {
  const g = group(); g.childWorkflow.version = version;
  await expect(save(g)).rejects.toMatchObject({ statusCode: 400 });
});

it('does not let a valid CAS replace the published version of a frozen group', async () => {
  await save();
  await expect(save({ ...group(), revision: 1, childPublishedVersion: 2 }, 0)).rejects.toMatchObject({ statusCode: 409 });
  expect((await repo.getGroup('parent', 'fanout')).group).toMatchObject({ revision: 0, childPublishedVersion: 1 });
});

it('requires a sealed result to use the same published version as its group', async () => {
  await save();
  const sealed = seal(group(), 'succeeded'); sealed.result.childPublishedVersion = 2;
  await expect(save(sealed, 0)).rejects.toMatchObject({ statusCode: 400 });
  expect((await repo.getGroup('parent', 'fanout')).group?.revision).toBe(0);
});

it.each([0, -1, Number.NaN, Number.POSITIVE_INFINITY])('rejects nonpositive or nonfinite cancellation grace %s', async grace => {
  const g = group(); g.config.cancelGraceSeconds = grace;
  await expect(save(g)).rejects.toMatchObject({ statusCode: 400 });
});

it('accepts a positive fractional cancellation grace', async () => {
  const g = group(); g.config.cancelGraceSeconds = 0.001;
  await save(g);
  expect((await repo.getGroup('parent', 'fanout')).group?.config).toMatchObject({ cancelGraceSeconds: 0.001 });
});
