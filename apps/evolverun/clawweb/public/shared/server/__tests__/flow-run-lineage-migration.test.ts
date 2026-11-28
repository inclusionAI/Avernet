import { seedLegacyApprovalCards } from './legacy-approval-cards-fixture.js';
import Database from 'better-sqlite3';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { SqliteDatabase, runMigrations, allMigrations, type IDatabase } from '../db.js';
import { dialectFor } from '../db/dialect.js';
import { collectMigrations } from '../db/migrations-registry.js';
import { migrations, sqliteTriggers } from '../schema.js';
import { workflowSnapshotMigrations } from '../schema-workflow-snapshot.js';
import { ordinaryRunRequestDdl, ordinaryRunRequestMigrations } from '../schema-ordinary-run-request.js';
import { aistudioTrainMigrations } from '../schema-aistudio-train.js';
import { assertFlowRunLineageSchema, flowRunLineageDdl, flowRunLineageMigrations } from '../schema-flow-run-lineage.js';

const migration = flowRunLineageMigrations[0];
const upgrade = (db: IDatabase) => migration.migrate!(db);
const names = ['parent_flow_id', 'root_flow_id'];
const ledgerInsert = 'INSERT INTO schema_version (version, description) VALUES (?, ?)';

async function withSqlite(test: (raw: Database.Database, db: SqliteDatabase) => Promise<void>) {
  const dir = mkdtempSync(join(tmpdir(), 'clawweb-lineage-'));
  const raw = new Database(join(dir, 'test.sqlite'));
  const db = new SqliteDatabase(raw);
  try { await test(raw, db); }
  finally { vi.restoreAllMocks(); await db.close(); rmSync(dir, { recursive: true, force: true }); }
}

function previousSchema(raw: Database.Database) {
  seedLegacyApprovalCards(raw);
  raw.exec(`CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER, description TEXT,
    gmt_create INTEGER DEFAULT (unixepoch()), gmt_modified INTEGER DEFAULT (unixepoch()));
    INSERT INTO schema_version (version, description) VALUES (156, 'previous');
    CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, flow_id TEXT UNIQUE, state_json TEXT, gmt_modified INTEGER DEFAULT 0);`);
}

it('registers v157 exactly once after snapshots, ordinary migrations and retired v155/v156', () => {
  const assembled = [...migrations, ...workflowSnapshotMigrations, ...ordinaryRunRequestMigrations, ...aistudioTrainMigrations];
  expect(migration.version).toBe(157);
  // Verify the predecessor, not the latest migration: later registrations must remain valid.
  expect(Math.max(...assembled.filter(m => m.version < migration.version).map(m => m.version))).toBe(156);
  expect(assembled.filter(m => m.version === 157)).toEqual([migration]);
  expect(collectMigrations().filter(m => m.version === 157)).toEqual([migration]);
  expect(allMigrations.filter(m => m.version === 157)).toEqual([migration]);
  expect(allMigrations.findIndex(m => m.version === 157)).toBeGreaterThan(allMigrations.findIndex(m => m.version === 156));
  expect(migration.selfHealing).toBeUndefined();
  expect(aistudioTrainMigrations.map(m => [m.version, m.sql, m.migrate])).toEqual([[155, [], undefined], [156, [], undefined]]);
});

it('boots a fresh temporary SQLite file through the real registry and records lineage only once', async () => {
  await withSqlite(async (raw, db) => {
    await runMigrations(db, 'sqlite');
    raw.exec("INSERT INTO flow_runs (flow_id,workflow_id,status,started_at) VALUES ('fresh','workflow','running',1)");
    expect(raw.prepare('SELECT parent_flow_id,root_flow_id FROM flow_runs').get()).toEqual({ parent_flow_id: null, root_flow_id: null });
    expect(raw.prepare('PRAGMA table_info(flow_runs)').all()).toEqual(expect.arrayContaining(names.map(name =>
      expect.objectContaining({ name, type: 'VARCHAR(255)', notnull: 0, dflt_value: 'NULL' }))));
    const exec = vi.spyOn(db, 'exec');
    await runMigrations(db, 'sqlite');
    expect(exec.mock.calls.some(([sql]) => /ADD COLUMN (parent_flow_id|root_flow_id)/.test(sql))).toBe(false);
    expect(raw.prepare('SELECT COUNT(*) AS n FROM schema_version WHERE version=157').get()).toEqual({ n: 1 });
    expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: Math.max(...allMigrations.map(m => m.version)) });
  });
});

it.each(['none', 'parent_flow_id', 'root_flow_id', 'both'])('preserves rows and independently skips preapplied SQLite columns: %s', async existing => {
  await withSqlite(async (raw, db) => {
    previousSchema(raw);
    for (const name of names) if (existing === name || existing === 'both')
      raw.exec(`ALTER TABLE flow_runs ADD COLUMN ${name} VARCHAR(255) NULL DEFAULT NULL`);
    const state = JSON.stringify({ parent_flow_id: 'not-provenance', root_flow_id: 'do-not-backfill' });
    raw.prepare('INSERT INTO flow_runs (flow_id,state_json) VALUES (?,?)').run('legacy', state);
    raw.exec("INSERT INTO flow_runs (flow_id) VALUES ('null')");
    for (const name of names) if (existing === name || existing === 'both')
      raw.prepare(`UPDATE flow_runs SET ${name}=? WHERE flow_id='legacy'`).run(name[0].repeat(255));
    const indexes = raw.prepare('PRAGMA index_list(flow_runs)').all();
    const exec = vi.spyOn(db, 'exec');
    await runMigrations(db, 'sqlite');
    await runMigrations(db, 'sqlite');
    expect(raw.prepare('SELECT flow_id,state_json,parent_flow_id,root_flow_id FROM flow_runs ORDER BY flow_id').all()).toEqual([
      { flow_id: 'legacy', state_json: state,
        parent_flow_id: existing === 'parent_flow_id' || existing === 'both' ? 'p'.repeat(255) : null,
        root_flow_id: existing === 'root_flow_id' || existing === 'both' ? 'r'.repeat(255) : null },
      { flow_id: 'null', state_json: null, parent_flow_id: null, root_flow_id: null },
    ]);
    expect(exec.mock.calls.filter(([sql]) => /ADD COLUMN (parent_flow_id|root_flow_id)/.test(sql)).map(([sql]) => sql.trim()))
      .toEqual(names.filter(name => existing !== name && existing !== 'both')
        .map(name => dialectFor('sqlite').renderDdl(flowRunLineageDdl[names.indexOf(name)]).trim()));
    expect(raw.prepare('PRAGMA index_list(flow_runs)').all()).toEqual(indexes);
    expect(raw.prepare('SELECT COUNT(*) AS n FROM schema_version WHERE version=157').get()).toEqual({ n: 1 });
  });
});

describe.each([150, 151] as const)('SQLite v%i deferral lineage guard', version => {
  it.each(['ddl-denied', 'ddl-busy', 'ledger-busy'].flatMap(phase => ['absent', 'compatible'].map(lineage => ({ phase, lineage }))))
    ('checks $lineage columns read-only after $phase without running v157', async ({ phase, lineage }) => {
      await withSqlite(async (raw, db) => {
        previousSchema(raw);
        raw.prepare('UPDATE schema_version SET version=?').run(version - 1);
        raw.exec("INSERT INTO flow_runs (flow_id,state_json) VALUES ('preserve','unchanged')");
        if (version === 151) raw.exec(db.dialect.renderDdl(ordinaryRunRequestDdl));
        if (lineage === 'compatible') for (const ddl of flowRunLineageDdl) raw.exec(db.dialect.renderDdl(ddl));
        for (const ddl of sqliteTriggers.filter(sql => /trg_(flow_runs|schema_version)_update\b/.test(sql))) raw.exec(ddl);
        const actual = db.exec.bind(db);
        const failure = Object.assign(new Error('optional schema unavailable'), { code: phase === 'ddl-denied' ? 'SQLITE_AUTH' : 'SQLITE_BUSY' });
        const exec = vi.spyOn(db, 'exec').mockImplementation(async (sql, params) => {
          const blocked = phase === 'ledger-busy' ? sql === ledgerInsert && params?.[0] === version
            : version === 150 ? sql.startsWith('CREATE TABLE IF NOT EXISTS ordinary_run_requests')
              : sql.startsWith('ALTER TABLE flow_runs ADD COLUMN ordinary_run_marker');
          if (blocked) throw failure;
          return actual(sql, params);
        });
        const query = vi.spyOn(db, 'query');
        const deferred = vi.fn();
        if (lineage === 'absent') await expect(runMigrations(db, 'sqlite', deferred)).rejects.toThrow('flow_runs.parent_flow_id is not compatibly provisioned');
        else await expect(runMigrations(db, 'sqlite', deferred)).resolves.toBeUndefined();
        expect(query).toHaveBeenLastCalledWith('PRAGMA table_xinfo(flow_runs)');
        expect(exec.mock.calls.some(([sql]) => /ADD COLUMN (parent_flow_id|root_flow_id)/.test(sql))).toBe(false);
        expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: version - 1 });
        expect(raw.prepare('SELECT flow_id,state_json FROM flow_runs').all()).toEqual([{ flow_id: 'preserve', state_json: 'unchanged' }]);
        expect(deferred).toHaveBeenCalledTimes(phase === 'ddl-denied' ? 0 : 1);
      });
    });
});

it('does not record progress on a real SQLite missing-table failure', async () => {
  await withSqlite(async (raw, db) => {
    previousSchema(raw); raw.exec('DROP TABLE flow_runs');
    await expect(runMigrations(db, 'sqlite')).rejects.toThrow(/no such table.*flow_runs/i);
    expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 156 });
  });
});

it.each(['ddl', 'ledger'])('propagates SQLite %s write failures and resumes a partially applied upgrade', async phase => {
  await withSqlite(async (raw, db) => {
    previousSchema(raw);
    raw.exec("INSERT INTO flow_runs (flow_id,state_json) VALUES ('keep','unchanged')");
    const actual = db.exec.bind(db);
    const failure = Object.assign(new Error('storage write failed'), { code: 'SQLITE_IOERR' });
    const spy = vi.spyOn(db, 'exec').mockImplementation(async (sql, params) => {
      if (phase === 'ddl' ? sql.includes('ADD COLUMN root_flow_id') : sql === ledgerInsert) throw failure;
      return actual(sql, params);
    });
    await expect(runMigrations(db, 'sqlite')).rejects.toBe(failure);
    expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 156 });
    expect(raw.prepare('SELECT flow_id,state_json,parent_flow_id FROM flow_runs').get()).toEqual({ flow_id: 'keep', state_json: 'unchanged', parent_flow_id: null });
    spy.mockRestore();
    raw.exec("UPDATE flow_runs SET parent_flow_id='preserve-after-interruption'");
    await runMigrations(db, 'sqlite');
    expect(raw.prepare('SELECT parent_flow_id,root_flow_id FROM flow_runs').get()).toEqual({ parent_flow_id: 'preserve-after-interruption', root_flow_id: null });
    expect(raw.prepare('SELECT COUNT(*) AS n FROM schema_version WHERE version=157').get()).toEqual({ n: 1 });
  });
});

it.each(['INTEGER', "VARCHAR(255) NOT NULL DEFAULT ''", "VARCHAR(255) DEFAULT 'bad'", 'CHARINT', "TEXT GENERATED ALWAYS AS ('x') VIRTUAL"])
  ('rejects incompatible pre-existing SQLite lineage without changing it: %s', async definition => {
    await withSqlite(async (raw, db) => {
      previousSchema(raw);
      raw.exec(`ALTER TABLE flow_runs ADD COLUMN parent_flow_id ${definition}`);
      const schema = raw.prepare('SELECT sql FROM sqlite_master WHERE name=\'flow_runs\'').get();
      await expect(runMigrations(db, 'sqlite')).rejects.toThrow('flow_runs.parent_flow_id is not compatibly provisioned');
      expect(raw.prepare('SELECT sql FROM sqlite_master WHERE name=\'flow_runs\'').get()).toEqual(schema);
      expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({ v: 156 });
    });
  });

type MysqlColumn = {
  column_name: string; data_type: string; max_length: number | string; is_nullable: string;
  column_default: string | null; extra: string; generation_expression: string | null;
};
const mysqlColumn = (column_name: string): MysqlColumn => ({ column_name, data_type: 'varchar', max_length: 255,
  is_nullable: 'YES', column_default: null, extra: '', generation_expression: null });

function mysqlDb(dbType: 'mysql' | 'zdas', columns: MysqlColumn[] = []) {
  const exec = vi.fn<IDatabase['exec']>(async (sql) => {
    const name = /ADD COLUMN (parent_flow_id|root_flow_id)\b/.exec(sql)?.[1];
    if (name) columns.push(mysqlColumn(name));
    return { affectedRows: 1 };
  });
  const query = vi.fn<IDatabase['query']>(async <T>(sql: string): Promise<T[]> => {
    if (sql.includes("TABLE_NAME = 'flow_runs'")) return columns.map(c => ({ ...c })) as T[];
    if (sql.includes("TABLE_NAME = 'workflow_deploy_history'")) return [];
    if (sql === 'SELECT MAX(version) as v FROM schema_version') return [{ v: 156 }] as T[];
    throw new Error(`Unexpected query: ${sql}`);
  });
  const db: IDatabase = { dbType, dialect: dialectFor(dbType), exec, query, transaction: fn => fn(db), close: async () => {} };
  return { db, exec, query, columns };
}

describe.each(['mysql', 'zdas'] as const)('%s lineage', dbType => {
  it('bypasses generic narrowing and retains nullable unindexed VARCHAR(255)', async () => {
    const { db, exec } = mysqlDb(dbType);
    expect(db.dialect.renderDdl(flowRunLineageDdl[0])).toContain('VARCHAR(190)');
    await upgrade(db);
    expect(exec.mock.calls.map(([sql]) => sql)).toEqual(flowRunLineageDdl);
    expect(flowRunLineageDdl.join('\n')).not.toMatch(/INDEX|UPDATE|NOT NULL/i);
  });

  it.each(['parent_flow_id', 'root_flow_id', 'both'])('skips compatible preprovisioned columns independently: %s', async existing => {
    const { db, exec } = mysqlDb(dbType, names.filter(name => existing === name || existing === 'both').map(mysqlColumn));
    await upgrade(db);
    expect(exec.mock.calls.map(([sql]) => sql)).toEqual(flowRunLineageDdl.filter((_, i) => existing !== names[i] && existing !== 'both'));
  });

  it.each(['message', 'code', 'errno'])('accepts only verified concurrent duplicate-column %s errors', async kind => {
    const { db, exec, columns } = mysqlDb(dbType);
    exec.mockImplementation(async sql => {
      const name = /ADD COLUMN (\w+)/.exec(sql)![1]; columns.push(mysqlColumn(name));
      if (kind === 'message') throw new Error(`Duplicate column name '${name}'`);
      throw Object.assign(new Error('duplicate field'), kind === 'code' ? { code: 'ER_DUP_FIELDNAME' } : { errno: 1060 });
    });
    await expect(upgrade(db)).resolves.toBeUndefined();
    expect(exec).toHaveBeenCalledTimes(2);
  });

  it('rejects an unverified duplicate rather than claiming the missing column exists', async () => {
    const { db, exec } = mysqlDb(dbType);
    const failure = Object.assign(new Error('duplicate field'), { errno: 1060 });
    exec.mockRejectedValue(failure);
    await expect(upgrade(db)).rejects.toBe(failure);
  });

  it.each(['ALTER command denied', 'Connection lost', 'no such column: unrelated', 'already exists', 'syntax error', 'Table flow_runs does not exist'])
    ('propagates non-duplicate DDL failures without ledger progress: %s', async message => {
      const { db, exec } = mysqlDb(dbType);
      const failure = new Error(message);
      const actual = exec.getMockImplementation()!;
      exec.mockImplementation(async (sql, params) => {
        if (sql.includes('ADD COLUMN root_flow_id')) throw failure;
        return actual(sql, params);
      });
      await expect(runMigrations(db, dbType)).rejects.toBe(failure);
      expect(exec).not.toHaveBeenCalledWith(ledgerInsert, [157, expect.any(String)]);
      expect(exec).toHaveBeenCalledWith(flowRunLineageDdl[0]);
    });

  it('fails before ledger registration when the database silently narrows a new column', async () => {
    const { db, exec, columns } = mysqlDb(dbType);
    exec.mockImplementation(async sql => {
      const name = /ADD COLUMN (\w+)/.exec(sql)?.[1];
      if (name) columns.push({ ...mysqlColumn(name), max_length: 190 });
      return { affectedRows: 1 };
    });
    await expect(runMigrations(db, dbType)).rejects.toThrow('flow_runs.parent_flow_id is not compatibly provisioned');
    expect(exec).not.toHaveBeenCalledWith(ledgerInsert, [157, expect.any(String)]);
  });

  it.each(['compatible', 'missing', 'narrow', 'numeric', 'required', 'default', 'generated'])
    ('read-only managed startup checks %s columns without replaying ordinary migrations', async defect => {
      const cols = names.map(mysqlColumn);
      if (defect === 'missing') cols.pop();
      if (defect === 'narrow') cols[0].max_length = '190';
      if (defect === 'numeric') cols[0].data_type = 'bigint';
      if (defect === 'required') cols[0].is_nullable = 'NO';
      if (defect === 'default') cols[0].column_default = '';
      if (defect === 'generated') { cols[0].extra = 'VIRTUAL GENERATED'; cols[0].generation_expression = "'fixed'"; }
      const { db, exec, query } = mysqlDb(dbType, cols);
      exec.mockRejectedValue(new Error('CREATE command denied'));
      if (defect === 'compatible') await expect(runMigrations(db, dbType)).resolves.toBeUndefined();
      else await expect(runMigrations(db, dbType)).rejects.toThrow('not compatibly provisioned');
      expect(exec).toHaveBeenCalledExactlyOnceWith(expect.stringMatching(/^CREATE TABLE IF NOT EXISTS schema_version/));
      expect(query.mock.calls.every(([sql]) => !/schema_version|ordinary_run_requests/.test(sql))).toBe(true);
      expect(query).toHaveBeenCalledWith(expect.stringContaining("TABLE_NAME = 'flow_runs'"));
    });

  it('propagates metadata read errors unchanged and performs no writes in the guard', async () => {
    const { db, exec, query } = mysqlDb(dbType);
    const failure = new Error('metadata storage failure'); query.mockRejectedValue(failure);
    await expect(assertFlowRunLineageSchema(db)).rejects.toBe(failure);
    expect(exec).not.toHaveBeenCalled();
  });
});

it('keeps operator SQL identical to runtime DDL and documents safe already-executed handling', () => {
  const script = readFileSync(new URL('../../../../scripts/sql/flow-run-lineage-v157.mysql.sql', import.meta.url), 'utf8');
  const statements = [...script.matchAll(/'((?:ALTER TABLE flow_runs ADD COLUMN)(?:[^']|'')*)'/g)]
    .map(([, sql]) => sql.replace(/''/g, "'"));
  expect(statements).toEqual(flowRunLineageDdl);
  expect(script).toMatch(/Already executed the DDL\?/);
  expect(script).toMatch(/Do NOT set schema_version blindly/);
  expect(script).not.toMatch(/CREATE INDEX|UPDATE flow_runs|INSERT INTO schema_version|JSON_EXTRACT/i);
});
