/**
 * Database access types for clawmind.
 *
 * Provides IDatabase interface and supporting types for both
 * SqliteDatabase (local) and ZdasDatabase (production) adapters.
 */

// ── Core Types ──

/** A single database row returned from a SELECT query. */
export type Row = Record<string, unknown>;

/** Result of a non-SELECT statement (INSERT/UPDATE/DELETE). */
export type ExecResult = {
  affectedRows: number;
  insertId?: number;
};

// ── Database Interface ──

/**
 * Database access interface.
 *
 * Implementations: SqliteDatabase (local, node:sqlite), ZdasDatabase (prod, mysql2).
 * All methods are async; all use parameterized queries to prevent SQL injection.
 */
export interface IDatabase {
  /** Database type identifier. */
  readonly dbType: "sqlite" | "mysql" | "noop";

  /**
   * Execute a SELECT query and return typed rows.
   *
   * @param sql  - SQL statement with `?` placeholders
   * @param params - Positional parameter values
   */
  query<T = Row>(sql: string, params?: unknown[]): Promise<T[]>;

  /**
   * Execute a non-SELECT statement (INSERT/UPDATE/DELETE).
   *
   * @param sql  - SQL statement with `?` placeholders
   * @param params - Positional parameter values
   */
  exec(sql: string, params?: unknown[]): Promise<ExecResult>;

  /**
   * Run `fn` inside a transaction.
   * Commits on success, rolls back on error, and re-throws the error.
   */
  transaction<T>(fn: (db: IDatabase) => Promise<T>): Promise<T>;

  /** Release all database resources (connection pool, file handle, etc.). */
  close(): Promise<void>;
}

// ── Configuration Types ──

/** MySQL connection parameters for ZDAS proxy. */
export type MySqlConfig = {
  host: string;
  port: number;
  user: string;
  password: string;
  database: string;
  poolSize?: number;
  poolMin?: number;
};

/** API mode config for clawweb internal endpoints. */
export type ApiConfig = {
  baseUrl: string;
  privateKeyB64: string;
  iamtoken?: string;
  timeout?: number;
  maxRetries?: number;
};

/** Configuration for creating a database instance. */
export type DatabaseConfig = {
  type: "sqlite" | "mysql" | "api";
  sqlitePath?: string;
  mysql?: MySqlConfig;
  api?: ApiConfig;
};

/**
 * Format a timestamp for the given database type.
 * SQLite accepts raw unix seconds; MySQL requires 'YYYY-MM-DD HH:MM:SS'.
 */
export function formatTimestamp(dbType: "sqlite" | "mysql" | "noop", unixSeconds: number): number | string {
  if (dbType === "mysql") {
    const d = new Date(unixSeconds * 1000);
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }
  return unixSeconds;
}

/** Convenience: returns the current time formatted for the given dbType. */
export function nowForDb(dbType: "sqlite" | "mysql" | "noop"): number | string {
  return formatTimestamp(dbType, Math.floor(Date.now() / 1000));
}