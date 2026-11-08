// @vitest-environment node
import { setTimeout as delay } from 'node:timers/promises';
import { afterEach, beforeEach, expect, it } from 'vitest';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { FanoutStateRepository } from '../fanout-state-repository.js';
import { childInput, fixture, group } from './fanout-state-fixture.js';

let f: ReturnType<typeof fixture>;
let repo: FanoutStateRepository;
beforeEach(() => { f = fixture(); repo = new FanoutStateRepository(f.db); });
afterEach(async () => { await f.db.close(); });
const save = (g = group(), expectedRevision: number | null = null) =>
  repo.saveGroup(g.parentFlowId, g.groupNodeId, { group: g, expectedRevision });
const start = (g = group(), input = childInput(g), target = repo) =>
  target.startOrGetChild(g.parentFlowId, g.children[0].childFlowId, input as never);
const config = (cap: number) => ({ enabled: true,
  queueGuard: { enabled: true, maxQueueSize: 5000, maxEnqueuePerFlow: 3, activeStatuses: ['waiting'] },
  perWorkflow: { default: { maxConcurrent: cap } },
});
function otherParent(id: string) {
  f.raw.prepare('INSERT INTO flow_runs (flow_id, workflow_id, status, started_at) VALUES (?, ?, ?, ?)').run(id, 'parent-wf', 'running', 1);
  const g = group(id); g.parentFlowId = id; g.children[0].parentFlowId = id;
  return g;
}
function inputFor(g: Record<string, any>) {
  const input = childInput(g); input.stateJson.subworkflowMeta.parentFlowId = g.parentFlowId;
  return input;
}
function seal(g: Record<string, any>, status: string) {
  const counts = { total: 1, successCount: 0, failedCount: 0, cancelledCount: 0, timedOutCount: 0, rejectedCount: 0, pendingCount: 1 };
  return { ...g, revision: 1, status, sealedAt: new Date().toISOString(), result: {
    schemaVersion: 'fanout.v1', kind: 'subworkflowFanoutResult', parentFlowId: g.parentFlowId,
    groupNodeId: g.groupNodeId, groupExecutionId: g.groupExecutionId, groupRevision: 1,
    childWorkflowId: g.childWorkflow.id, childPublishedVersion: g.childPublishedVersion, status, complete: false, businessOutcome: 'incomplete',
    successPolicy: 'all_done', finishMode: 'wait_all', resultPolicy: 'collect_all', ...counts, counts,
    items: g.children, error: null,
  } };
}

it('normalizes native params exactly like legacy FlowState without losing the raw input binding', async () => {
  const g = group();
  g.children[0].params = { number: 3, string: '3', object: { z: 2, a: true }, array: [1, false], nil: null, bool: false };
  await save(g);
  const input = childInput(g);
  input.stateJson.params = { number: '3', string: '3', object: '{"z":2,"a":true}', array: '[1,false]', nil: 'null', bool: 'false' };
  input.stateJson._fanoutRequestDigest = 'original-raw-request-digest';
  const created = await start(g, input);
  expect(created.run.stateJson.params).toEqual(input.stateJson.params);
  expect(created.run.stateJson._fanoutRequestDigest).toBe('original-raw-request-digest');
  expect((await repo.getGroup('parent', 'fanout')).group?.children).toEqual(g.children);
  const row = f.raw.prepare('SELECT params_json FROM flow_runs WHERE flow_id = ?').get(g.children[0].childFlowId);
  expect(JSON.parse(row.params_json)).toEqual(input.stateJson.params);
  expect((await start(g, input)).created).toBe(false);
  await expect(start(g, { ...input, stateJson: { ...input.stateJson, _fanoutRequestDigest: 'different-raw-request' } }))
    .rejects.toMatchObject({ statusCode: 409 });
});

it('keeps distinct raw bindings even when native number and string normalize identically', async () => {
  const a = group('a'); a.children[0].params = { value: 3 };
  const b = group('b'); b.children[0].params = { value: '3' };
  await save(a); await save(b);
  const ai = childInput(a); ai.stateJson.params = { value: '3' };
  const ar = await start(a, ai); const br = await start(b);
  expect(ar.run.stateJson._fanoutIdentity.paramsDigest).toBe(br.run.stateJson._fanoutIdentity.paramsDigest);
  expect(ar.run.stateJson._fanoutIdentity.rawParamsDigest).not.toBe(br.run.stateJson._fanoutIdentity.rawParamsDigest);
});

it('rejects an expired succeeded seal but still permits timeout cleanup', async () => {
  const g = { ...group(), deadlineAt: new Date(Date.now() - 1).toISOString() };
  await save(g);
  await expect(save(seal(g, 'succeeded'), 0)).rejects.toMatchObject({ statusCode: 409 });
  expect((await repo.getGroup('parent', 'fanout')).group?.revision).toBe(0);
  await save(seal(g, 'timed_out'), 0);
  expect((await repo.getGroup('parent', 'fanout')).group?.status).toBe('timed_out');
});

it('rejects creation when the durable deadline has already expired', async () => {
  const g = { ...group(), deadlineAt: new Date(Date.now() - 1).toISOString() };
  await save(g);
  await expect(start(g)).rejects.toMatchObject({ statusCode: 409 });
  await expect(repo.getChild('parent', g.children[0].childFlowId)).rejects.toMatchObject({ statusCode: 404 });
});

/** Pause the actual SQL mutation, after all repository reads/checks, then let the
 * real database clock pass the deadline. This catches preflight-only time checks. */
function atWriteBoundary(db: IDatabase, match: (sql: string) => boolean) {
  let entered!: () => void; let resume!: () => void; let gated = false;
  const reached = new Promise<void>(resolve => { entered = resolve; });
  const release = new Promise<void>(resolve => { resume = resolve; });
  const wrap = (inner: IDatabase): IDatabase => new Proxy(inner, { get(target, key) {
    if (key === 'transaction') return (operation: (tx: IDatabase) => Promise<unknown>) => target.transaction(tx => operation(wrap(tx)));
    if (key === 'exec') return async (sql: string, params: unknown[]) => {
      if (!gated && match(sql)) { gated = true; entered(); await release; }
      return target.exec(sql, params);
    };
    const member = Reflect.get(target, key);
    return typeof member === 'function' ? member.bind(target) : member;
  } });
  return { db: wrap(db), reached, resume };
}

it.each(['seal', 'create', 'claim'])('fences %s when deadline expires between validation and its SQL write', async operation => {
  const deadline = Date.now() + 1_000;
  const g = { ...group(), deadlineAt: new Date(deadline).toISOString() };
  await save(g);
  const run = operation === 'claim' ? (await start(g)).run : null;
  const gate = atWriteBoundary(f.db, sql => operation === 'create' ? sql.startsWith('INSERT INTO flow_runs') : sql.startsWith('UPDATE flow_runs SET state_json'));
  const fenced = new FanoutStateRepository(gate.db);
  const pending = operation === 'create' ? start(g, childInput(g), fenced)
    : operation === 'seal' ? fenced.saveGroup('parent', 'fanout', { group: seal(g, 'succeeded'), expectedRevision: 0 })
      : fenced.checkpointChild('parent', run!.flowId, { expectedRevision: 1, status: 'running', stateJson: { ...run!.stateJson, _fanoutStarted: true } });
  const rejected = expect(pending).rejects.toMatchObject({ statusCode: 409 });
  try { await gate.reached; await delay(Math.max(1, deadline - Date.now() + 20)); }
  finally { gate.resume(); }
  await rejected;
  if (operation === 'create') await expect(repo.getChild('parent', g.children[0].childFlowId)).rejects.toMatchObject({ statusCode: 404 });
  if (operation === 'seal') expect((await repo.getGroup('parent', 'fanout')).group?.revision).toBe(0);
  if (operation === 'claim') {
    expect((await repo.getChild('parent', run!.flowId)).revision).toBe(1);
    expect((await start(g)).created).toBe(false); // Expiration must not block observation/rejoin.
    await repo.checkpointChild('parent', run!.flowId, { expectedRevision: 1, status: 'cancelled', stateJson: run!.stateJson });
  }
});

it('serializes two parents competing for one workflow slot, then admits the same losing child ID after release', async () => {
  const a = group(); const b = otherParent('other-parent'); await save(a); await save(b);
  const capped = new FanoutStateRepository(f.db, config(1));
  const outcomes = await Promise.allSettled([start(a, inputFor(a), capped), start(b, inputFor(b), capped)]);
  expect(outcomes.filter(o => o.status === 'fulfilled')).toHaveLength(1);
  expect(outcomes.find(o => o.status === 'rejected')).toMatchObject({ reason: { statusCode: 409, code: 'CAPACITY_BUSY' } });
  const winner = outcomes[0].status === 'fulfilled' ? a : b;
  const loser = winner === a ? b : a;
  expect((await start(winner, inputFor(winner), capped)).created).toBe(false);
  const run = await capped.getChild(winner.parentFlowId, winner.children[0].childFlowId);
  await capped.checkpointChild(winner.parentFlowId, run.flowId, { expectedRevision: 1, status: 'cancelled', stateJson: run.stateJson });
  expect((await start(loser, inputFor(loser), capped)).created).toBe(true);
});

it('defaults to five finite slots even when legacy flow control is disabled', async () => {
  const capped = new FanoutStateRepository(f.db, { enabled: false, queueGuard: config(1).queueGuard });
  for (let i = 0; i < 6; i++) {
    const g = group(`node-${i}`); await save(g);
    if (i < 5) expect((await start(g, inputFor(g), capped)).created).toBe(true);
    else await expect(start(g, inputFor(g), capped)).rejects.toMatchObject({ statusCode: 409, code: 'CAPACITY_BUSY' });
  }
});

it.each([0, -1, 1.5, Number.NaN, Number.POSITIVE_INFINITY])('fails closed for invalid configured capacity %s', async cap => {
  await save();
  await expect(start(group(), childInput(), new FanoutStateRepository(f.db, config(cap)))).rejects.toMatchObject({ statusCode: 503 });
});

it.each(['absent', 'unpublished'])('refuses new children without a lockable published workflow: %s', async mode => {
  await save();
  f.raw.exec(mode === 'absent' ? 'DELETE FROM workflow_specs' : 'UPDATE workflow_specs SET version = NULL');
  await expect(start()).rejects.toMatchObject({ statusCode: 409, code: 'WORKFLOW_NOT_PUBLISHED' });
  await expect(repo.getChild('parent', 'child-fanout')).rejects.toMatchObject({ statusCode: 404 });
});
