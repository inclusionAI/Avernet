/**
 * SqliteDatabase — local development adapter using node:sqlite (Node 22+).
 *
 * Uses DatabaseSync with WAL mode for concurrent read support.
 * All methods use parameterized queries to prevent SQL injection.
 */
import { existsSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";
import type { IDatabase, Row, ExecResult } from "./types.js";

type DatabaseSync = import("node:sqlite").DatabaseSync;

export type SqliteDatabaseOptions = {
  /** Path to the SQLite database file. Created if it doesn't exist. */
  path: string;
};

export class SqliteDatabase implements IDatabase {
  readonly dbType = "sqlite" as const;

  private db: DatabaseSync | null = null;
  private closed = false;

  constructor(private options: SqliteDatabaseOptions) {}

  /** Open (or return existing) database connection with WAL mode enabled. */
  async connect(): Promise<void> {
    if (this.db) return;
    if (this.closed) throw new Error("SqliteDatabase: cannot reconnect after close()");

    const dir = dirname(this.options.path);
    if (!existsSync(dir)) {
      mkdirSync(dir, { recursive: true });
    }

    const sqlite = await import("node:sqlite");
    this.db = new sqlite.DatabaseSync(this.options.path);

    // Enable WAL mode for concurrent read support
    this.db.exec("PRAGMA journal_mode=WAL");
    // Enable foreign keys
    this.db.exec("PRAGMA foreign_keys=ON");
    // Busy timeout: wait up to 5s when another process holds the write lock
    // instead of immediately throwing SQLITE_BUSY. Essential when Claude Code
    // (stdio) and TeClaw (HTTP-SSE) processes write to the same DB file.
    this.db.exec("PRAGMA busy_timeout=5000");
  }

  private ensureOpen(): DatabaseSync {
    if (!this.db || this.closed) {
      throw new Error("SqliteDatabase: database is not open. Call connect() first or after close().");
    }
    return this.db;
  }

  async query<T = Row>(sql: string, params: unknown[] = []): Promise<T[]> {
    const db = this.ensureOpen();
    const stmt = db.prepare(sql);
    const rows = stmt.all(...params as import("node:sqlite").SQLInputValue[]) as T[];
    return rows;
  }

  async exec(sql: string, params: unknown[] = []): Promise<ExecResult> {
    const db = this.ensureOpen();
    if (params.length > 0) {
      const stmt = db.prepare(sql);
      const result = stmt.run(...params as import("node:sqlite").SQLInputValue[]) as { lastInsertRowid: number; changes: number };
      return {
        affectedRows: result.changes,
        insertId: result.lastInsertRowid,
      };
    }
    // For DDL or parameterless statements, use db.exec()
    db.exec(sql);
    return { affectedRows: 0 };
  }

  async transaction<T>(fn: (db: IDatabase) => Promise<T>): Promise<T> {
    const db = this.ensureOpen();
    db.exec("BEGIN IMMEDIATE");
    try {
      const result = await fn(this);
      db.exec("COMMIT");
      return result;
    } catch (error) {
      db.exec("ROLLBACK");
      throw error;
    }
  }

  async close(): Promise<void> {
    if (this.closed || !this.db) return;
    this.db.close();
    this.db = null;
    this.closed = true;
  }
}