// @vitest-environment node
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import Database from 'better-sqlite3';
import { SqliteDatabase, type IDatabase } from '@avernet/clawweb-shared/server/db';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { FanoutStateRepository } from '../fanout-state-repository.js';
import { FlowRunRepository } from '../flow-run-repository.js';
import { childInput, fixture, group } from './fanout-state-fixture.js';

let dir: string;
let db: SqliteDatabase;
let repo: FanoutStateRepository;
let path: string;
const queued = () => { const g = group(); g.children[0].status = 'queued'; return g; };
const claim = (token: string) => {
  const next = queued(); next.revision = 1; next.children[0].status = 'dispatch_pending';
  next.children[0].dispatchClaimToken = token; next.cleanupPendingChildIds = [next.children[0].childFlowId]; return next;
};
beforeEach(async () => {
  dir = mkdtempSync(join(tmpdir(), 'fanout-claim-'));
  path = join(dir, 'state.sqlite');
  const f = fixture(); await f.raw.backup(path); await f.db.close();
  db = new SqliteDatabase(new Database(path)); repo = new FanoutStateRepository(db);
  await repo.saveGroup('parent', 'fanout', { group: queued(), expectedRevision: null });
});
afterEach(async () => { await db.close(); rmSync(dir, { recursive: true, force: true }); });

it('rejects a stale claim when a different process commits the same revision first', async () => {
  const winner = claim('other-process'); let raced = false;
  const sqlite = createRequire(import.meta.url).resolve('better-sqlite3');
  const competing = new Proxy(db as IDatabase, { get(target, key) {
    if (key === 'transaction') return async (fn: (tx: IDatabase) => Promise<unknown>) => {
      raced = true;
      const script = `const Database = require(process.argv[1]); const db = new Database(process.argv[2]);
        const row = db.prepare("SELECT state_json FROM flow_runs WHERE flow_id='parent'").get();
        const state = JSON.parse(row.state_json); state.subworkflowFanouts.fanout = JSON.parse(process.argv[3]);
        const result = db.prepare("UPDATE flow_runs SET state_json=? WHERE flow_id='parent' AND state_json=? AND status='running'")
          .run(JSON.stringify(state), row.state_json); if (result.changes !== 1) process.exit(2); db.close();`;
      execFileSync(process.execPath, ['-e', script, sqlite, path, JSON.stringify(winner)]);
      return target.transaction(fn);
    };
    const member = Reflect.get(target, key); return typeof member === 'function' ? member.bind(target) : member;
  } });
  await expect(new FanoutStateRepository(competing).saveGroup('parent', 'fanout', { group: claim('local'), expectedRevision: 0 }))
    .rejects.toMatchObject({ statusCode: 409 });
  expect(raced).toBe(true);
  expect((await repo.getGroup('parent', 'fanout')).group).toEqual(winner);
});

it('propagates a failed claim write without persisting any claim or revision', async () => {
  await db.exec("CREATE TRIGGER reject_claim BEFORE UPDATE OF state_json ON flow_runs BEGIN SELECT RAISE(ABORT, 'disk write failed'); END");
  await expect(repo.saveGroup('parent', 'fanout', { group: claim('local'), expectedRevision: 0 }))
    .rejects.toThrow('disk write failed');
  expect((await repo.getGroup('parent', 'fanout')).group).toEqual(queued());
});


it('keeps ordinary parent and child abort independent after a claim', async () => {
  const claimed = claim('claim-token');
  await repo.saveGroup('parent', 'fanout', { group: claimed, expectedRevision: 0 });
  await db.exec('INSERT INTO flow_runs (flow_id, workflow_id, status, started_at) VALUES (?, ?, ?, ?)',
    ['child-fanout', 'child-wf', 'running', 1]);
  const runs = new FlowRunRepository(db);
  expect(await runs.abort('parent')).toBe(true);
  const stopping = { ...claimed, revision: 2, status: 'cancelling',
    cancelRequestedAt: claimed.createdAt, cancelDeadlineAt: claimed.deadlineAt };
  await repo.saveGroup('parent', 'fanout', { group: stopping, expectedRevision: 1 });
  const child = () => db.query<{ status: string }>('SELECT status FROM flow_runs WHERE flow_id = ?', ['child-fanout']);
  expect((await child())[0].status).toBe('running');
  expect((await repo.getGroup('parent', 'fanout')).parentStatus).toBe('cancelled');
  expect(await runs.abort('child-fanout')).toBe(true);
  expect((await child())[0].status).toBe('cancelled');
  expect((await repo.getGroup('parent', 'fanout')).group).toEqual(stopping);
});

it('preserves legacy queued-to-pending reservation but cannot claim it after restart', async () => {
  const reserved = claim('unused'); delete reserved.children[0].dispatchClaimToken;
  await repo.saveGroup('parent', 'fanout', { group: reserved, expectedRevision: 0 });
  const restarted = new FanoutStateRepository(db);
  expect((await restarted.getGroup('parent', 'fanout')).group).toEqual(reserved);
  const unsafe = structuredClone(reserved); unsafe.revision = 2; unsafe.children[0].dispatchClaimToken = 'late-claim';
  await expect(restarted.saveGroup('parent', 'fanout', { group: unsafe, expectedRevision: 1 }))
    .rejects.toMatchObject({ statusCode: 409 });
  expect((await restarted.getGroup('parent', 'fanout')).group).toEqual(reserved);
});

it('never claims a legacy pending reservation, which may already have launched', async () => {
  const legacy = group();
  await db.exec('UPDATE flow_runs SET state_json = ? WHERE flow_id = ?',
    [JSON.stringify({ subworkflowFanouts: { fanout: legacy } }), 'parent']);
  const candidate = structuredClone(legacy); candidate.revision = 1;
  candidate.children[0].dispatchClaimToken = 'unsafe'; candidate.cleanupPendingChildIds = [candidate.children[0].childFlowId];
  await expect(repo.saveGroup('parent', 'fanout', { group: candidate, expectedRevision: 0 }))
    .rejects.toMatchObject({ statusCode: 409 });
  expect((await repo.getGroup('parent', 'fanout')).group).toEqual(legacy);
});

it('requires exactly the collector cleanup projection and preserves siblings', async () => {
  const initial = queued();
  initial.children.push({ ...structuredClone(initial.children[0]), itemId: 'two', itemIndex: 1, childFlowId: 'child-two', status: 'running' });
  initial.children.push({ ...structuredClone(initial.children[0]), itemId: 'three', itemIndex: 2, childFlowId: 'child-three', status: 'succeeded' });
  initial.cleanupPendingChildIds = ['child-two'];
  await db.exec('UPDATE flow_runs SET state_json = ? WHERE flow_id = ?',
    [JSON.stringify({ subworkflowFanouts: { fanout: initial } }), 'parent']);
  const valid = structuredClone(initial); valid.revision = 1; valid.children[0].status = 'dispatch_pending';
  valid.children[0].dispatchClaimToken = 'token'; valid.cleanupPendingChildIds = ['child-fanout', 'child-two'];
  for (const cleanup of [[], ['child-fanout'], ['child-two', 'child-fanout'], ['child-fanout', 'child-two', 'child-three']]) {
    await expect(repo.saveGroup('parent', 'fanout', { group: { ...valid, cleanupPendingChildIds: cleanup }, expectedRevision: 0 }))
      .rejects.toMatchObject({ statusCode: 409 });
  }
  const siblingChanged = structuredClone(valid); siblingChanged.children[1].status = 'cancel_requested';
  await expect(repo.saveGroup('parent', 'fanout', { group: siblingChanged, expectedRevision: 0 }))
    .rejects.toMatchObject({ statusCode: 409 });
  await repo.saveGroup('parent', 'fanout', { group: valid, expectedRevision: 0 });
  expect((await repo.getGroup('parent', 'fanout')).group).toEqual(valid);
});


it.each(['dispatch_pending', 'dispatch_unknown', 'running', 'cancel_requested'].flatMap(status =>
  [false, true].map(token => ({ status, token }))))
('rejects rollback from $status to queued with claim=$token', async ({ status, token }) => {
  let current = token ? claim('existing-claim') : queued();
  if (token) await repo.saveGroup('parent', 'fanout', { group: current, expectedRevision: 0 });
  if (current.children[0].status !== status) {
    const next = structuredClone(current); next.revision++; next.children[0].status = status;
    next.cleanupPendingChildIds = [next.children[0].childFlowId];
    await repo.saveGroup('parent', 'fanout', { group: next, expectedRevision: current.revision });
    current = next;
  }
  const rollback = structuredClone(current); rollback.revision++; rollback.children[0].status = 'queued';
  rollback.cleanupPendingChildIds = [];
  await expect(repo.saveGroup('parent', 'fanout', { group: rollback, expectedRevision: current.revision }))
    .rejects.toMatchObject({ statusCode: 409 });
  expect((await repo.getGroup('parent', 'fanout')).group).toEqual(current);
});

it('rejects managed-child creation for a claimed attempt without inserting a child', async () => {
  const claimed = claim('ordinary-launch-token');
  await repo.saveGroup('parent', 'fanout', { group: claimed, expectedRevision: 0 });
  await expect(repo.startOrGetChild('parent', 'child-fanout', childInput(claimed) as never))
    .rejects.toMatchObject({ statusCode: 409 });
  expect(await db.query('SELECT flow_id FROM flow_runs WHERE flow_id = ?', ['child-fanout'])).toEqual([]);
  expect((await repo.getGroup('parent', 'fanout')).group).toEqual(claimed);
});

it('blocks a first managed checkpoint solely on an ordinary claim while parent and group run', async () => {
  const pending = queued(); pending.revision = 1; pending.children[0].status = 'dispatch_pending';
  pending.cleanupPendingChildIds = ['child-fanout'];
  await repo.saveGroup('parent', 'fanout', { group: pending, expectedRevision: 0 });
  const { run } = await repo.startOrGetChild('parent', 'child-fanout', childInput(pending) as never);
  expect(run.stateJson._fanoutStarted).toBe(false);
  // A contradictory snapshot could have been written before the existence fence.
  const historical = structuredClone(pending); historical.revision = 2;
  historical.children[0].dispatchClaimToken = 'ordinary-claim';
  await db.exec('UPDATE flow_runs SET state_json = ? WHERE flow_id = ?',
    [JSON.stringify({ subworkflowFanouts: { fanout: historical } }), 'parent']);
  expect(await repo.getGroup('parent', 'fanout')).toMatchObject({ parentStatus: 'running', group: { status: 'running' } });
  const before = await db.query('SELECT * FROM flow_runs WHERE flow_id = ?', ['child-fanout']);
  const firstStart = { ...run.stateJson, _fanoutStarted: true };
  await expect(repo.checkpointChild('parent', 'child-fanout', { expectedRevision: 1, status: 'running', stateJson: firstStart }))
    .rejects.toMatchObject({ statusCode: 409, message: 'Child execution is no longer authorized' });
  expect(await db.query('SELECT * FROM flow_runs WHERE flow_id = ?', ['child-fanout'])).toEqual(before);
  // Control: identical persisted state without a claim permits the first start.
  delete historical.children[0].dispatchClaimToken;
  await db.exec('UPDATE flow_runs SET state_json = ? WHERE flow_id = ?',
    [JSON.stringify({ subworkflowFanouts: { fanout: historical } }), 'parent']);
  const started = await repo.checkpointChild('parent', 'child-fanout', { expectedRevision: 1, status: 'running', stateJson: firstStart });
  expect(started.stateJson._fanoutStarted).toBe(true);
  expect(started.revision).toBe(2);
});

it('reuses an existing managed child for a claimed attempt without creating or mutating it', async () => {
  // Simulate a pre-existing child beside a historical queued snapshot: old
  // snapshots could be rewound before the new non-regression fence existed.
  const pending = queued(); pending.revision = 1; pending.children[0].status = 'dispatch_pending';
  pending.cleanupPendingChildIds = [pending.children[0].childFlowId];
  await repo.saveGroup('parent', 'fanout', { group: pending, expectedRevision: 0 });
  const input = childInput(pending) as never;
  const created = await repo.startOrGetChild('parent', 'child-fanout', input);
  expect(created.created).toBe(true);
  const historical = queued(); historical.revision = 2;
  await db.exec('UPDATE flow_runs SET state_json = ? WHERE flow_id = ?',
    [JSON.stringify({ subworkflowFanouts: { fanout: historical } }), 'parent']);
  const claimed = structuredClone(historical); claimed.revision = 3; claimed.children[0].status = 'dispatch_pending';
  claimed.children[0].dispatchClaimToken = 'ordinary-launch-token'; claimed.cleanupPendingChildIds = ['child-fanout'];
  // This incompatible combination was persistable before the existence fence.
  await db.exec('UPDATE flow_runs SET state_json = ? WHERE flow_id = ?',
    [JSON.stringify({ subworkflowFanouts: { fanout: claimed } }), 'parent']);
  await db.exec("UPDATE flow_runs SET status = 'cancelled' WHERE flow_id = ?", ['parent']);
  const before = await db.query('SELECT * FROM flow_runs WHERE flow_id = ?', ['child-fanout']);
  expect(await repo.startOrGetChild('parent', 'child-fanout', input)).toEqual({ created: false, run: created.run });
  expect(await db.query('SELECT * FROM flow_runs WHERE flow_id = ?', ['child-fanout'])).toEqual(before);
  const firstStart = { ...created.run.stateJson, _fanoutStarted: true };
  await expect(repo.checkpointChild('parent', 'child-fanout', { expectedRevision: 1, status: 'running', stateJson: firstStart }))
    .rejects.toMatchObject({ statusCode: 409 });
  expect(await db.query('SELECT * FROM flow_runs WHERE flow_id = ?', ['child-fanout'])).toEqual(before);
  // Model a child started before the historical contradictory snapshot arose.
  await db.exec('UPDATE flow_runs SET state_json = ? WHERE flow_id = ?', [JSON.stringify(firstStart), 'child-fanout']);
  const updated = await repo.checkpointChild('parent', 'child-fanout', { expectedRevision: 1, status: 'waiting', stateJson: firstStart });
  const finished = await repo.checkpointChild('parent', 'child-fanout', { expectedRevision: 2, status: 'succeeded', stateJson: updated.stateJson });
  expect(finished.status).toBe('succeeded');
  const wrong = { ...childInput(pending), sessionKey: 'different' };
  await expect(repo.startOrGetChild('parent', 'child-fanout', wrong as never)).rejects.toMatchObject({ statusCode: 409 });
});


it.each([false, true])('refuses a new claim beside a historical managed child, started=%s', async started => {
  const pending = queued(); pending.revision = 1; pending.children[0].status = 'dispatch_pending';
  pending.cleanupPendingChildIds = ['child-fanout'];
  await repo.saveGroup('parent', 'fanout', { group: pending, expectedRevision: 0 });
  const { run } = await repo.startOrGetChild('parent', 'child-fanout', childInput(pending) as never);
  if (started) await repo.checkpointChild('parent', run.flowId, { expectedRevision: 1, status: 'running',
    stateJson: { ...run.stateJson, _fanoutStarted: true } });
  const historical = queued(); historical.revision = 2;
  await db.exec('UPDATE flow_runs SET state_json = ? WHERE flow_id = ?',
    [JSON.stringify({ subworkflowFanouts: { fanout: historical } }), 'parent']);
  const candidate = claim('unsafe'); candidate.revision = 3;
  await expect(repo.saveGroup('parent', 'fanout', { group: candidate, expectedRevision: 2 }))
    .rejects.toMatchObject({ statusCode: 409 });
  expect((await repo.getGroup('parent', 'fanout')).group).toEqual(historical);
});

it('serializes an in-flight claim before a competing legacy managed creation', async () => {
  let entered!: () => void; let release!: () => void;
  const reached = new Promise<void>(resolve => { entered = resolve; });
  const gate = new Promise<void>(resolve => { release = resolve; });
  const wrap = (connection: IDatabase): IDatabase => new Proxy(connection, { get(target, key) {
    if (key === 'transaction') return (fn: (tx: IDatabase) => Promise<unknown>) => target.transaction(tx => fn(wrap(tx)));
    if (key === 'exec') return async (sql: string, params: unknown[]) => {
      if (sql.startsWith('UPDATE flow_runs SET state_json')) { entered(); await gate; }
      return target.exec(sql, params);
    };
    const member = Reflect.get(target, key); return typeof member === 'function' ? member.bind(target) : member;
  } });
  const candidate = claim('claim-winner');
  const claiming = new FanoutStateRepository(wrap(db)).saveGroup('parent', 'fanout', { group: candidate, expectedRevision: 0 });
  await reached;
  const creating = repo.startOrGetChild('parent', 'child-fanout', childInput(candidate) as never)
    .then(value => ({ value }), error => ({ error }));
  try {
    expect(await Promise.race([creating.then(() => 'finished'), new Promise(resolve => setTimeout(() => resolve('blocked'), 30))]))
      .toBe('blocked');
  } finally { release(); }
  expect((await claiming).group).toEqual(candidate);
  expect(await creating).toMatchObject({ error: { statusCode: 409 } });
  expect(await db.query('SELECT flow_id FROM flow_runs WHERE flow_id = ?', ['child-fanout'])).toEqual([]);
});
