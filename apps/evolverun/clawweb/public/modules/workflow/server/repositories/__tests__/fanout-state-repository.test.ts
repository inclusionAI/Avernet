// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
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

it('allows a durable dispatch claim to advance without changing launch identity', async () => {
  const g = group(); g.children[0].status = 'queued'; await save(g);
  const claimed = structuredClone(g); claimed.revision = 1; claimed.children[0].status = 'dispatch_pending';
  claimed.children[0].dispatchClaimToken = 'claim-token'; claimed.cleanupPendingChildIds = [claimed.children[0].childFlowId];
  await save(claimed, 0);
  expect(stored().subworkflowFanouts.fanout.children[0].dispatchClaimToken).toBe('claim-token');
});

describe('one-shot dispatch claims', () => {
  const queued = (nodeId?: string) => { const g = group(nodeId); g.children[0].status = 'queued'; return g; };
  const claimed = (g = queued(), token: unknown = 'claim-token', index = 0) => {
    const next = structuredClone(g); next.revision++; next.children[index].status = 'dispatch_pending';
    next.children[index].dispatchClaimToken = token; next.cleanupPendingChildIds = next.children
      .filter((child: any) => !['queued', 'succeeded', 'failed', 'cancelled', 'timed_out', 'rejected'].includes(child.status))
      .map((child: any) => child.childFlowId); return next;
  };
  const twoChildren = () => { const g = queued();
    g.children.push({ ...structuredClone(g.children[0]), itemId: 'two', itemIndex: 1, childFlowId: 'child-two' });
    return g;
  };

  it.each([[undefined], [null], [''], ['  '], [0], [false], [{}], [[]], [['token']]])('rejects malformed token %j', async token => {
    await save(queued());
    const invalid = claimed(); invalid.children[0].dispatchClaimToken = token;
    await expect(save(invalid, 0)).rejects.toMatchObject({ statusCode: 400 });
    expect(stored().subworkflowFanouts.fanout.revision).toBe(0);
  });

  it('reads and updates legacy children without a token, but rejects a preclaimed group creation', async () => {
    await expect(save({ ...claimed(), revision: 0 })).rejects.toMatchObject({ statusCode: 409 });
    await save(queued());
    const legacy = (await repo.getGroup('parent', 'fanout')).group as Record<string, any>;
    expect(legacy.children[0]).not.toHaveProperty('dispatchClaimToken');
    await save({ ...legacy, revision: 1 }, 0);
  });

  it.each(['dispatch_pending', 'dispatch_unknown', 'running', 'cancel_requested', 'succeeded'])('refuses to claim child status %s', async status => {
    const g = queued(); g.children[0].status = status; await save(g);
    await expect(save(claimed(g), 0)).rejects.toMatchObject({ statusCode: 409 });
  });

  it('refuses to claim an associated child or non-running group', async () => {
    const g = queued(); g.children[0].nativeFlowId = 'native'; await save(g);
    await expect(save(claimed(g), 0)).rejects.toMatchObject({ statusCode: 409 });
    const stopping = queued('stopping'); stopping.status = 'cancelling';
    stopping.cancelRequestedAt = stopping.createdAt; stopping.cancelDeadlineAt = stopping.deadlineAt;
    await save(stopping);
    await expect(save(claimed(stopping), 0)).rejects.toMatchObject({ statusCode: 409 });
  });

  it.each(['paused', 'pending', 'waiting', 'cancelled', 'succeeded'])('refuses a claim when parent is %s', async status => {
    await save(queued()); f.raw.prepare('UPDATE flow_runs SET status = ? WHERE flow_id = ?').run(status, 'parent');
    await expect(save(claimed(), 0)).rejects.toMatchObject({ statusCode: 409 });
    expect(stored().subworkflowFanouts.fanout.revision).toBe(0);
  });

  it('rejects an already expired claim without blocking an ordinary stop', async () => {
    const g = queued(); g.deadlineAt = new Date(Date.now() - 1000).toISOString(); await save(g);
    await expect(save(claimed(g), 0)).rejects.toMatchObject({ statusCode: 409 });
    await save({ ...g, revision: 1, status: 'cancelling', cancelRequestedAt: g.createdAt, cancelDeadlineAt: g.deadlineAt }, 0);
  });

  it.each(['other-child', 'second-claim', 'own-status', 'group-status', 'group-field'])('rejects simultaneous %s during first claim', async change => {
    const g = twoChildren(); await save(g);
    const next = claimed(g);
    if (change === 'other-child') next.children[1].status = 'running';
    if (change === 'second-claim') next.children[1].dispatchClaimToken = 'another';
    if (change === 'own-status') next.children[0].status = 'running';
    if (change === 'group-status') { next.status = 'cancelling'; next.cancelRequestedAt = g.createdAt; next.cancelDeadlineAt = g.deadlineAt; }
    if (change === 'group-field') next.cleanupPendingChildIds = [];
    await expect(save(next, 0)).rejects.toMatchObject({ statusCode: 409 });
    expect(stored().subworkflowFanouts.fanout).toEqual(g);
  });

  it('keeps an existing claim immutable while allowing normal progress and other child claims', async () => {
    const g = twoChildren(); await save(g);
    const first = claimed(g); await save(first, 0);
    for (const replacement of [undefined, 'different']) {
      const next = structuredClone(first); next.revision = 2;
      if (replacement === undefined) delete next.children[0].dispatchClaimToken;
      else next.children[0].dispatchClaimToken = replacement;
      await expect(save(next, 1)).rejects.toMatchObject({ statusCode: 409 });
    }
    const second = claimed(first, 'claim-two', 1);
    await save(second, 1);
    const progress = structuredClone(second); progress.revision = 3; progress.children[0].status = 'running';
    progress.children[0].nativeFlowId = 'native-one'; await save(progress, 2);
    expect((await repo.getGroup('parent', 'fanout')).group).toEqual(progress);
  });

  it('has one CAS winner and permits read-back after an unacknowledged write, not a duplicate commit', async () => {
    await save(queued());
    const candidates = await Promise.allSettled([save(claimed(queued(), 'first'), 0), save(claimed(queued(), 'second'), 0)]);
    expect(candidates.filter(candidate => candidate.status === 'fulfilled')).toHaveLength(1);
    expect(candidates.find(candidate => candidate.status === 'rejected')).toMatchObject({ reason: { statusCode: 409 } });
    const persisted = (await repo.getGroup('parent', 'fanout')).group as Record<string, any>;
    expect(['first', 'second']).toContain(persisted.children[0].dispatchClaimToken);
    await expect(save(persisted, 0)).rejects.toMatchObject({ statusCode: 409 });
    expect((await repo.getGroup('parent', 'fanout')).group).toEqual(persisted);
  });

  it('propagates a lost acknowledgement and exposes the committed claim for reconciliation', async () => {
    await save(queued());
    const unreliable = new Proxy(f.db as IDatabase, { get(target, key) {
      if (key === 'transaction') return async (fn: (db: IDatabase) => Promise<unknown>) => {
        await target.transaction(fn); throw new Error('claim response lost');
      };
      const member = Reflect.get(target, key); return typeof member === 'function' ? member.bind(target) : member;
    } });
    const candidate = claimed();
    await expect(new FanoutStateRepository(unreliable).saveGroup('parent', 'fanout', { group: candidate, expectedRevision: 0 }))
      .rejects.toThrow('claim response lost');
    expect((await repo.getGroup('parent', 'fanout')).group).toEqual(candidate);
    await expect(save(candidate, 0)).rejects.toMatchObject({ statusCode: 409 });
    await expect(save(claimed(queued(), 'other-token'), 0)).rejects.toMatchObject({ statusCode: 409 });
  });

  it('does not bootstrap unrelated parent mirror fields as part of a claim', async () => {
    await save(queued());
    await save(claimed(), 0, { currentPhase: 'untrusted' });
    expect(stored().currentPhase).toBeUndefined();
    expect(stored().subworkflowFanouts.fanout.children[0].dispatchClaimToken).toBe('claim-token');
  });

  it('preserves claims during a concurrent mirror update and a later stop', async () => {
    await save(queued());
    const first = claimed();
    await Promise.all([save(first, 0), new FlowRunRepository(f.db).updateStateJson('parent', JSON.stringify({ currentPhase: 'next' }))]);
    expect(stored().currentPhase).toBe('next');
    expect(stored().subworkflowFanouts.fanout).toEqual(first);
    const stopping = { ...structuredClone(first), revision: 2, status: 'cancelling',
      cancelRequestedAt: first.createdAt, cancelDeadlineAt: first.deadlineAt };
    await save(stopping, 1);
    expect((await repo.getGroup('parent', 'fanout')).group).toEqual(stopping);
  });

  it.each(['mysql', 'zdas'])('generates byte-exact CAS and database-clock fences for %s', async dbType => {
    await save(queued());
    let statement = ''; let values: unknown[] = []; let locked = false; let inTransaction = false;
    const captured = new Proxy(f.db as IDatabase, { get(target, key) {
      if (key === 'dbType') return dbType;
      if (key === 'query') return (sql: string, params: unknown[]) => target.query(sql.replace(/ FOR UPDATE$/, ''), params);
      if (key === 'transaction') return async (fn: (db: IDatabase) => Promise<unknown>) => { inTransaction = true; return fn(captured); };
      if (key === 'exec') return async (sql: string, params: unknown[]) => {
        if (sql.startsWith('UPDATE flow_runs SET flow_id = flow_id')) { locked = inTransaction; return { affectedRows: 1 }; }
        statement = sql; values = params; throw new Error('captured SQL');
      };
      const member = Reflect.get(target, key); return typeof member === 'function' ? member.bind(target) : member;
    } });
    await expect(new FanoutStateRepository(captured).saveGroup('parent', 'fanout', { group: claimed(), expectedRevision: 0 }))
      .rejects.toThrow('captured SQL');
    expect(locked).toBe(true);
    expect(statement).toContain('BINARY state_json = BINARY ?');
    expect(statement).toContain('AND status = ?');
    expect(statement).toContain('UNIX_TIMESTAMP(CURRENT_TIMESTAMP(3)) * 1000');
    expect(values.slice(-2)).toEqual(['running', Date.parse(queued().deadlineAt)]);
  });

  it.each(['status', 'deadline'])('fences claim when %s changes before the SQL write', async mode => {
    const g = queued(); if (mode === 'deadline') g.deadlineAt = new Date(Date.now() + 500).toISOString();
    await save(g);
    let resume!: () => void; let entered!: () => void; let intercepted = false;
    const reached = new Promise<void>(resolve => { entered = resolve; });
    const release = new Promise<void>(resolve => { resume = resolve; });
    const wrap = (connection: IDatabase): IDatabase => new Proxy(connection, { get(target, key) {
      if (key === 'transaction') return (fn: (db: IDatabase) => Promise<unknown>) => target.transaction(tx => fn(wrap(tx)));
      if (key === 'exec') return async (sql: string, params: unknown[]) => {
        if (!intercepted && sql.startsWith('UPDATE flow_runs SET state_json')) {
          intercepted = true; entered(); await release;
        }
        return target.exec(sql, params);
      };
      const member = Reflect.get(target, key); return typeof member === 'function' ? member.bind(target) : member;
    } });
    const pending = new FanoutStateRepository(wrap(f.db)).saveGroup('parent', 'fanout', { group: claimed(g), expectedRevision: 0 });
    const rejected = expect(pending).rejects.toMatchObject({ statusCode: 409 });
    try {
      await reached;
      if (mode === 'status') f.raw.prepare('UPDATE flow_runs SET status = ? WHERE flow_id = ?').run('paused', 'parent');
      else await new Promise(resolve => setTimeout(resolve, Math.max(1, Date.parse(g.deadlineAt) - Date.now() + 30)));
    } finally { resume(); }
    await rejected;
    expect(stored().subworkflowFanouts.fanout).toEqual(g);
  });
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
    const g = group('other'); g.children[0].childFlowId = 'child-fanout';
    await expect(save(g)).rejects.toMatchObject({ statusCode: 409 });
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

it.each([null, 0, -1, 1.5, '1', Number.MAX_SAFE_INTEGER + 1])('rejects invalid childPublishedVersion %s', async version => {
  await expect(save({ ...group(), childPublishedVersion: version })).rejects.toMatchObject({ statusCode: 400 });
  expect((await repo.getGroup('parent', 'fanout')).group).toBeNull();
});

it.each([null, -2, 1.5, '1', Number.MAX_SAFE_INTEGER + 1])('preserves opaque spec.version metadata %s', async version => {
  const g = group(); g.childWorkflow.version = version;
  expect((await save(g)).group).toEqual(g);
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

function independentGroup() {
  const g = group();
  delete g.childPublishedVersion;
  delete g.config.cancelGraceSeconds;
  delete g.children[0].sessionKey;
  delete g.children[0].stateDirRef;
  g.children[0].nativeFlowId = null;
  return g;
}

it('round-trips an independent child and seals cancellation with a still-running child', async () => {
  const g = independentGroup();
  g.children[0].status = 'running';
  await save(g);
  expect((await repo.getGroup('parent', 'fanout')).group).toEqual(g);
  const cancelled = seal(g, 'cancelled');
  delete cancelled.result.childPublishedVersion;
  await save(cancelled, 0);
  expect((await repo.getGroup('parent', 'fanout')).group).toEqual(cancelled);
  expect(cancelled.result).toMatchObject({ complete: false, pendingCount: 1 });
  expect(f.raw.prepare("SELECT COUNT(*) AS n FROM flow_runs WHERE flow_id='child-fanout'").get().n).toBe(0);
});

it('binds a native flow once with CAS, without allowing removal or rebinding', async () => {
  const g = independentGroup(); await save(g);
  const bound = { ...g, revision: 1, children: [{ ...g.children[0], nativeFlowId: 'native-a' }] };
  const competing = { ...bound, children: [{ ...g.children[0], nativeFlowId: 'native-b' }] };
  const writes = await Promise.allSettled([save(bound, 0), save(competing, 0)]);
  expect(writes.filter(x => x.status === 'fulfilled')).toHaveLength(1);
  expect(writes.filter(x => x.status === 'rejected')).toMatchObject([{ reason: { statusCode: 409 } }]);
  const winner = (await repo.getGroup('parent', 'fanout')).group as Record<string, any>;
  for (const value of [null, undefined, winner.children[0].nativeFlowId === 'native-a' ? 'native-b' : 'native-a']) {
    await expect(save({ ...winner, revision: 2, children: [{ ...winner.children[0], nativeFlowId: value }] }, 1))
      .rejects.toMatchObject({ statusCode: 409 });
  }
});

it('freezes independent-child association and optional release metadata', async () => {
  const g = releasedGroup(); await save(g);
  for (const patch of [{ childFlowId: 'other' }, { itemId: 'different' }, { params: { input: 'other' } }, { sessionKey: 'new' }]) {
    await expect(save({ ...g, revision: 1, children: [{ ...g.children[0], ...patch }] }, 0)).rejects.toMatchObject({ statusCode: 409 });
  }
  await expect(save({ ...g, revision: 1, childRelease: { ...g.childRelease, releaseId: 'another-release' } }, 0)).rejects.toMatchObject({ statusCode: 409 });
});

it.each(['', '  ', 42, {}, false])('rejects malformed nativeFlowId %j', async nativeFlowId => {
  const g = independentGroup(); g.children[0].nativeFlowId = nativeFlowId;
  await expect(save(g)).rejects.toMatchObject({ statusCode: 400 });
});

it.each([undefined, -1, 0, 1, 7])('round-trips an independent child with workflow version %s', async version => {
  const g = independentGroup();
  if (version === undefined) delete g.childWorkflow.version; else g.childWorkflow.version = version;
  await save(g);
  expect((await repo.getGroup('parent', 'fanout')).group).toEqual(g);
});

it('treats absent native identity as unbound and preserves the first binding in legacy groups', async () => {
  const g = group(); await save(g);
  const bound = { ...g, revision: 1, children: [{ ...g.children[0], nativeFlowId: 'native-legacy' }] };
  await save(bound, 0);
  await expect(save({ ...g, revision: 2 }, 1)).rejects.toMatchObject({ statusCode: 409 });
  expect((await repo.getGroup('parent', 'fanout')).group).toEqual(bound);
});

it('does not change an ordinary child row when its parent group is cancelled', async () => {
  const g = independentGroup(); g.children[0].status = 'running';
  f.raw.prepare('INSERT INTO flow_runs (flow_id, workflow_id, status, started_at, state_json) VALUES (?, ?, ?, ?, ?)')
    .run('child-fanout', 'child-wf', 'running', 1, JSON.stringify({ marker: 'independent' }));
  await save(g);
  const cancelled = seal(g, 'cancelled'); delete cancelled.result.childPublishedVersion;
  await save(cancelled, 0);
  expect(f.raw.prepare("SELECT status, state_json FROM flow_runs WHERE flow_id='child-fanout'").get())
    .toEqual({ status: 'running', state_json: JSON.stringify({ marker: 'independent' }) });
});

it('retains the parent cancellation fence for independent child snapshots', async () => {
  const g = independentGroup(); await save(g);
  f.raw.exec("UPDATE flow_runs SET status='cancelled' WHERE flow_id='parent'");
  await expect(save({ ...g, revision: 1 }, 0)).rejects.toMatchObject({ statusCode: 409 });
  const succeeded = seal(g, 'succeeded'); delete succeeded.result.childPublishedVersion;
  await expect(save(succeeded, 0)).rejects.toMatchObject({ statusCode: 409 });
});

it('removes credentials from independent snapshots and does not include input in conflict errors', async () => {
  const g = independentGroup(); g.children[0].params = { input: 'sensitive-input', credentials: 'secret-credential' };
  const saved = await save(g);
  expect(JSON.stringify(saved)).not.toContain('secret-credential');
  expect(JSON.stringify(stored())).not.toContain('secret-credential');
  const next = independentGroup(); next.revision = 1; next.children[0].params = { input: 'different-sensitive-input' };
  let failure: unknown;
  try { await save(next, 0); } catch (error) { failure = error; }
  expect(failure).toMatchObject({ statusCode: 409 });
  expect(String(failure)).not.toContain('sensitive-input');
});

// Cross-language vector for ClawMind canonicalWorkflowJson: numeric keys are sorted
// lexically ("10" before "2"), not in JavaScript's object enumeration order.
function releasedGroup() {
  const g = independentGroup();
  g.childWorkflow.numericKeys = { 2: 'two', 10: 'ten' };
  g.childPublishedVersion = 1;
  g.childRelease = { publishedVersion: 1, releaseId: 'release-1',
    digest: 'sha256:32fe4573bcb7d2c9142d2e544dfce3d31a7fe2e3b6b1379f0b8fb95570caf9d0' };
  return g;
}
function resolvedGroup() {
  const g = independentGroup();
  g.childResolved = { id: g.childWorkflow.id, spec: structuredClone(g.childWorkflow), digest: 'catalog-digest',
    absolutePath: '/pack/child.yaml', source: { kind: 'workspace-pack', root: '/pack' },
    pack: { id: 'pack', version: '1', root: '/pack', digest: 'pack-digest' }, workflowVersion: 3 };
  return g;
}
function twoChildren() {
  const g = independentGroup();
  g.children.push({ ...g.children[0], itemId: 'two', itemIndex: 1, childFlowId: 'child-two', item: { id: 'two' } });
  return g;
}

it('rejects duplicate native identities on initial group creation', async () => {
  const g = twoChildren(); g.children.forEach((child: any) => { child.nativeFlowId = 'native-shared'; });
  await expect(save(g)).rejects.toMatchObject({ statusCode: 400 });
  expect((await repo.getGroup('parent', 'fanout')).group).toBeNull();
});

it('rejects a later CAS that assigns an already occupied native identity', async () => {
  const g = twoChildren(); g.children[0].nativeFlowId = 'native-shared'; await save(g);
  const next = structuredClone(g); next.revision = 1; next.children[1].nativeFlowId = 'native-shared';
  await expect(save(next, 0)).rejects.toMatchObject({ statusCode: 400 });
  expect((await repo.getGroup('parent', 'fanout')).group).toEqual(g);
});

it('arbitrates concurrent bindings to the same native identity and rejects a retry against the winner', async () => {
  const g = twoChildren(); await save(g);
  const candidates = [0, 1].map(index => {
    const next = structuredClone(g); next.revision = 1; next.children[index].nativeFlowId = 'native-shared'; return next;
  });
  const writes = await Promise.allSettled(candidates.map(next => save(next, 0)));
  expect(writes.filter(result => result.status === 'fulfilled')).toHaveLength(1);
  expect(writes.filter(result => result.status === 'rejected')).toMatchObject([{ reason: { statusCode: 409 } }]);
  const winner = (await repo.getGroup('parent', 'fanout')).group as Record<string, any>;
  const unbound = winner.children.findIndex((child: any) => child.nativeFlowId === null);
  winner.revision = 2; winner.children[unbound].nativeFlowId = 'native-shared';
  await expect(save(winner, 1)).rejects.toMatchObject({ statusCode: 400 });
});

it('accepts a genuine release using the ClawMind canonical digest including numeric keys', async () => {
  const g = releasedGroup(); await save(g);
  expect((await repo.getGroup('parent', 'fanout')).group).toEqual(g);
  await save({ ...g, revision: 1 }, 0);
});

it.each([
  null, {}, { version: 2 },
  { publishedVersion: 1, releaseId: '', digest: 'sha256:32fe4573bcb7d2c9142d2e544dfce3d31a7fe2e3b6b1379f0b8fb95570caf9d0' },
  { publishedVersion: 2, releaseId: 'release-1', digest: 'sha256:32fe4573bcb7d2c9142d2e544dfce3d31a7fe2e3b6b1379f0b8fb95570caf9d0' },
  { publishedVersion: 1, releaseId: 'release-1', digest: 'sha256:' + '0'.repeat(64) },
  { publishedVersion: 1, releaseId: 'release-1', digest: 'invalid' },
])('rejects invalid release identity %j', async childRelease => {
  await expect(save({ ...releasedGroup(), childRelease })).rejects.toMatchObject({ statusCode: 400 });
});

it('requires a publication pin for a release identity', async () => {
  const g = releasedGroup(); delete g.childPublishedVersion;
  await expect(save(g)).rejects.toMatchObject({ statusCode: 400 });
});

it('rejects a resolved child whose identity or spec differs from the saved workflow', async () => {
  const g = resolvedGroup();
  for (const childResolved of [null, {}, { ...g.childResolved, id: 'other' },
    { ...g.childResolved, spec: { ...g.childWorkflow, title: 'different' } }]) {
    await expect(save({ ...g, childResolved })).rejects.toMatchObject({ statusCode: 400 });
  }
});

it('freezes all resolved metadata after creation without fabricating release metadata', async () => {
  const g = resolvedGroup(); await save(g);
  expect((await repo.getGroup('parent', 'fanout')).group).toEqual(g);
  for (const patch of [{ workflowVersion: 4 }, { source: { kind: 'db' } }, { pack: { ...g.childResolved.pack, root: '/other' } }]) {
    await expect(save({ ...g, revision: 1, childResolved: { ...g.childResolved, ...patch } }, 0))
      .rejects.toMatchObject({ statusCode: 409 });
  }
  const next = { ...g, revision: 1 }; delete next.childResolved;
  await expect(save(next, 0)).rejects.toMatchObject({ statusCode: 409 });
});

it('rejects resolved-spec corruption in an existing durable group', async () => {
  const g = resolvedGroup(); await save(g);
  const state = stored(); state.subworkflowFanouts.fanout.childResolved.spec.version = 42;
  f.raw.prepare("UPDATE flow_runs SET state_json = ? WHERE flow_id='parent'").run(JSON.stringify(state));
  await expect(repo.getGroup('parent', 'fanout')).rejects.toThrow('Invalid durable fanout snapshot');
});

it('rejects duplicate native identities in an existing durable group', async () => {
  const g = twoChildren(); await save(g);
  const state = stored();
  state.subworkflowFanouts.fanout.children.forEach((child: any) => { child.nativeFlowId = 'native-shared'; });
  f.raw.prepare("UPDATE flow_runs SET state_json = ? WHERE flow_id='parent'").run(JSON.stringify(state));
  await expect(repo.getGroup('parent', 'fanout')).rejects.toThrow('Invalid durable fanout snapshot');
});

it('does not permit resolved metadata to be introduced after group creation', async () => {
  const g = independentGroup(); await save(g);
  await expect(save({ ...g, revision: 1, childResolved: resolvedGroup().childResolved }, 0))
    .rejects.toMatchObject({ statusCode: 409 });
});

it('rejects a digest change caused by a different lexically ordered numeric key', async () => {
  const g = releasedGroup(); g.childWorkflow.numericKeys['2'] = 'different';
  await expect(save(g)).rejects.toMatchObject({ statusCode: 400 });
});

function templateCredentialGroup() {
  const g = resolvedGroup();
  g.childWorkflow.nodes[0].executor.params = { credentials: '{{params.credentials}}' };
  g.childResolved.spec = structuredClone(g.childWorkflow);
  g.childPublishedVersion = 1;
  g.childRelease = { publishedVersion: 1, releaseId: 'release-template',
    digest: 'sha256:d1e60244c758f0d3309adcdaa992586e06540099e4e9671358450bceb2bdb8d9' };
  return g;
}

it('round-trips credential template fields without changing the released spec or its digest', async () => {
  const g = templateCredentialGroup();
  const saved = await save(g);
  expect(saved.group).toEqual(g);
  expect((await repo.getGroup('parent', 'fanout')).group).toEqual(g);
  expect(stored().subworkflowFanouts.fanout).toEqual(g);
  await save({ ...g, revision: 1 }, 0);
});

it.each(['literal-secret-value', 'prefix {{params.credentials}}', { ['token']: 'actual-secret-value' },
  { ['token']: '{{params.credentials}}', ['password']: 'actual-secret-value' }])
('fails closed for literal credentials in a workflow snapshot without exposing them or misreporting a digest mismatch', async credentials => {
  const g = templateCredentialGroup();
  g.childWorkflow.nodes[0].executor.params.credentials = credentials;
  g.childResolved.spec = structuredClone(g.childWorkflow);
  let failure: unknown;
  try { await save(g); } catch (error) { failure = error; }
  expect(failure).toMatchObject({ statusCode: 400, code: 'UNSAFE_WORKFLOW_CREDENTIALS' });
  expect(String(failure)).not.toMatch(/literal-secret-value|actual-secret-value|release identity/i);
  expect((await repo.getGroup('parent', 'fanout')).group).toBeNull();
});

it('rejects unsafe credentials in resolved.spec before redaction or digest comparison', async () => {
  const g = templateCredentialGroup();
  g.childResolved.spec.nodes[0].executor.params.credentials = 'actual-secret-value';
  await expect(save(g)).rejects.toMatchObject({ statusCode: 400, code: 'UNSAFE_WORKFLOW_CREDENTIALS' });
});

it.each([
  { digest: undefined }, { digest: 2 }, { digest: '' },
  { absolutePath: undefined }, { absolutePath: 42 },
  { pack: undefined }, { pack: {} }, { pack: { id: 'pack', version: '1', root: '/pack', digest: 4 } },
  { source: undefined }, { source: {} }, { source: { kind: 'unknown' } },
  { source: { kind: 'workspace-pack' } }, { source: { kind: 'configured-pack', root: 1 } },
  { workflowVersion: '1' }, { deployNumber: '1' }, { tagName: null },
])('rejects incomplete or malformed resolved workflow metadata %j', async patch => {
  const g = resolvedGroup(); g.childResolved = { ...g.childResolved, ...patch };
  await expect(save(g)).rejects.toMatchObject({ statusCode: 400 });
  expect((await repo.getGroup('parent', 'fanout')).group).toBeNull();
});

it.each(['db', 'deploy-history', 'remote-snapshot'])('accepts resolved %s metadata with intentionally empty pack paths', async kind => {
  const g = resolvedGroup();
  g.childResolved = { ...g.childResolved, source: { kind }, absolutePath: '', digest: 'db:1:2',
    pack: { id: '', version: '', root: '', digest: '' }, workflowVersion: -1, deployNumber: -1 };
  await save(g);
  expect((await repo.getGroup('parent', 'fanout')).group).toEqual(g);
});

it('keeps the previous pinned snapshot intact when an unsafe credential update is rejected', async () => {
  const g = templateCredentialGroup(); await save(g);
  const unsafe = structuredClone(g); unsafe.revision = 1;
  unsafe.childWorkflow.nodes[0].executor.params.credentials = 'literal-secret-value';
  unsafe.childResolved.spec = structuredClone(unsafe.childWorkflow);
  await expect(save(unsafe, 0)).rejects.toMatchObject({ statusCode: 400, code: 'UNSAFE_WORKFLOW_CREDENTIALS' });
  expect((await repo.getGroup('parent', 'fanout')).group).toEqual(g);
  expect(JSON.stringify(stored())).not.toContain('literal-secret-value');
});

it('preserves nested whole-value credential references without a release pin', async () => {
  const g = resolvedGroup();
  g.childWorkflow.nodes[0].executor.params = { ['credentials']: { ['token']: '{{params.credentials}}', optional: null },
    ['credentials_json']: '{{item.credentials}}' };
  g.childResolved.spec = structuredClone(g.childWorkflow);
  await save(g);
  expect((await repo.getGroup('parent', 'fanout')).group).toEqual(g);
});

it('does not return pre-existing unsafe credentials in a durable workflow snapshot', async () => {
  const g = resolvedGroup(); await save(g);
  const state = stored();
  state.subworkflowFanouts.fanout.childWorkflow.nodes[0].executor.credentials = 'literal-secret-value';
  state.subworkflowFanouts.fanout.childResolved.spec = structuredClone(state.subworkflowFanouts.fanout.childWorkflow);
  f.raw.prepare("UPDATE flow_runs SET state_json = ? WHERE flow_id='parent'").run(JSON.stringify(state));
  await expect(repo.getGroup('parent', 'fanout')).rejects.toMatchObject({ statusCode: 400, code: 'UNSAFE_WORKFLOW_CREDENTIALS' });
});

it('preserves safe workflow templates through legacy child start, read, rejoin and checkpoint while redacting runtime credentials', async () => {
  const g = group();
  g.childWorkflow.nodes[0].executor.params = { credentials: '{{params.credentials}}' };
  await save(g);
  const input = childInput(g);
  input.stateJson.credentials = 'runtime-secret';
  input.stateJson.workflowData = { ['credentials']: { ['token']: 'nested-runtime-secret' }, progress: 1 };
  const started = await start(input);
  expect(started.created).toBe(true);
  expect(started.run.stateJson.workflowSnapshot).toEqual(g.childWorkflow);
  expect(JSON.stringify(started)).not.toMatch(/runtime-secret|nested-runtime-secret|private-value/);
  const read = await repo.getChild('parent', started.run.flowId);
  expect(read).toEqual(started.run);
  expect(await start(input)).toEqual({ created: false, run: read });
  const updated = await repo.checkpointChild('parent', read.flowId, {
    expectedRevision: 1, status: 'waiting',
    stateJson: { ...read.stateJson, workflowData: { progress: 2, credentials: 'checkpoint-secret' } },
  });
  expect(updated.revision).toBe(2);
  expect(updated.stateJson.workflowSnapshot).toEqual(g.childWorkflow);
  expect(updated.stateJson.workflowData).toEqual({ progress: 2 });
  expect(await repo.getChild('parent', read.flowId)).toEqual(updated);
  expect(JSON.stringify(f.raw.prepare("SELECT state_json, params_json FROM flow_runs WHERE flow_id='child-fanout'").get()))
    .not.toMatch(/runtime-secret|nested-runtime-secret|checkpoint-secret|private-value/);
  await expect(repo.checkpointChild('parent', read.flowId, { expectedRevision: 1, status: 'running', stateJson: read.stateJson }))
    .rejects.toMatchObject({ statusCode: 409 });
});

it('rejects literal credentials in legacy child snapshot start and checkpoint without changing the stored child', async () => {
  const g = group(); g.childWorkflow.nodes[0].executor.params = { credentials: '{{params.credentials}}' };
  await save(g);
  const input = childInput(g);
  const unsafe = structuredClone(input);
  unsafe.stateJson.workflowSnapshot.nodes[0].executor.params.credentials = 'literal-secret-value';
  await expect(start(unsafe)).rejects.toMatchObject({ statusCode: 400, code: 'UNSAFE_WORKFLOW_CREDENTIALS' });
  const { run } = await start(input);
  const changed = structuredClone(run.stateJson) as Record<string, any>;
  changed.workflowSnapshot.nodes[0].executor.params.credentials = 'literal-secret-value';
  await expect(repo.checkpointChild('parent', run.flowId, { expectedRevision: 1, status: 'running', stateJson: changed }))
    .rejects.toMatchObject({ statusCode: 400, code: 'UNSAFE_WORKFLOW_CREDENTIALS' });
  expect(await repo.getChild('parent', run.flowId)).toEqual(run);
  expect(JSON.stringify(f.raw.prepare("SELECT state_json FROM flow_runs WHERE flow_id='child-fanout'").get()))
    .not.toContain('literal-secret-value');
});

it('preserves nested durable group templates when a managed child checkpoints', async () => {
  const g = group(); await save(g);
  const { run } = await start(childInput(g));
  const nested = templateCredentialGroup();
  nested.parentFlowId = run.flowId; nested.groupNodeId = 'nested'; nested.groupExecutionId = 'nested-execution';
  nested.children[0].parentFlowId = run.flowId; nested.children[0].parentNodeId = 'nested';
  nested.children[0].groupExecutionId = nested.groupExecutionId;
  await repo.saveGroup(run.flowId, 'nested', { group: nested, expectedRevision: null });
  const updated = await repo.checkpointChild('parent', run.flowId, { expectedRevision: 1, status: 'waiting', stateJson: run.stateJson });
  expect((updated.stateJson.subworkflowFanouts as Record<string, any>).nested).toEqual(nested);
  expect((await repo.getGroup(run.flowId, 'nested')).group).toEqual(nested);
});

it('fails closed when a durable legacy child workflow snapshot contains literal credentials', async () => {
  const g = group(); g.childWorkflow.nodes[0].executor.params = { credentials: '{{params.credentials}}' };
  await save(g);
  const { run } = await start(childInput(g));
  const state = JSON.parse(f.raw.prepare("SELECT state_json FROM flow_runs WHERE flow_id='child-fanout'").get().state_json);
  state.workflowSnapshot.nodes[0].executor.params.credentials = 'literal-secret-value';
  f.raw.prepare("UPDATE flow_runs SET state_json = ? WHERE flow_id='child-fanout'").run(JSON.stringify(state));
  await expect(repo.getChild('parent', run.flowId)).rejects.toMatchObject({ statusCode: 400, code: 'UNSAFE_WORKFLOW_CREDENTIALS' });
});

it('strips previously persisted runtime credentials during legacy reads and checkpoint writes', async () => {
  const g = group(); g.childWorkflow.nodes[0].executor.params = { credentials: '{{params.credentials}}' };
  await save(g);
  const { run } = await start(childInput(g));
  const state = JSON.parse(f.raw.prepare("SELECT state_json FROM flow_runs WHERE flow_id='child-fanout'").get().state_json);
  state.workflowData.credentials = { ['token']: 'previous-runtime-secret' };
  f.raw.prepare("UPDATE flow_runs SET state_json = ? WHERE flow_id='child-fanout'").run(JSON.stringify(state));
  const current = await repo.getChild('parent', run.flowId);
  expect(current.stateJson.workflowSnapshot).toEqual(g.childWorkflow);
  expect(JSON.stringify(current)).not.toContain('previous-runtime-secret');
  await repo.checkpointChild('parent', run.flowId, { expectedRevision: 1, status: 'waiting', stateJson: current.stateJson });
  expect(f.raw.prepare("SELECT state_json FROM flow_runs WHERE flow_id='child-fanout'").get().state_json)
    .not.toContain('previous-runtime-secret');
});

it('keeps legacy child checkpoint CAS for credential-template snapshots', async () => {
  const g = group(); g.childWorkflow.nodes[0].executor.params = { credentials: '{{params.credentials}}' };
  await save(g);
  const input = childInput(g); const { run } = await start(input);
  const writes = await Promise.allSettled(['waiting', 'running'].map(status => repo.checkpointChild('parent', run.flowId,
    { expectedRevision: 1, status, stateJson: run.stateJson })));
  expect(writes.filter(result => result.status === 'fulfilled')).toHaveLength(1);
  expect(writes.filter(result => result.status === 'rejected')).toMatchObject([{ reason: { statusCode: 409 } }]);
  const current = await repo.getChild('parent', run.flowId);
  expect(current.stateJson.workflowSnapshot).toEqual(g.childWorkflow);
  expect(await start(input)).toEqual({ created: false, run: current });
});

it('rejects legacy checkpoint rebinding to a different safe credential template', async () => {
  const g = group(); g.childWorkflow.nodes[0].executor.params = { credentials: '{{params.credentials}}' };
  await save(g);
  const { run } = await start(childInput(g));
  const changed = structuredClone(run.stateJson) as Record<string, any>;
  changed.workflowSnapshot.nodes[0].executor.params.credentials = '{{params.other}}';
  await expect(repo.checkpointChild('parent', run.flowId, { expectedRevision: 1, status: 'running', stateJson: changed }))
    .rejects.toMatchObject({ statusCode: 409 });
  expect(await repo.getChild('parent', run.flowId)).toEqual(run);
});

it.each(['{{input.params.credentials}}', '{{nodeOutput.compose-final.credentials}}',
  '{{params.providers[0].api-key}}', '{{nodeOutput.step@v2.2}}'])
('preserves the complete supported credential reference %s through group CAS', async reference => {
  const g = resolvedGroup();
  g.childWorkflow.nodes[0].executor.params = { credentials: reference };
  g.childResolved.spec = structuredClone(g.childWorkflow);
  await save(g);
  expect((await repo.getGroup('parent', 'fanout')).group).toEqual(g);
  await save({ ...g, revision: 1 }, 0);
  expect(stored().subworkflowFanouts.fanout.childWorkflow).toEqual(g.childWorkflow);
  const changed = structuredClone(g); changed.revision = 2;
  changed.childWorkflow.nodes[0].executor.params.credentials = '{{params.other}}';
  changed.childResolved.spec = structuredClone(changed.childWorkflow);
  await expect(save(changed, 1)).rejects.toMatchObject({ statusCode: 409 });
  expect(stored().subworkflowFanouts.fanout.childWorkflow).toEqual(g.childWorkflow);
});

it.each(['{{params.credentials}}\n', '{{nodeOutput.compose-final.credentials}}secret',
  '{{input.params.credentials}} {{params.other}}', '{{input.params.credentials || "secret"}}'])
('rejects a credential value containing a reference plus other content %j', async credentials => {
  const g = resolvedGroup();
  g.childWorkflow.nodes[0].executor.params = { credentials };
  g.childResolved.spec = structuredClone(g.childWorkflow);
  await expect(save(g)).rejects.toMatchObject({ statusCode: 400, code: 'UNSAFE_WORKFLOW_CREDENTIALS' });
  expect((await repo.getGroup('parent', 'fanout')).group).toBeNull();
});

it('fails closed when a legacy redacted snapshot cannot prove its missing credential reference', async () => {
  const g = group();
  g.childWorkflow.nodes[0].executor.params = { credentials: '{{params.credentials}}' };
  const legacy = structuredClone(g);
  delete legacy.childWorkflow.nodes[0].executor.params.credentials;
  await save(legacy);
  // This durable form is indistinguishable from a genuine snapshot which never
  // had credentials. Accepting the new field would also permit rebinding it.
  for (const credentials of ['{{params.credentials}}', '{{params.other}}']) {
    const next = structuredClone(g); next.revision = 1;
    next.childWorkflow.nodes[0].executor.params.credentials = credentials;
    await expect(save(next, 0)).rejects.toMatchObject({ statusCode: 409 });
  }
  expect((await repo.getGroup('parent', 'fanout')).group).toEqual(legacy);
  expect(stored().subworkflowFanouts.fanout).toEqual(legacy);
  await save({ ...legacy, revision: 1 }, 0);
  expect(stored().subworkflowFanouts.fanout).toEqual({ ...legacy, revision: 1 });
});
