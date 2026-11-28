import Database from 'better-sqlite3';
import { readFileSync } from 'node:fs';
import { afterEach, expect, it, vi } from 'vitest';
import { SqliteDatabase, runMigrations, allMigrations } from '../db.js';
import { dialectFor } from '../db/dialect.js';
import { collectMigrations } from '../db/migrations-registry.js';
import { distributedLockDdl, distributedLockMigrations, assertDistributedLockSchema } from '../schema-distributed-lock.js';

const databases: SqliteDatabase[] = [];
afterEach(async () => { vi.restoreAllMocks(); for (const db of databases.splice(0)) await db.close(); });

it('registers the next migration once after v157 and reserves the existing ledger IDs', () => {
  expect(distributedLockMigrations[0].version).toBe(158);
  expect(allMigrations.filter(m => m.version === 158)).toEqual([distributedLockMigrations[0]]);
  expect(Math.max(...allMigrations.filter(m => m.version < 158).map(m => m.version))).toBe(157);
  expect(allMigrations.filter(m => m.version === 155 || m.version === 156).map(m => m.sql)).toEqual([[], []]);
});

it.each(['sqlite', 'mysql', 'zdas'] as const)('renders an auto-increment id and a full unique logical lock key for %s', type => {
  const ddl = dialectFor(type).renderDdl(distributedLockDdl);
  expect(ddl).toMatch(type === 'sqlite' ? /id INTEGER PRIMARY KEY AUTOINCREMENT/ : /id BIGINT PRIMARY KEY AUTO_INCREMENT/);
  expect(ddl).toMatch(/UNIQUE\s*(?:INDEX\s+\w+\s*)?\(lock_id\)/i);
  expect(ddl).not.toMatch(/lock_id\s*\(\d+\)/);
  expect(ddl).not.toMatch(/ce_tasks|workflow_repair_release_records|created_at_ms|updated_at_ms/);
  for (const name of ['gmt_create', 'gmt_modified']) {
    expect(ddl).toContain(type === 'sqlite' ? `${name} INTEGER NOT NULL DEFAULT (unixepoch())`
      : `${name} TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP`);
  }
  if (type !== 'sqlite') expect(ddl).toContain('gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP');
});

it.each(['auto-increment', 'unique-key', 'gmt_create', 'gmt_modified'] as const)('rejects a complete-looking table missing its %s guarantee', async missing => {
  const db = new SqliteDatabase(new Database(':memory:')); databases.push(db);
  const ddl = missing === 'auto-increment' ? distributedLockDdl.replace('AUTOINCREMENT', '')
    : missing === 'unique-key' ? distributedLockDdl.replace(',\n  UNIQUE INDEX uk_clawweb_distributed_lock (lock_id)', '')
    : distributedLockDdl.replace(new RegExp(`  ${missing}[^\\n]+\\n`), '');
  await db.exec(db.dialect.renderDdl(ddl));
  await expect(assertDistributedLockSchema(db)).rejects.toThrow('Incompatible clawweb_distributed_locks');
});

it('does not advance v158 after a storage failure and safely retries after provisioning', async () => {
  vi.spyOn(console, 'log').mockImplementation(() => {});
  const db = new SqliteDatabase(new Database(':memory:')); databases.push(db);
  await runMigrations(db, 'sqlite');
  await db.exec('DROP TABLE clawweb_distributed_locks');
  // Simulate a pre-v158 ledger even when fresh boot also applied later migrations.
  await db.exec('DELETE FROM schema_version WHERE version >= 158');
  const exec = db.exec.bind(db), failure = new Error('lock table create denied');
  const spy = vi.spyOn(db, 'exec').mockImplementation((sql, params) => sql.includes('CREATE TABLE IF NOT EXISTS clawweb_distributed_locks')
    ? Promise.reject(failure) : exec(sql, params));
  await expect(runMigrations(db, 'sqlite')).rejects.toBe(failure);
  expect(await db.query('SELECT version FROM schema_version WHERE version = 158')).toEqual([]);
  spy.mockRestore();
  await runMigrations(db, 'sqlite');
  await assertDistributedLockSchema(db);
  expect(await db.query('SELECT version FROM schema_version WHERE version = 158')).toEqual([{ version: 158 }]);
});

it('rejects a duplicate lock migration version instead of executing an ambiguous registry', () => {
  const registrations = distributedLockMigrations as Array<(typeof distributedLockMigrations)[number]>;
  registrations.push({ ...registrations[0] });
  try { expect(() => collectMigrations()).toThrow('Conflicting distributed lock migration version 158'); }
  finally { registrations.pop(); }
});

it('keeps the operator MySQL provisioning SQL aligned with the canonical schema', () => {
  const sql = readFileSync(new URL('../../../../scripts/sql/distributed-locks-v158.mysql.sql', import.meta.url), 'utf8');
  expect(sql).toContain(dialectFor('mysql').renderDdl(distributedLockDdl) + ' ENGINE=InnoDB;');
});
