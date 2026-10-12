import type Database from 'better-sqlite3';
import type { ExecResult, IDatabase } from '../db.js';
import { sqliteDialect } from './dialect.js';

/** One connection must not interleave async transactions or unrelated statements.
 * The transaction callback receives an exclusive facade; only that facade may
 * execute until commit/rollback. Independent callers are queued, including reads. */
export class SqliteDatabase implements IDatabase {
  readonly dbType = 'sqlite' as const;
  readonly dialect = sqliteDialect;
  private tail: Promise<unknown> = Promise.resolve();

  constructor(private db: Database.Database, private inTransaction = false) {}

  private exclusive<T>(fn: () => T | Promise<T>): Promise<T> {
    if (this.inTransaction) return Promise.resolve().then(fn);
    const next = this.tail.then(fn);
    this.tail = next.catch(() => undefined);
    return next;
  }

  query<T>(sql: string, params?: unknown[]): Promise<T[]> {
    return this.exclusive(() => this.db.prepare(sql).all(...(params ?? [])) as T[]);
  }

  exec(sql: string, params?: unknown[]): Promise<ExecResult> {
    return this.exclusive(() => {
      if (params === undefined) {
        this.db.exec(sql);
        return { affectedRows: 0, insertId: undefined };
      }
      const result = this.db.prepare(sql).run(...params);
      return { affectedRows: result.changes, insertId: Number(result.lastInsertRowid) };
    });
  }

  transaction<T>(fn: (db: IDatabase) => Promise<T>): Promise<T> {
    if (this.inTransaction) return fn(this);
    return this.exclusive(async () => {
      this.db.exec('BEGIN IMMEDIATE');
      try {
        const result = await fn(new SqliteDatabase(this.db, true));
        this.db.exec('COMMIT');
        return result;
      } catch (error) {
        this.db.exec('ROLLBACK');
        throw error;
      }
    });
  }

  close(): Promise<void> {
    return this.exclusive(() => { this.db.close(); });
  }
}
