import mysql, { type Pool, type PoolConnection, type ResultSetHeader } from 'mysql2/promise';
import { randomUUID } from 'node:crypto';
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { IDatabase } from '../../db.js';
import { mysqlDialect } from '../../db/dialect.js';
import { distributedLockMigrations, assertDistributedLockSchema } from '../../schema-distributed-lock.js';
import { DistributedLockRepository, DistributedLeaseLostError } from '../distributed-lock-repository.js';

const socketPath = process.env.CLAWWEB_LOCK_TEST_MYSQL_SOCKET;
const options = { environment: 'prod', scene: 'maintenance', tenantId: '__global__', resourceKey: 'scan', leaseMs: 120_000 };
function adapter(connection: Pool | PoolConnection): IDatabase {
  const db: IDatabase = {
    dbType: 'mysql', dialect: mysqlDialect,
    async query<T>(sql: string, params?: unknown[]) { const [rows] = await connection.query(sql, params); return rows as T[]; },
    async exec(sql, params) {
      const [result] = await connection.query(sql, params);
      return { affectedRows: (result as ResultSetHeader).affectedRows, insertId: (result as ResultSetHeader).insertId };
    },
    async transaction<T>(fn: (db: IDatabase) => Promise<T>) {
      if (!('getConnection' in connection)) return fn(db);
      const c = await connection.getConnection();
      try { await c.beginTransaction(); const value = await fn(adapter(c)); await c.commit(); return value; }
      catch (error) { await c.rollback(); throw error; } finally { c.release(); }
    },
    async close() {},
  };
  return db;
}

/** Explicit opt-in to a disposable local server. Never reads application DB credentials. */
describe.skipIf(!socketPath)('distributed lease MySQL concurrency', () => {
  const name = 'cw_lock_test_' + randomUUID().replaceAll('-', '');
  let admin: mysql.Connection, pool: Pool, db: IDatabase;
  beforeAll(async () => {
    admin = await mysql.createConnection({ socketPath, user: 'root' });
    await admin.query('CREATE DATABASE ' + name + ' CHARACTER SET utf8mb4 COLLATE utf8mb4_bin');
    pool = mysql.createPool({ socketPath, user: 'root', database: name, connectionLimit: 8 });
    db = adapter(pool);
    await distributedLockMigrations[0].migrate!(db);
  });
  beforeEach(async () => { await db.exec('DELETE FROM clawweb_distributed_locks'); });
  afterAll(async () => {
    vi.restoreAllMocks();
    await pool?.end();
    if (admin) { await admin.query('DROP DATABASE IF EXISTS ' + name); await admin.end(); }
  });

  it('admits exactly one of eight simultaneous callers on a missing lock', async () => {
    const results = await Promise.all(Array.from({ length: 8 }, () => new DistributedLockRepository(db).claim(options)));
    expect(results.filter(Boolean)).toHaveLength(1);
    const rows = await db.query<{ id: number; owner_token: string }>('SELECT id, owner_token FROM clawweb_distributed_locks');
    expect(rows).toHaveLength(1);
    expect(rows[0].id).toBeGreaterThan(0);
    expect(rows[0].owner_token).toBe(results.find(Boolean)!.ownerToken);
  });

  it('retains the auto-increment primary key and validates the full unique index', async () => {
    await assertDistributedLockSchema(db);
    const columns = await db.query<{ Field: string; Type: string; Null: string; Default: string; Extra: string }>(
      'SHOW COLUMNS FROM clawweb_distributed_locks');
    for (const name of ['gmt_create', 'gmt_modified']) {
      const column = columns.find(c => c.Field === name)!;
      expect(column.Type).toBe('timestamp');
      expect(column.Null).toBe('NO');
      expect(column.Default.toUpperCase()).toMatch(/^CURRENT_TIMESTAMP(?:\(\))?$/);
      if (name === 'gmt_modified') expect(column.Extra.toLowerCase()).toContain('on update current_timestamp');
    }
    const a = new DistributedLockRepository(db), b = new DistributedLockRepository(db);
    const first = (await a.claim(options))!;
    const before = await db.query('SELECT id FROM clawweb_distributed_locks');
    expect(await a.release(first)).toBe(true);
    const next = (await b.claim(options))!;
    expect(next.ownerToken).not.toBe(first.ownerToken);
    expect(await db.query('SELECT id FROM clawweb_distributed_locks')).toEqual(before);
  });

  it('keeps creation time while claim, renewal and release update standard TIMESTAMP audit fields', async () => {
    const a = new DistributedLockRepository(db);
    let lease = (await a.claim(options))!;
    const [created] = await db.query<{ created: number; modified: number; db_now: number }>(
      'SELECT UNIX_TIMESTAMP(gmt_create) AS created, UNIX_TIMESTAMP(gmt_modified) AS modified, UNIX_TIMESTAMP() AS db_now FROM clawweb_distributed_locks');
    expect(created.created).toBeGreaterThan(0);
    expect(created.modified).toBeGreaterThanOrEqual(created.created);
    expect(created.modified).toBeLessThanOrEqual(created.db_now);
    for (const action of ['renew', 'release', 'claim'] as const) {
      await db.exec('UPDATE clawweb_distributed_locks SET gmt_create = FROM_UNIXTIME(1), gmt_modified = FROM_UNIXTIME(1)');
      if (action === 'renew') lease = await a.renew(lease);
      else if (action === 'release') expect(await a.release(lease)).toBe(true);
      else expect(await a.claim(options)).not.toBeNull();
      const [row] = await db.query<{ created: number; modified: number; db_now: number }>(
        'SELECT UNIX_TIMESTAMP(gmt_create) AS created, UNIX_TIMESTAMP(gmt_modified) AS modified, UNIX_TIMESTAMP() AS db_now FROM clawweb_distributed_locks');
      expect(row.created).toBe(1);
      expect(row.modified).toBeGreaterThan(1);
      expect(row.modified).toBeLessThanOrEqual(row.db_now);
    }
  });

  it('uses database expiry and rejects a stale holder after takeover', async () => {
    const a = new DistributedLockRepository(db), b = new DistributedLockRepository(db);
    const old = (await a.claim(options))!;
    await db.exec('UPDATE clawweb_distributed_locks SET expires_at_ms = ' + mysqlDialect.nowMsSql + ' - 1 WHERE lock_id = ?', [old.lockId]);
    const next = (await b.claim(options))!;
    await expect(a.renew(old)).rejects.toBeInstanceOf(DistributedLeaseLostError);
    expect(await a.release(old)).toBe(false);
    expect(await db.query('SELECT owner_token FROM clawweb_distributed_locks')).toEqual([{ owner_token: next.ownerToken }]);
  });

  it('renews from the persisted custom duration independently of application wall-clock skew', async () => {
    const a = new DistributedLockRepository(db);
    const lease = (await a.claim({ ...options, leaseMs: 30_000 }))!;
    vi.spyOn(Date, 'now').mockReturnValue(9_000_000_000_000);
    try {
      const renewed = await a.renew(lease);
      expect(renewed.leaseMs).toBe(30_000);
      expect(renewed.remainingMs).toBeGreaterThan(29_000);
      expect(renewed.remainingMs).toBeLessThanOrEqual(30_000);
      expect(await new DistributedLockRepository(db).claim(options)).toBeNull();
    } finally { vi.restoreAllMocks(); }
  });

  it('keeps a release cooldown in the database across independent callers', async () => {
    const a = new DistributedLockRepository(db), b = new DistributedLockRepository(db);
    const lease = (await a.claim({ ...options, minIntervalMs: 60_000 }))!;
    expect(await a.release(lease)).toBe(true);
    expect(await b.claim(options)).toBeNull();
    await db.exec('UPDATE clawweb_distributed_locks SET next_acquire_at_ms = ' + mysqlDialect.nowMsSql + ' - 1 WHERE lock_id = ?', [lease.lockId]);
    expect(await b.claim(options)).not.toBeNull();
  });
  it('rejects a truncated unique index instead of treating it as effective lock identity', async () => {
    await db.exec('ALTER TABLE clawweb_distributed_locks DROP INDEX uk_clawweb_distributed_lock');
    await db.exec('ALTER TABLE clawweb_distributed_locks ADD UNIQUE INDEX uk_clawweb_distributed_lock (lock_id(16))');
    try { await expect(assertDistributedLockSchema(db)).rejects.toThrow('Incompatible clawweb_distributed_locks'); }
    finally {
      await db.exec('ALTER TABLE clawweb_distributed_locks DROP INDEX uk_clawweb_distributed_lock');
      await db.exec('ALTER TABLE clawweb_distributed_locks ADD UNIQUE INDEX uk_clawweb_distributed_lock (lock_id)');
    }
  });

});
