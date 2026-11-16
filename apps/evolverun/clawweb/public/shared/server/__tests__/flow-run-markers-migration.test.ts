import { migrations as currentMigrations } from '../schema.js';
import Database from 'better-sqlite3';
import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import { SqliteDatabase, runMigrations, type IDatabase } from '../db.js';
import { dialectFor } from '../db/dialect.js';
import { migrations } from '../schema.js';
import { flowRunMarkersDdl, flowRunMarkersMigrations } from '../schema-flow-run-markers.js';
import { workflowSnapshotMigrations } from '../schema-workflow-snapshot.js';
import { ordinaryRunRequestMigrations } from '../schema-ordinary-run-request.js';

const latestSchemaVersion = Math.max(...currentMigrations.map(m => m.version));

const migration = flowRunMarkersMigrations[0];
const upgrade = (db: IDatabase) => migration.migrate!(db);

it('appends a unique version beyond every assembled historical migration, including v151', () => {
  const all = [...migrations, ...workflowSnapshotMigrations, ...ordinaryRunRequestMigrations];
  expect(all.filter(m => m.version === migration.version)).toEqual([migration]);
  expect(migration.version).toBe(152);
  expect(Math.max(...all.filter(m => m.version < migration.version).map(m => m.version))).toBe(151);
  expect(migrations.filter(m => m.version <= migration.version).at(-1)).toBe(migration);
  expect(migration.selfHealing).toBeUndefined();
});

it('upgrades history, keeps nullable defaults and survives duplicate-column replay', async () => {
  const raw = new Database(':memory:'); const db = new SqliteDatabase(raw);
  try {
    raw.exec("CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, state_json TEXT); INSERT INTO flow_runs VALUES ('old', '{\"legacy\":true}')");
    await upgrade(db);
    expect(raw.prepare('SELECT * FROM flow_runs').get()).toEqual({ flow_id: 'old', state_json: '{"legacy":true}', env: 'prod', tag: '' });
    raw.exec("INSERT INTO flow_runs (flow_id) VALUES ('default'); INSERT INTO flow_runs (flow_id,env,tag) VALUES ('null',NULL,NULL),('custom','test','release-1')");
    await upgrade(db);
    expect(raw.prepare('SELECT env,tag FROM flow_runs WHERE flow_id = ?').get('default')).toEqual({ env: 'prod', tag: '' });
    expect(raw.prepare('SELECT env,tag FROM flow_runs WHERE flow_id = ?').get('null')).toEqual({ env: null, tag: null });
    expect(raw.prepare('SELECT env,tag FROM flow_runs WHERE flow_id = ?').get('custom')).toEqual({ env: 'test', tag: 'release-1' });
    expect(raw.prepare('PRAGMA table_info(flow_runs)').all()).toEqual(expect.arrayContaining([
      expect.objectContaining({ name: 'env', type: 'VARCHAR(32)', notnull: 0, dflt_value: "'prod'" }),
      expect.objectContaining({ name: 'tag', type: 'VARCHAR(255)', notnull: 0, dflt_value: "''" }),
    ]));
    expect(raw.prepare('PRAGMA index_list(flow_runs)').all()).toHaveLength(1); // Only the existing primary key.
  } finally { await db.close(); }
});

it.each(['env', 'tag', 'both'])('accepts shared ClawMind columns or a partially applied migration: %s', async existing => {
  const raw = new Database(':memory:'); const db = new SqliteDatabase(raw);
  try {
    raw.exec('CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY)');
    if (existing !== 'tag') raw.exec("ALTER TABLE flow_runs ADD COLUMN env VARCHAR(32) NULL DEFAULT 'prod'");
    if (existing !== 'env') raw.exec("ALTER TABLE flow_runs ADD COLUMN tag VARCHAR(255) NULL DEFAULT ''");
    raw.exec("INSERT INTO flow_runs (flow_id) VALUES ('shared')");
    if (existing !== 'tag') raw.exec("UPDATE flow_runs SET env=NULL");
    if (existing !== 'env') raw.exec("UPDATE flow_runs SET tag='preserve'");
    await upgrade(db); await upgrade(db);
    expect(raw.prepare('SELECT env,tag FROM flow_runs').get()).toEqual({ env: existing === 'tag' ? 'prod' : null, tag: existing === 'env' ? '' : 'preserve' });
  } finally { await db.close(); }
});

describe.each(['mysql', 'zdas'] as const)('%s DDL compatibility', dbType => {
  it('executes nullable VARCHAR defaults without narrowing tag to 190 or adding an index', async () => {
    const exec = vi.fn().mockResolvedValue({ affectedRows: 0 });
    await upgrade({ dbType, dialect: dialectFor(dbType), exec } as unknown as IDatabase);
    const ddl = exec.mock.calls.map(([sql]) => String(sql));
    expect(ddl).toEqual(flowRunMarkersDdl);
    expect(ddl[0]).toMatch(/ADD COLUMN env VARCHAR\(32\) NULL DEFAULT 'prod'/);
    expect(ddl[1]).toMatch(/ADD COLUMN tag VARCHAR\(255\) NULL DEFAULT ''/);
    expect(ddl.join('\n')).not.toMatch(/NOT NULL|CREATE INDEX|UPDATE flow_runs/i);
  });
  it('only ignores duplicate columns, not permission/connection/schema errors', async () => {
    const exec = vi.fn().mockRejectedValue(new Error("Duplicate column name 'env'"));
    const db = { dbType, dialect: dialectFor(dbType), exec } as unknown as IDatabase;
    await expect(upgrade(db)).resolves.toBeUndefined();
    expect(exec).toHaveBeenCalledTimes(2);
    for (const message of ['ALTER command denied', 'Connection lost', 'no such column: unrelated', 'Unknown column other', 'Table flow_runs does not exist']) {
      exec.mockRejectedValue(new Error(message));
      await expect(upgrade(db)).rejects.toThrow(message);
    }
  });
});

it('runs through the real migration ledger and does not repeat a completed upgrade', async () => {
  const raw = new Database(':memory:'); const db = new SqliteDatabase(raw);
  try {
    await runMigrations(db, 'sqlite');
    expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: latestSchemaVersion });
    raw.exec("INSERT INTO flow_runs (flow_id,workflow_id,status,started_at,env,tag) VALUES ('nullable','workflow','running',1,NULL,NULL)");
    await runMigrations(db, 'sqlite');
    expect(raw.prepare('SELECT COUNT(*) AS n FROM schema_version WHERE version=152').get()).toEqual({ n: 1 });
    expect(raw.prepare('SELECT env,tag FROM flow_runs').get()).toEqual({ env: null, tag: null });
  } finally { await db.close(); }
});

it('propagates a real DDL failure through the runner without recording v152', async () => {
  const raw = new Database(':memory:'); const db = new SqliteDatabase(raw);
  try {
    raw.exec("CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY); CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (151, 'previous')");
    const actual = db.exec.bind(db);
    vi.spyOn(db, 'exec').mockImplementation(async (sql, params) => {
      if (sql.includes('ADD COLUMN tag')) throw new Error('DDL write failed');
      return actual(sql, params);
    });
    await expect(runMigrations(db, 'sqlite')).rejects.toThrow('DDL write failed');
    expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 151 });
  } finally { vi.restoreAllMocks(); await db.close(); }
});

it('keeps the operator SQL definitions identical to the runtime MySQL DDL', () => {
  const script = readFileSync(new URL('../../../../scripts/sql/flow-run-markers-v152.mysql.sql', import.meta.url), 'utf8');
  const statements = [...script.matchAll(/'((?:ALTER TABLE flow_runs ADD COLUMN)(?:[^']|'')*)'/g)]
    .map(([, sql]) => sql.replace(/''/g, "'"));
  expect(statements).toEqual(flowRunMarkersDdl);
  expect(script).not.toMatch(/CREATE INDEX|UPDATE flow_runs|INSERT INTO schema_version/i);
});
