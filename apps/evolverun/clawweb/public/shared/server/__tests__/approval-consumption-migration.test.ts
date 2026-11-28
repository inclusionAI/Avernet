import Database from 'better-sqlite3';
import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SqliteDatabase, runMigrations, allMigrations } from '../db.js';
import { dialectFor } from '../db/dialect.js';
import { collectMigrations } from '../db/migrations-registry.js';
import {
  approvalConsumptionColumnDdl,
  approvalConsumptionIndexDdl,
  approvalConsumptionMigrations,
} from '../schema-approval-consumption.js';

const opened: SqliteDatabase[] = [];
const ledgerInsert = 'INSERT INTO schema_version (version, description) VALUES (?, ?)';
const legacyTableDdl = `CREATE TABLE approval_cards (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  delivery_mode TEXT NOT NULL DEFAULT 'card-web',
  comment TEXT DEFAULT NULL,
  resolved_at INTEGER DEFAULT NULL,
  approved_by TEXT NOT NULL DEFAULT '',
  rejected_by TEXT NOT NULL DEFAULT '',
  gmt_create INTEGER NOT NULL DEFAULT 100,
  gmt_modified INTEGER NOT NULL DEFAULT 200
)`;

async function legacyDb() {
  const db = new SqliteDatabase(new Database(':memory:'));
  opened.push(db);
  await db.exec('CREATE TABLE schema_version (version INTEGER, description TEXT)');
  await db.exec("INSERT INTO schema_version VALUES (158, 'pre-consumption ledger')");
  await db.exec(legacyTableDdl);
  for (const [status, mode, comment, resolved, approved, rejected] of [
    ['pending', 'card-web', null, null, '', ''],
    ['approved', 'card-web', '{"text":"accepted"}', 101, 'approver-a', ''],
    ['rejected', 'card-web', 'needs changes', 102, '', 'approver-b'],
    ['approved', 'card-web-processed', 'legacy delivery marker', 103, 'approver-c', ''],
    ['cancelled', 'card-web', 'cancelled run', 104, '', ''],
    ['expired', 'card-web', null, null, '', ''],
    ['approved', 'other-delivery', 'external approval', 105, 'approver-d', ''],
  ]) {
    await db.exec(`INSERT INTO approval_cards
      (flow_id, status, delivery_mode, comment, resolved_at, approved_by, rejected_by)
      VALUES ('legacy-flow', ?, ?, ?, ?, ?, ?)`, [status, mode, comment, resolved, approved, rejected]);
  }
  return db;
}

async function consumptionLedger(db: SqliteDatabase) {
  return db.query('SELECT version, description FROM schema_version WHERE version = 159');
}

async function indexColumns(db: SqliteDatabase) {
  return (await db.query<{ name: string }>('PRAGMA index_info(idx_approval_cards_polling)'))
    .map(column => column.name);
}

afterEach(async () => {
  vi.restoreAllMocks();
  for (const db of opened.splice(0)) await db.close();
});

describe('approval consumption migration v159', () => {
  it('registers one ordinary migration after v158 without colliding with current dev', () => {
    const registry = collectMigrations();
    expect(approvalConsumptionMigrations).toHaveLength(1);
    expect(approvalConsumptionMigrations[0]).toMatchObject({ version: 159, sql: [] });
    expect(approvalConsumptionMigrations[0].selfHealing).not.toBe(true);
    expect(registry.filter(m => m.version === 159)).toEqual([approvalConsumptionMigrations[0]]);
    // Pre-existing v36/v99/v100 overlaps are historical; only our allocation is new.
    expect(allMigrations.filter(m => m.version === 159)).toEqual([approvalConsumptionMigrations[0]]);
    expect(Math.max(...registry.filter(m => m.version < 159).map(m => m.version))).toBe(158);
    expect(registry.filter(m => m.version >= 150 && m.version <= 158).map(m => m.version))
      .toEqual([150, 151, 152, 153, 154, 155, 156, 157, 158]);
    expect(registry.some(m => m.version === 146 || m.version === 149)).toBe(false);
    expect(registry.filter(m => m.version === 155 || m.version === 156).map(m => m.sql))
      .toEqual([[], []]);
  });

  it('creates a complete fresh SQLite schema and ledger through v159, once', async () => {
    const db = new SqliteDatabase(new Database(':memory:'));
    opened.push(db);
    await runMigrations(db, 'sqlite');
    // Existing SQLite v7 rebuilds the early ledger; do not change that history.
    const expected = allMigrations.filter(m => m.version >= 7 && !m.mysqlOnly
      && (!('supportedDbTypes' in m) || (m.supportedDbTypes as readonly string[]).includes('sqlite')));
    expect(await db.query('SELECT version, description FROM schema_version ORDER BY version'))
      .toEqual(expected.map(m => ({ version: m.version, description: m.description })));
    expect(await indexColumns(db)).toEqual(['delivery_mode', 'processed_at', 'status', 'resolved_at', 'id']);
    const ledger = await db.query('SELECT * FROM schema_version ORDER BY version');
    const schema = await db.query('SELECT type, name, sql FROM sqlite_master ORDER BY type, name');
    await runMigrations(db, 'sqlite');
    expect(await db.query('SELECT * FROM schema_version ORDER BY version')).toEqual(ledger);
    expect(await db.query('SELECT type, name, sql FROM sqlite_master ORDER BY type, name')).toEqual(schema);
  });

  it('keeps the approved external rollout SQL identical to the canonical MySQL DDL', () => {
    const sql = readFileSync(new URL('../../../modules/workflow/docs/approval-consumption-ob-ddl.sql', import.meta.url), 'utf8');
    expect(sql).toContain(approvalConsumptionColumnDdl + ';');
    expect(sql).toContain(approvalConsumptionIndexDdl.mysql + ';');
    expect(sql.indexOf('SHOW COLUMNS')).toBeLessThan(sql.indexOf(approvalConsumptionColumnDdl));
    expect(sql.indexOf('SHOW INDEX')).toBeLessThan(sql.indexOf(approvalConsumptionIndexDdl.mysql));
    expect(sql).not.toMatch(/(?:INSERT|UPDATE|DELETE)\s+(?:INTO\s+)?(?:schema_version|approval_cards)/i);
  });

  it('exports exact nullable unix-second DDL and renders the SQLite polling index', () => {
    expect(approvalConsumptionColumnDdl).toBe("ALTER TABLE approval_cards ADD COLUMN processed_at BIGINT DEFAULT NULL COMMENT 'Workflow consumption time in unix seconds; NULL means unconsumed or unknown'");
    expect(dialectFor('sqlite').renderDdl(approvalConsumptionColumnDdl).trim())
      .toBe('ALTER TABLE approval_cards ADD COLUMN processed_at INTEGER DEFAULT NULL');
    expect(approvalConsumptionIndexDdl.sqlite).toBe('CREATE INDEX IF NOT EXISTS idx_approval_cards_polling ON approval_cards (delivery_mode, processed_at, status, resolved_at, id)');
    expect(dialectFor('sqlite').renderDdl(approvalConsumptionIndexDdl.sqlite))
      .toBe(approvalConsumptionIndexDdl.sqlite);
  });

  it.each(['mysql', 'zdas'] as const)('renders BIGINT and a real ALTER-based index for %s', dbType => {
    const dialect = dialectFor(dbType);
    expect(dialect.renderDdl(approvalConsumptionColumnDdl)).toBe(approvalConsumptionColumnDdl);
    expect(approvalConsumptionIndexDdl.mysql).toBe('ALTER TABLE approval_cards ADD INDEX idx_approval_cards_polling (delivery_mode, processed_at, status, resolved_at, id)');
    expect(dialect.renderDdl(approvalConsumptionIndexDdl.mysql)).toBe(approvalConsumptionIndexDdl.mysql);
  });

  it('migrates legacy rows without rewriting decision, delivery, comments, resolution, or audit history', async () => {
    const db = await legacyDb();
    const before = await db.query<Record<string, unknown>>('SELECT * FROM approval_cards ORDER BY id');
    const ledger = await db.query('SELECT * FROM schema_version WHERE version <= 158');

    await runMigrations(db, 'sqlite');

    expect(await db.query('SELECT * FROM approval_cards ORDER BY id'))
      .toEqual(before.map(row => ({ ...row, processed_at: null })));
    const columns = await db.query<{ name: string; type: string; notnull: number; dflt_value: string }>('PRAGMA table_info(approval_cards)');
    expect(columns.find(column => column.name === 'processed_at'))
      .toMatchObject({ type: 'INTEGER', notnull: 0, dflt_value: 'NULL' });
    expect(await indexColumns(db)).toEqual(['delivery_mode', 'processed_at', 'status', 'resolved_at', 'id']);
    expect(await consumptionLedger(db)).toEqual([{ version: 159, description: approvalConsumptionMigrations[0].description }]);
    expect(await db.query('SELECT * FROM schema_version WHERE version <= 158')).toEqual(ledger);
  });

  it('defaults newly inserted cards to NULL and accepts unix seconds beyond the 32-bit range', async () => {
    const db = await legacyDb();
    await runMigrations(db, 'sqlite');
    await db.exec("INSERT INTO approval_cards (flow_id) VALUES ('new-card')");
    expect(await db.query("SELECT status, delivery_mode, processed_at FROM approval_cards WHERE flow_id = 'new-card'"))
      .toEqual([{ status: 'pending', delivery_mode: 'card-web', processed_at: null }]);
    await db.exec('UPDATE approval_cards SET processed_at = ? WHERE id = 2', [4_294_967_296]);
    expect(await db.query('SELECT processed_at FROM approval_cards WHERE id = 2'))
      .toEqual([{ processed_at: 4_294_967_296 }]);
  });

  it.each(['column only', 'column and index'])('is idempotent after external rollout of $0 and is not replayed once recorded', async provisioned => {
    const db = await legacyDb();
    await db.exec(db.dialect.renderDdl(approvalConsumptionColumnDdl));
    if (provisioned === 'column and index') await db.exec(approvalConsumptionIndexDdl.sqlite);
    await db.exec('UPDATE approval_cards SET processed_at = 123 WHERE id = 2');
    const before = await db.query<Record<string, unknown>>('SELECT * FROM approval_cards ORDER BY id');

    await runMigrations(db, 'sqlite');
    const exec = vi.spyOn(db, 'exec');
    await runMigrations(db, 'sqlite');

    expect(exec.mock.calls.some(([sql]) => /^(?:ALTER TABLE approval_cards|CREATE INDEX.*idx_approval_cards_polling)/i.test(sql))).toBe(false);
    expect(exec).not.toHaveBeenCalledWith(ledgerInsert, [159, expect.any(String)]);
    expect(await db.query('SELECT * FROM approval_cards ORDER BY id')).toEqual(before);
    expect(await indexColumns(db)).toEqual(['delivery_mode', 'processed_at', 'status', 'resolved_at', 'id']);
    expect(await consumptionLedger(db)).toHaveLength(1);
  });

  it.each([
    { step: 'column', error: 'ALTER command denied' },
    { step: 'index', error: 'disk full' },
    { step: 'index', error: 'no such column: status' },
    { step: 'ledger', error: 'ledger write failed' },
  ])('does not record success when the $step fails with $error, and permits retry', async ({ step, error }) => {
    const db = await legacyDb();
    const failure = new Error(error);
    const originalExec = db.exec.bind(db);
    const exec = vi.spyOn(db, 'exec');
    exec.mockImplementation(async (sql, params) => {
      if ((step === 'column' && sql.startsWith('ALTER TABLE approval_cards')) ||
          (step === 'index' && sql.startsWith('CREATE INDEX IF NOT EXISTS idx_approval_cards_polling')) ||
          (step === 'ledger' && sql === ledgerInsert)) {
        throw failure;
      }
      return originalExec(sql, params);
    });

    await expect(runMigrations(db, 'sqlite')).rejects.toBe(failure);
    if (step !== 'ledger') expect(exec).not.toHaveBeenCalledWith(ledgerInsert, [159, expect.any(String)]);
    expect(await consumptionLedger(db)).toEqual([]);

    exec.mockRestore();
    await runMigrations(db, 'sqlite');
    expect(await consumptionLedger(db)).toHaveLength(1);
    expect(await indexColumns(db)).toEqual(['delivery_mode', 'processed_at', 'status', 'resolved_at', 'id']);
    expect(await db.query('SELECT processed_at FROM approval_cards'))
      .toEqual(Array.from({ length: 7 }, () => ({ processed_at: null })));
  });

  it('does not mark an absent approval_cards table as migrated', async () => {
    const db = await legacyDb();
    await db.exec('DROP TABLE approval_cards');
    await expect(runMigrations(db, 'sqlite')).rejects.toThrow('no such table: approval_cards');
    expect(await consumptionLedger(db)).toEqual([]);
  });
});
