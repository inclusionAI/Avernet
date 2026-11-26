import { createHash, randomUUID } from 'node:crypto';
import type { IDatabase } from '../db.js';
import { assertDistributedLockSchema } from '../schema-distributed-lock.js';

export interface DistributedLockScope {
  environment: string;
  scene: string;
  tenantId: string;
  resourceKey: string;
}
export interface DistributedLockOptions extends DistributedLockScope {
  leaseMs: number;
  /** Zero/default permits an immediate new holder after release. */
  minIntervalMs?: number;
}
export type DistributedLockRecord = {
  lockId: string; ownerToken: string; version: number; leaseMs: number; remainingMs: number;
};
type LockRow = {
  lock_id: string; owner_token: string | null; version: number | string; lease_ms: number | string;
  expires_at_ms: number | string; next_acquire_at_ms: number | string; db_now_ms: number | string;
};
export class DistributedLeaseLostError extends Error {
  readonly code = 'DISTRIBUTED_LOCK_LEASE_LOST';
  constructor() { super('Distributed lock lease is no longer held'); }
}
function duplicate(error: unknown, db: IDatabase): boolean {
  const e = error as { code?: string; errno?: number };
  return db.dbType === 'sqlite' ? e.code === 'SQLITE_CONSTRAINT_UNIQUE'
    : e.code === 'ER_DUP_ENTRY' || e.errno === 1062;
}
function validate(options: DistributedLockOptions): void {
  const parts: Array<[string, number]> = [[options.environment, 16], [options.scene, 64], [options.tenantId, 128], [options.resourceKey, 256]];
  if (parts.some(([value, length]) => typeof value !== 'string' || !value.length || value.length > length))
    throw new Error('Invalid distributed lock scope');
  if (!Number.isSafeInteger(options.leaseMs) || options.leaseMs <= 0 || options.leaseMs > 2_147_483_647
    || !Number.isSafeInteger(options.minIntervalMs ?? 0) || (options.minIntervalMs ?? 0) < 0
    || (options.minIntervalMs ?? 0) > 2_147_483_647) throw new Error('Invalid distributed lock duration');
}
function owned(row: LockRow | null, token: string, version: number): DistributedLockRecord {
  if (!row || row.owner_token !== token || Number(row.version) !== version
    || Number(row.expires_at_ms) <= Number(row.db_now_ms)) throw new DistributedLeaseLostError();
  return { lockId: row.lock_id, ownerToken: token, version, leaseMs: Number(row.lease_ms),
    remainingMs: Number(row.expires_at_ms) - Number(row.db_now_ms) };
}

/** Unique insert + conditional UPDATE are the authority. No transaction spans business work. */
export class DistributedLockRepository {
  private readiness?: Promise<void>;
  constructor(private readonly db: IDatabase) {}
  private async ready(): Promise<void> {
    this.readiness ??= assertDistributedLockSchema(this.db).catch(error => { this.readiness = undefined; throw error; });
    await this.readiness;
  }
  private async get(lockId: string): Promise<LockRow | null> {
    const rows = await this.db.query<LockRow>(
      'SELECT lock_id, owner_token, version, lease_ms, expires_at_ms, next_acquire_at_ms, '
      + this.db.dialect.nowMsSql + ' AS db_now_ms FROM clawweb_distributed_locks WHERE lock_id = ?', [lockId]);
    return rows[0] ?? null;
  }
  async claim(options: DistributedLockOptions): Promise<DistributedLockRecord | null> {
    validate(options);
    await this.ready();
    const lockId = createHash('sha256').update(JSON.stringify(
      [options.environment, options.scene, options.tenantId, options.resourceKey])).digest('hex');
    const now = this.db.dialect.nowMsSql;
    const modified = this.db.dbType === 'sqlite' ? 'unixepoch()' : 'CURRENT_TIMESTAMP';
    let row = await this.get(lockId);
    if (!row) {
      try {
        await this.db.exec('INSERT INTO clawweb_distributed_locks '
          + '(lock_id, environment, scene, tenant_id, resource_key) '
          + 'VALUES (?, ?, ?, ?, ?)',
        [lockId, options.environment, options.scene, options.tenantId, options.resourceKey]);
      } catch (error) {
        if (!duplicate(error, this.db) || !await this.get(lockId)) throw error;
      }
      row = await this.get(lockId);
      if (!row) throw new Error('Distributed lock record was not persisted');
    }
    if (Number(row.expires_at_ms) > Number(row.db_now_ms) || Number(row.next_acquire_at_ms) > Number(row.db_now_ms)) return null;
    const version = Number(row.version);
    if (!Number.isSafeInteger(version) || version < 0 || version >= Number.MAX_SAFE_INTEGER) throw new Error('Invalid distributed lock version');
    const ownerId = randomUUID();
    const result = await this.db.exec('UPDATE clawweb_distributed_locks SET owner_token = ?, lease_ms = ?, expires_at_ms = '
      + now + ' + ?, next_acquire_at_ms = ' + now + ' + ?, version = version + 1, gmt_modified = ' + modified
      + ' WHERE lock_id = ? AND version = ? AND expires_at_ms <= ' + now + ' AND next_acquire_at_ms <= ' + now,
    [ownerId, options.leaseMs, options.leaseMs, options.minIntervalMs ?? 0, lockId, version]);
    if (result.affectedRows === 0) return null;
    if (result.affectedRows !== 1) throw new Error('Distributed lock claim updated an unexpected number of rows');
    return owned(await this.get(lockId), ownerId, version + 1);
  }
  async renew(lease: DistributedLockRecord): Promise<DistributedLockRecord> {
    const now = this.db.dialect.nowMsSql;
    const modified = this.db.dbType === 'sqlite' ? 'unixepoch()' : 'CURRENT_TIMESTAMP';
    const result = await this.db.exec('UPDATE clawweb_distributed_locks SET expires_at_ms = ' + now
      + ' + lease_ms, version = version + 1, gmt_modified = ' + modified
      + ' WHERE lock_id = ? AND owner_token = ? AND version = ? AND expires_at_ms > ' + now,
    [lease.lockId, lease.ownerToken, lease.version]);
    if (result.affectedRows !== 1) throw new DistributedLeaseLostError();
    return owned(await this.get(lease.lockId), lease.ownerToken, lease.version + 1);
  }
  async release(lease: DistributedLockRecord): Promise<boolean> {
    const now = this.db.dialect.nowMsSql;
    const modified = this.db.dbType === 'sqlite' ? 'unixepoch()' : 'CURRENT_TIMESTAMP';
    const result = await this.db.exec('UPDATE clawweb_distributed_locks SET owner_token = NULL, expires_at_ms = 0, '
      + 'version = version + 1, gmt_modified = ' + modified
      + ' WHERE lock_id = ? AND owner_token = ? AND version = ? AND expires_at_ms > ' + now,
    [lease.lockId, lease.ownerToken, lease.version]);
    if (result.affectedRows > 1) throw new Error('Distributed lock release updated an unexpected number of rows');
    return result.affectedRows === 1;
  }
}
