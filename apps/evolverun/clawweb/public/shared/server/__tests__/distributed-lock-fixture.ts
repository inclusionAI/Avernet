import Database from 'better-sqlite3';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { SqliteDatabase } from '../db/sqlite-database.js';
import { distributedLockMigrations } from '../schema-distributed-lock.js';
import { DistributedLockRepository } from '../repositories/distributed-lock-repository.js';

export const lockOptions = { environment: 'prod', scene: 'maintenance', tenantId: '__global__', resourceKey: 'scan', leaseMs: 120_000 };
/** Two independent connections; only the database clock is controlled. */
export async function lockFixture() {
  const root = mkdtempSync(join(tmpdir(), 'cw-distributed-lock-test-'));
  const clock = { now: 1_000_000 };
  const databases: SqliteDatabase[] = [];
  async function connect() {
    const raw = new Database(join(root, 'locks.sqlite'));
    raw.pragma('journal_mode = WAL');
    raw.function('lock_test_now_ms', () => clock.now);
    const db = new SqliteDatabase(raw);
    Object.assign(db, { dialect: { ...db.dialect, nowMsSql: 'lock_test_now_ms()' } });
    databases.push(db);
    return db;
  }
  const db = await connect();
  await distributedLockMigrations[0].migrate!(db);
  const other = await connect();
  return { db, other, clock, a: new DistributedLockRepository(db), b: new DistributedLockRepository(other),
    async close() { for (const db of databases) await db.close(); rmSync(root, { recursive: true, force: true }); } };
}
