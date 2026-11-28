import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { SqliteDatabase } from "../src/db/sqlite-database.js";
import { SchemaMigrator } from "../src/db/migrator.js";
import { FlowRunRepository } from "../src/db/repositories/flow-run-repository.js";
import { NodeExecutionRepository } from "../src/db/repositories/node-execution-repository.js";
import { loadRunningSnapshot } from "../src/recovery/scan.js";

async function fixture(t: any, path = ":memory:") {
  const db = new SqliteDatabase({ path });
  await db.connect();
  t.after(() => db.close());
  await db.exec(`CREATE TABLE IF NOT EXISTS flow_runs (
    id INTEGER PRIMARY KEY, flow_id TEXT UNIQUE, workflow_id TEXT, status TEXT,
    retry_epoch INTEGER NOT NULL DEFAULT 0, state_json TEXT,
    params_json TEXT, current_phase TEXT, result_json TEXT, input_json TEXT,
    total_duration_ms INTEGER, total_token_usage INTEGER,
    succeeded_count INTEGER DEFAULT 0, failed_count INTEGER DEFAULT 0,
    started_at INTEGER, completed_at INTEGER, gmt_modified INTEGER
  )`);
  await db.exec(`CREATE TABLE IF NOT EXISTS node_executions (
    id INTEGER PRIMARY KEY, flow_id TEXT, node_id TEXT, attempt INTEGER DEFAULT 1,
    status TEXT, error_text TEXT, completed_at INTEGER, gmt_modified INTEGER,
    output_json TEXT, duration_ms INTEGER, token_usage_json TEXT,
    embedded_session_key TEXT, system_context_json TEXT, resolved_prompt TEXT,
    started_at INTEGER, version INTEGER NOT NULL DEFAULT 0
  )`);
  const runs = new FlowRunRepository(db);
  const nodes = new NodeExecutionRepository(db);
  async function seed(id: string, status = "running", epoch = 0) {
    await db.exec("INSERT INTO flow_runs (flow_id, workflow_id, status, retry_epoch, started_at) VALUES (?, 'wf', ?, ?, 1)", [id, status, epoch]);
    await db.exec("INSERT INTO node_executions (flow_id, node_id, status) VALUES (?, 'node', 'running')", [id]);
  }
  return { db, runs, nodes, seed };
}
const reap = { reason: '{"error":"timeout"}', currentPhase: "timeout", completedAt: 50 };
const completion = { status: "succeeded", currentPhase: "done", completedAt: 50 };

test("timeout claim uses affectedRows: only running can win, never waiting/blocked/terminal", async t => {
  const { runs, db, seed } = await fixture(t);
  for (const status of ["running", "waiting", "blocked", "succeeded", "failed", "cancelled", "completed"]) {
    await seed(status, status);
    assert.equal(await runs.markFailedIfRunning(status, reap), status === "running", status);
    const [row] = await db.query<any>("SELECT status FROM flow_runs WHERE flow_id = ?", [status]);
    assert.equal(row.status, status === "running" ? "failed" : status);
  }
});

test("two independent SQLite writers have exactly one timeout winner", async t => {
  const dir = await mkdtemp(join(process.cwd(), ".closeout-sqlite-"));
  const path = join(dir, "runs.db");
  const first = await fixture(t, path);
  const second = await fixture(t, path);
  t.after(async () => { await first.db.close(); await second.db.close(); await rm(dir, { recursive: true, force: true }); });
  await first.seed("race", "running", 3);
  const winners = await Promise.all([
    first.runs.markFailedIfRunning("race", { ...reap, retryEpoch: 3 }),
    second.runs.markFailedIfRunning("race", { ...reap, retryEpoch: 3 }),
  ]);
  assert.deepEqual(winners.sort(), [false, true]);
});

test("timeout and completion reject a newer retry attempt including omitted legacy epoch", async t => {
  const { runs, seed, db } = await fixture(t);
  await seed("new", "running", 1);
  for (const retryEpoch of [undefined, 0]) {
    assert.equal(await runs.markFailedIfRunning("new", { ...reap, retryEpoch }), false);
    assert.equal(await runs.updateCompletion("new", { ...completion, retryEpoch }), false);
  }
  assert.equal((await db.query<any>("SELECT status FROM flow_runs WHERE flow_id = 'new'"))[0].status, "running");
  assert.equal(await runs.updateCompletion("new", { ...completion, retryEpoch: 1 }), true);
  assert.equal(await runs.markFailedIfRunning("new", { ...reap, retryEpoch: 1 }), false);
});

test("completion is an epoch CAS and cannot overwrite any terminal row", async t => {
  const { runs, seed, db } = await fixture(t);
  await seed("legacy");
  assert.equal(await runs.updateCompletion("legacy", completion), true);
  for (const status of ["succeeded", "failed", "cancelled", "completed"]) {
    await seed(status, status);
    assert.equal(await runs.updateCompletion(status, { ...completion, resultJson: "wrong" }), false);
    const [row] = await db.query<any>("SELECT status, result_json FROM flow_runs WHERE flow_id = ?", [status]);
    assert.equal(row.status, status);
    assert.equal(row.result_json, null);
  }
  assert.equal(await runs.updateCompletion("missing", completion), false);
});

test("stale scanned epoch cannot claim after retry; scans preserve epoch/state snapshot", async t => {
  const { runs, seed, db } = await fixture(t);
  await seed("snapshot");
  await db.exec("UPDATE flow_runs SET state_json = ? WHERE flow_id = ?", ['{"retryEpoch":0}', "snapshot"]);
  const [snapshot] = await runs.findStaleRunning(10);
  assert.equal(snapshot.retry_epoch, 0);
  assert.equal(snapshot.state_json, '{"retryEpoch":0}');
  await db.exec("UPDATE flow_runs SET retry_epoch = 1 WHERE flow_id = ?", ["snapshot"]);
  assert.equal(await runs.markFailedIfRunning("snapshot", { ...reap, retryEpoch: snapshot.retry_epoch }), false);
});

test("explicit invalid epochs fail closed for completion, claim and reconciliation", async t => {
  const { runs, nodes, seed, db } = await fixture(t);
  await seed("invalid");
  for (const retryEpoch of [-1, "0", null, NaN, Infinity, 0.5]) {
    await assert.rejects(runs.updateCompletion("invalid", { ...completion, retryEpoch } as any), /epoch/i);
    await assert.rejects(runs.markFailedIfRunning("invalid", { ...reap, retryEpoch } as any), /epoch/i);
    await assert.rejects(nodes.reconcileStaleRunning("invalid", "failed", undefined, retryEpoch as any), /epoch/i);
  }
  assert.equal((await db.query<any>("SELECT status FROM flow_runs WHERE flow_id = 'invalid'"))[0].status, "running");
});

test("node reconciliation atomically checks matching terminal parent status and epoch", async t => {
  const { nodes, seed, db } = await fixture(t);
  const cases = [
    ["running", "failed", 0, 0, false], ["waiting", "failed", 0, 0, false],
    ["blocked", "failed", 0, 0, false], ["failed", "succeeded", 0, 0, false],
    ["succeeded", "failed", 0, 0, false], ["failed", "failed", 1, 0, false],
    ["failed", "failed", 1, undefined, false], ["failed", "failed", 1, 1, true],
    ["failed", "failed", 0, undefined, true], ["succeeded", "succeeded", 0, 0, true],
    ["completed", "succeeded", 0, 0, true], ["succeeded", "completed", 0, 0, true],
    ["cancelled", "cancelled", 0, 0, true], ["running", "running", 0, 0, false],
  ] as const;
  for (const [index, [parent, requested, epoch, expected, allowed]] of cases.entries()) {
    const id = `parent-${index}`;
    await seed(id, parent, epoch);
    assert.equal(await nodes.reconcileStaleRunning(id, requested, "custom timeout", expected), allowed ? 1 : 0, id);
    const [node] = await db.query<any>("SELECT status, error_text FROM node_executions WHERE flow_id = ?", [id]);
    assert.equal(node.status, allowed ? (requested === "failed" ? "failed" : "skipped") : "running");
    assert.equal(node.error_text, allowed ? "custom timeout" : null);
  }
  await db.exec("INSERT INTO node_executions (flow_id, status) VALUES ('absent', 'running')");
  assert.equal(await nodes.reconcileStaleRunning("absent", "failed"), 0);
});

test("completion, timeout and reconcile propagate real SQLite write rejection", async t => {
  const { runs, nodes, seed, db } = await fixture(t);
  await seed("reject");
  await db.exec("CREATE TRIGGER reject_run BEFORE UPDATE ON flow_runs BEGIN SELECT RAISE(ABORT, 'write rejected'); END");
  await assert.rejects(runs.updateCompletion("reject", completion), /write rejected/);
  await assert.rejects(runs.markFailedIfRunning("reject", reap), /write rejected/);
  await db.exec("DROP TRIGGER reject_run");
  await db.exec("UPDATE flow_runs SET status = 'failed' WHERE flow_id = ?", ["reject"]);
  await db.exec("CREATE TRIGGER reject_node BEFORE UPDATE ON node_executions BEGIN SELECT RAISE(ABORT, 'write rejected'); END");
  await assert.rejects(nodes.reconcileStaleRunning("reject", "failed"), /write rejected/);
  const [node] = await db.query<any>("SELECT id FROM node_executions WHERE flow_id = ?", ["reject"]);
  await assert.rejects(nodes.updateCompletion(node.id, completion), /write rejected/);
  await assert.rejects(nodes.updateCompletionByFlowNode("reject", "node", 1, completion), /write rejected/);
});

test("SQLite migrations add retry_epoch/default0 and state_json and can run again", async t => {
  const db = new SqliteDatabase({ path: ":memory:" });
  await db.connect(); t.after(() => db.close());
  const migrator = new SchemaMigrator(db, "sqlite");
  await migrator.migrate();
  await db.exec("INSERT INTO flow_runs (flow_id, workflow_id, status, started_at) VALUES (?, 'wf', 'running', 1)", ["historical"]);
  await migrator.migrate();
  const [row] = await db.query<any>("SELECT retry_epoch, state_json FROM flow_runs WHERE flow_id = ?", ["historical"]);
  assert.equal(row.retry_epoch, 0); assert.equal(row.state_json, null);
  const columns = await db.query<any>("PRAGMA table_info(flow_runs)");
  const epoch = columns.find(c => c.name === "retry_epoch");
  assert.equal(epoch.type, "INTEGER"); assert.equal(epoch.notnull, 1); assert.equal(epoch.dflt_value, "0");
});


test("SQLite upgrade backfills historical rows without changing existing state storage", async t => {
  const db = new SqliteDatabase({ path: ":memory:" });
  await db.connect(); t.after(() => db.close());
  await db.exec("CREATE TABLE schema_version (version INTEGER, description TEXT)");
  await db.exec("INSERT INTO schema_version (version) VALUES (?)", [35]);
  await db.exec("CREATE TABLE flow_runs (flow_id TEXT, params_json TEXT)");
  const state = '{"_clawmind_state":"persisted legacy state"}';
  await db.exec("INSERT INTO flow_runs (flow_id, params_json) VALUES (?, ?)", ["historical", state]);
  await new SchemaMigrator(db, "sqlite").migrate();
  const [row] = await db.query<any>("SELECT retry_epoch, state_json, params_json FROM flow_runs");
  assert.equal(row.retry_epoch, 0); assert.equal(row.state_json, null);
  assert.equal(row.params_json, state);
});

test('status mirrors cannot reopen any settled epoch; only retry admission advances generation', async t => {
  const { db, runs, seed } = await fixture(t);
  for (const epoch of [0, 3]) {
    const id = `settled-${epoch}`;
    await seed(id, 'succeeded', epoch);
    assert.equal(await runs.updateStatus(id, 'running', epoch), false);
    assert.equal(await runs.updateStatus(id, 'failed', epoch), false);
    assert.equal(await runs.updateCurrentPhase(id, 'stale', epoch), false);
    assert.equal(await runs.resumeForRetry(id, epoch + 1), 'resumed');
    assert.equal(await runs.resumeForRetry(id, epoch + 1), 'resumed');
    assert.equal(await runs.updateCompletion(id, { ...completion, retryEpoch: epoch }), false);
    assert.equal(await runs.markFailedIfRunning(id, { ...reap, retryEpoch: epoch }), false);
    assert.equal(await runs.updateStatus(id, 'waiting', epoch), false);
    assert.equal(await runs.updateStatus(id, 'waiting', epoch + 1), true);
    const [row] = await db.query<any>('SELECT status, retry_epoch FROM flow_runs WHERE flow_id = ?', [id]);
    assert.equal(row.retry_epoch, epoch + 1); assert.equal(row.status, 'waiting');
  }
  await seed('cancelled-retry', 'cancelled', 0);
  assert.equal(await runs.resumeForRetry('cancelled-retry', 1), 'conflict');
  await assert.rejects(runs.resumeForRetry('settled-0', 0), /positive/);
});

test('manual retry cannot reopen normalized cancellation spellings', async t => {
  const { runs, seed } = await fixture(t);
  for (const [index, status] of ['Cancelled', ' CANCELED ', ' cancelled', 'Canceled '].entries()) {
    const id = `cancel-normalized-${index}`; await seed(id, status, 0);
    assert.equal(await runs.resumeForRetry(id, 1), 'conflict');
  }
});

test('completion cannot overwrite alternate or noncanonical terminal statuses', async t => {
  const { runs, seed } = await fixture(t);
  for (const [index, status] of ['canceled', ' Cancelled ', 'SUCCEEDED', ' Failed ', 'COMPLETED'].entries()) {
    const id = `terminal-spelling-${index}`; await seed(id, status, 0);
    assert.equal(await runs.updateCompletion(id, completion), false);
    assert.equal(await runs.markFailedIfRunning(id, reap), false);
  }
});


test('retry admission atomically checkpoints next state in canonical and historical storage', async t => {
  const { runs, db, seed } = await fixture(t);
  for (const kind of ['canonical', 'embedded-string', 'embedded-object']) {
    await seed(kind, 'failed');
    const prior = { workflowId: 'wf', retryEpoch: 0, effectiveFlowTimeoutMinutes: 8200, nodeStates: { task: { status: 'failed' } } };
    const embedded = kind === 'embedded-object' ? prior : JSON.stringify(prior);
    await db.exec('UPDATE flow_runs SET state_json = ?, params_json = ? WHERE flow_id = ?',
      [kind === 'canonical' ? JSON.stringify(prior) : null, JSON.stringify({ input: 7, _clawmind_state: embedded }), kind]);
    const next = { ...prior, retryEpoch: 1, nodeStates: { task: { status: 'pending' } } };
    assert.equal(await runs.resumeForRetry(kind, 1, JSON.stringify(next)), 'resumed');
    const snapshot = await loadRunningSnapshot(runs, kind);
    assert.equal(snapshot?.retryEpoch, 1);
    assert.deepEqual(snapshot?.state, next);
    const [saved] = await db.query<any>('SELECT params_json FROM flow_runs WHERE flow_id = ?', [kind]);
    const params = JSON.parse(saved.params_json); assert.equal(params.input, 7);
    assert.deepEqual(typeof params._clawmind_state === 'string' ? JSON.parse(params._clawmind_state) : params._clawmind_state, next);
    // Lost-response replay must never reset the already running attempt checkpoint.
    await db.exec('UPDATE flow_runs SET state_json = ? WHERE flow_id = ?', [JSON.stringify({ ...next, checkpoint: 2 }), kind]);
    assert.equal(await runs.resumeForRetry(kind, 1, JSON.stringify(next)), 'resumed');
    assert.equal((await loadRunningSnapshot(runs, kind))?.state?.checkpoint, 2);
  }
});

test('retry without supplied state advances a validated historical checkpoint, never mismatched generations', async t => {
  const { runs, db, seed } = await fixture(t);
  await seed('historical-retry', 'failed');
  await db.exec('UPDATE flow_runs SET params_json = ? WHERE flow_id = ?', [JSON.stringify({ _clawmind_state: JSON.stringify({ workflowId: 'wf', effectiveFlowTimeoutMinutes: 0 }) }), 'historical-retry']);
  assert.equal(await runs.resumeForRetry('historical-retry', 1), 'resumed');
  assert.equal((await loadRunningSnapshot(runs, 'historical-retry'))?.state?.retryEpoch, 1);
  await seed('mismatch-state', 'failed');
  await db.exec('UPDATE flow_runs SET state_json = ? WHERE flow_id = ?', ['{"workflowId":"wf","retryEpoch":3}', 'mismatch-state']);
  await assert.rejects(runs.resumeForRetry('mismatch-state', 1), /attempt|epoch/i);
  assert.equal((await runs.findByFlowId('mismatch-state'))?.retry_epoch, 0);
});

test('nonterminal queue mirrors preserve null completion time and allow same-epoch resumption', async t => {
  const { runs, db, seed } = await fixture(t);
  await seed('queue', 'running', 7);
  assert.equal(await runs.updateStatus('queue', 'waiting', 7), true);
  assert.equal(await runs.updateCurrentPhase('queue', 'queued', 7), true);
  const [queued] = await db.query<any>('SELECT completed_at FROM flow_runs WHERE flow_id = ?', ['queue']);
  assert.equal(queued.completed_at, null);
  assert.equal(await runs.updateStatus('queue', 'running', 7), true);
  assert.equal(await runs.updateStatus('queue', 'running', 6), false);
});
