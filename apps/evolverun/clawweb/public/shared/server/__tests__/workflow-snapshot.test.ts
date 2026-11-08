import { dialectFor } from '../db/dialect.js';
import { repairReleaseMigrations } from '../schema-repair-release.js';
import { workflowSnapshotColumnDdl } from '../schema-workflow-snapshot.js';
import Database from 'better-sqlite3';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SqliteDatabase, runMigrations } from '../db.js';
import { WorkflowSnapshotRepository } from '../services/workflow-snapshot.js';
import { workflowSnapshotKey } from '../contracts/workflow-snapshot.js';

const opened: SqliteDatabase[] = [];
afterEach(async () => { for (const db of opened.splice(0)) await db.close(); });
async function setup() {
  const db = new SqliteDatabase(new Database(':memory:')); opened.push(db);
  await db.exec('CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, workflow_id TEXT, workflow_version INTEGER, workflow_deploy_number INTEGER, state_json TEXT, workflow_snapshot_json TEXT)');
  await db.exec('CREATE TABLE workflow_deploy_history (workflow_id TEXT, version INTEGER, deploy_number INTEGER, spec_json TEXT, is_active INTEGER)');
  await db.exec(`INSERT INTO workflow_deploy_history VALUES ('wf',39,34,?,1)`, [JSON.stringify({ id: 'wf', nodes: [{ id: 'fetch', timeoutMs: 20 }] })]);
  await db.exec("INSERT INTO flow_runs VALUES ('deployed','wf',39,34,NULL,NULL)");
  return { db, snapshots: new WorkflowSnapshotRepository(db) };
}
describe('run workflow snapshots', () => {
  it.each(['mysql','zdas'] as const)('keeps additive nullable association DDL valid for %s', dialect => {
    for (const ddl of workflowSnapshotColumnDdl) {
      const rendered = dialectFor(dialect).renderDdl(ddl);
      expect(rendered).toMatch(/ADD COLUMN workflow_snapshot_json TEXT DEFAULT NULL/);
      expect(rendered).not.toMatch(/NOT NULL|DROP |RENAME /);
    }
  });

  it('resolves a run page with bounded queries rather than one deployment lookup per run', async () => {
    const { db, snapshots } = await setup();
    const ids = Array.from({ length: 120 }, (_, i) => `run-${i}`);
    for (const id of ids) await db.exec("INSERT INTO flow_runs VALUES (?,'wf',39,34,NULL,NULL)", [id]);
    const query = vi.spyOn(db, 'query');
    const bindings = await snapshots.forRuns(ids);
    expect(bindings.size).toBe(120);
    expect([...bindings.values()].every(value => value.status === 'resolved')).toBe(true);
    expect(query.mock.calls.length).toBeLessThanOrEqual(4);
  });

  it('persists an immutable association at registration and propagates association write failures', async () => {
    const {db,snapshots}=await setup();
    await snapshots.associateRun('deployed');
    const row=(await db.query<{workflow_snapshot_json:string}>("SELECT workflow_snapshot_json FROM flow_runs WHERE flow_id='deployed'"))[0];
    expect(JSON.parse(row.workflow_snapshot_json)).toMatchObject({status:'resolved',snapshot:{workflowVersion:39,deployNumber:34}});
    const exec=vi.spyOn(db,'exec'); exec.mockRejectedValueOnce(new Error('write denied'));
    await expect(snapshots.associateRun('deployed')).rejects.toThrow('write denied');
  });
  it('uses actual deployment 39/34 even when another deployment becomes active', async () => {
    const { db, snapshots } = await setup();
    const binding = await snapshots.forRun('deployed');
    expect(binding).toMatchObject({ status: 'resolved', snapshot: { kind: 'deployment', workflowVersion: 39, deployNumber: 34 } });
    await db.exec('UPDATE workflow_deploy_history SET is_active=0');
    await db.exec("INSERT INTO workflow_deploy_history VALUES ('wf',40,35,?,1)", [JSON.stringify({ id: 'wf', nodes: [] })]);
    expect(await snapshots.forRun('deployed')).toEqual(binding);
    expect(await snapshots.read(binding.snapshot!)).toMatchObject({ workflowSpec: { nodes: [{ timeoutMs: 20 }] } });
    expect(await snapshots.active('wf')).toMatchObject({ status: 'resolved', snapshot: { workflowVersion: 40, deployNumber: 35 } });
  });
  it('separates remote commits sharing version -1 and reads their captured configurations', async () => {
    const { db, snapshots } = await setup();
    for (const [id, commit, timeoutMs] of [['one', 'a'.repeat(40), 10], ['two', 'b'.repeat(40), 30]] as const) {
      const state = { workflowPin: { workflowId: 'wf', workflowDigest: `remote-snapshot:${commit}`, source: 'remote-snapshot' }, workflowSnapshot: { id: 'wf', nodes: [{ id: 'fetch', timeoutMs }] } };
      await db.exec("INSERT INTO flow_runs VALUES (?,'wf',-1,NULL,?,NULL)", [id, JSON.stringify(state)]);
    }
    const one = await snapshots.forRun('one'); const two = await snapshots.forRun('two');
    expect(one.status).toBe('resolved'); expect(two.status).toBe('resolved');
    expect(workflowSnapshotKey(one.snapshot!)).not.toBe(workflowSnapshotKey(two.snapshot!));
    expect(await snapshots.read(one.snapshot!)).toMatchObject({ workflowSpec: { nodes: [{ timeoutMs: 10 }] } });
  });
  it('does not invent a source for a mismatched version/deployment pair', async () => {
    const { db, snapshots } = await setup();
    await db.exec("UPDATE flow_runs SET workflow_version=34 WHERE flow_id='deployed'");
    expect(await snapshots.forRun('deployed')).toEqual({ status: 'unresolved', reason: 'IDENTITY_MISMATCH' });
    expect(await snapshots.active('not-deployed')).toBeNull();
  });
  it('does not replace a missing immutable snapshot with another deployment', async () => {
    const { db, snapshots } = await setup();
    const binding = await snapshots.forRun('deployed');
    await db.exec('DELETE FROM workflow_deploy_history');
    await expect(snapshots.read(binding.snapshot!)).rejects.toThrow('WORKFLOW_SNAPSHOT_UNAVAILABLE');
  });
  it('detects changed content under an existing identity', async () => {
    const { db, snapshots } = await setup();
    const binding = await snapshots.forRun('deployed');
    await db.exec("UPDATE workflow_deploy_history SET spec_json='{}'");
    await expect(snapshots.read(binding.snapshot!)).rejects.toThrow('IDENTITY_MISMATCH');
  });
});
it('adds associations with real migrations and preserves old rows on repeated migration', async () => {
  const db = new SqliteDatabase(new Database(':memory:')); opened.push(db);
  // A v144 fixture includes the release journal created by v142.
  for (const migration of repairReleaseMigrations) for (const sql of migration.sql) await db.exec(db.dialect.renderDdl(sql));
  await db.exec('CREATE TABLE schema_version (version INTEGER, description TEXT)');
  await db.exec('INSERT INTO schema_version VALUES (144, \'legacy\')');
  await db.exec('CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, workflow_id TEXT, status TEXT, started_at INTEGER)');
  await db.exec('CREATE TABLE workflow_evolution_analysis_runs (analysis_id TEXT PRIMARY KEY)');
  await runMigrations(db, 'sqlite');
  await db.exec("INSERT INTO flow_runs (flow_id,workflow_id,status,started_at) VALUES ('legacy','wf','completed',1)");
  await runMigrations(db, 'sqlite');
  expect(await db.query("SELECT flow_id,workflow_snapshot_json FROM flow_runs WHERE flow_id='legacy'")).toEqual([{ flow_id: 'legacy', workflow_snapshot_json: null }]);
  const cols = await db.query<{name:string}>('PRAGMA table_info(workflow_evolution_analysis_runs)');
  expect(cols.some(c => c.name === 'workflow_snapshot_json')).toBe(true);
});

it('does not require an analysis table to boot existing workflow deployments', async () => {
  const db = new SqliteDatabase(new Database(':memory:')); opened.push(db);
  // A v144 fixture includes the release journal created by v142.
  for (const migration of repairReleaseMigrations) for (const sql of migration.sql) await db.exec(db.dialect.renderDdl(sql));
  await db.exec('CREATE TABLE schema_version (version INTEGER, description TEXT)');
  await db.exec("INSERT INTO schema_version VALUES (144,'legacy')");
  await db.exec('CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY)');
  await expect(runMigrations(db,'sqlite')).resolves.toBeUndefined();
});
