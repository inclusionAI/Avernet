import Database from 'better-sqlite3';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SqliteDatabase, runMigrations } from '../db.js';
import * as registry from '../db/migrations-registry.js';
import { migrations } from '../schema.js';
import { approvalConsumptionColumnDdl, approvalConsumptionMigrations } from '../schema-approval-consumption.js';
import { callbackCompensationDdl, callbackCompensationMigrations } from '../schema-callback-compensation.js';

const opened: SqliteDatabase[] = [];
const ledgerInsert = 'INSERT INTO schema_version (version, description) VALUES (?, ?)';
const callbackTables = [
  'workflow_callback_compensation_previews',
  'workflow_callback_compensation_batches',
  'workflow_callback_compensation_items',
];

async function databaseThrough(version: 158 | 159) {
  const db = new SqliteDatabase(new Database(':memory:'));
  opened.push(db);
  // Reproduce the upstream registry before compensation existed, with its real migrations.
  const upstream = registry.collectMigrations().filter(m => m.version <= version
    && m !== callbackCompensationMigrations[0]);
  const snapshot = vi.spyOn(registry, 'collectMigrations').mockReturnValue(upstream);
  try { await runMigrations(db, 'sqlite'); }
  finally { snapshot.mockRestore(); }
  expect(await db.query('SELECT MAX(version) AS version FROM schema_version')).toEqual([{ version }]);
  return db;
}

async function latestLedger(db: SqliteDatabase) {
  return db.query('SELECT version, description FROM schema_version WHERE version >= 159 ORDER BY version');
}

beforeEach(() => { vi.spyOn(console, 'log').mockImplementation(() => {}); });
afterEach(async () => {
  vi.restoreAllMocks();
  for (const db of opened.splice(0)) await db.close();
});

describe('callback compensation migration v160 after deployed approval v159', () => {
  it('keeps both ordinary registrations unique and in order', () => {
    expect(registry.collectMigrations().filter(m => m.version >= 158)).toEqual([
      expect.objectContaining({ version: 158 }),
      approvalConsumptionMigrations[0],
      callbackCompensationMigrations[0],
    ]);
    expect(approvalConsumptionMigrations[0].version).toBe(159);
    expect(callbackCompensationMigrations[0].version).toBe(160);
    expect(callbackCompensationMigrations[0].selfHealing).not.toBe(true);
  });

  it.each([159, 160])('rejects a future collision with reserved version %i', version => {
    const registrations = migrations as Array<(typeof migrations)[number]>;
    registrations.push({ version, description: 'Conflicting registration probe', sql: [] });
    try { expect(() => registry.collectMigrations()).toThrow(/Conflicting .* migration version/); }
    finally { registrations.pop(); }
  });

  it('upgrades an existing v159 ledger to v160 without replaying approval or changing its schema/data', async () => {
    const db = await databaseThrough(159);
    await db.exec(`INSERT INTO approval_cards
      (flow_id, node_id, workflow_id, approver_ids, created_at, status, processed_at)
      VALUES ('preserved-run', 'approval-node', 'workflow-a', '[]', 100, 'approved', 123)`);
    const approvalRows = await db.query('SELECT * FROM approval_cards ORDER BY id');
    const approvalSchema = await db.query("SELECT * FROM sqlite_master WHERE tbl_name = 'approval_cards' ORDER BY name");
    const oldLedger = await db.query('SELECT * FROM schema_version WHERE version <= 159 ORDER BY id');
    expect(await db.query("SELECT name FROM sqlite_master WHERE name LIKE 'workflow_callback_compensation_%'")).toEqual([]);
    const exec = vi.spyOn(db, 'exec');

    await runMigrations(db, 'sqlite');

    expect(exec).not.toHaveBeenCalledWith(db.dialect.renderDdl(approvalConsumptionColumnDdl));
    expect(exec).not.toHaveBeenCalledWith(ledgerInsert, [159, expect.any(String)]);
    for (const ddl of callbackCompensationDdl) {
      expect(exec.mock.calls.filter(([sql]) => sql === db.dialect.renderDdl(ddl))).toHaveLength(1);
    }
    expect(await latestLedger(db)).toEqual([
      { version: 159, description: approvalConsumptionMigrations[0].description },
      { version: 160, description: callbackCompensationMigrations[0].description },
    ]);
    expect(await db.query('SELECT * FROM schema_version WHERE version <= 159 ORDER BY id')).toEqual(oldLedger);
    expect(await db.query('SELECT * FROM approval_cards ORDER BY id')).toEqual(approvalRows);
    expect(await db.query("SELECT * FROM sqlite_master WHERE tbl_name = 'approval_cards' ORDER BY name")).toEqual(approvalSchema);
    for (const table of callbackTables) expect(await db.query(`SELECT * FROM ${table}`)).toEqual([]);

    const ledger = await db.query('SELECT * FROM schema_version ORDER BY id');
    const schema = await db.query('SELECT * FROM sqlite_master ORDER BY name');
    exec.mockClear();
    await runMigrations(db, 'sqlite');
    expect(exec.mock.calls.filter(([sql]) => /workflow_callback_compensation_|ALTER TABLE approval_cards/.test(sql))).toEqual([]);
    expect(exec.mock.calls.filter(([sql, params]) => sql === ledgerInsert && Number(params?.[0]) >= 159)).toEqual([]);
    expect(await db.query('SELECT * FROM schema_version ORDER BY id')).toEqual(ledger);
    expect(await db.query('SELECT * FROM sqlite_master ORDER BY name')).toEqual(schema);
  });

  it('applies approval v159 and compensation v160 exactly once when upgrading v158', async () => {
    const db = await databaseThrough(158);
    const exec = vi.spyOn(db, 'exec');
    await runMigrations(db, 'sqlite');
    expect(exec.mock.calls.filter(([sql]) => sql === db.dialect.renderDdl(approvalConsumptionColumnDdl))).toHaveLength(1);
    for (const ddl of callbackCompensationDdl) {
      expect(exec.mock.calls.filter(([sql]) => sql === db.dialect.renderDdl(ddl))).toHaveLength(1);
    }
    expect(exec.mock.calls.filter(([sql, params]) => sql === ledgerInsert && Number(params?.[0]) >= 159)
      .map(([, params]) => params)).toEqual([
      [159, approvalConsumptionMigrations[0].description],
      [160, callbackCompensationMigrations[0].description],
    ]);
    await runMigrations(db, 'sqlite');
    expect(await latestLedger(db)).toHaveLength(2);
    expect(exec.mock.calls.filter(([sql]) => sql === db.dialect.renderDdl(approvalConsumptionColumnDdl))).toHaveLength(1);
    for (const ddl of callbackCompensationDdl) {
      expect(exec.mock.calls.filter(([sql]) => sql === db.dialect.renderDdl(ddl))).toHaveLength(1);
    }
  });

  it.each(['table', 'ledger'] as const)('keeps recorded v159 intact when v160 %s fails and permits a safe retry', async step => {
    const db = await databaseThrough(159);
    const oldLedger = await latestLedger(db);
    const originalExec = db.exec.bind(db);
    const failure = new Error('Compensation migration write failed');
    const exec = vi.spyOn(db, 'exec').mockImplementation(async (sql, params) => {
      if ((step === 'table' && sql === db.dialect.renderDdl(callbackCompensationDdl[1]))
        || (step === 'ledger' && sql === ledgerInsert && params?.[0] === 160)) throw failure;
      return originalExec(sql, params);
    });

    await expect(runMigrations(db, 'sqlite')).rejects.toBe(failure);
    expect(await latestLedger(db)).toEqual(oldLedger);
    expect(exec).not.toHaveBeenCalledWith(ledgerInsert, [159, expect.any(String)]);
    if (step === 'table') expect(exec).not.toHaveBeenCalledWith(ledgerInsert, [160, expect.any(String)]);
    exec.mockRestore();

    await runMigrations(db, 'sqlite');
    await runMigrations(db, 'sqlite');
    expect(await latestLedger(db)).toEqual([
      { version: 159, description: approvalConsumptionMigrations[0].description },
      { version: 160, description: callbackCompensationMigrations[0].description },
    ]);
    for (const table of callbackTables) expect(await db.query(`SELECT * FROM ${table}`)).toEqual([]);
  });
});
