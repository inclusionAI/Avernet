/**
 * Evolvetrace standalone database layer.
 * Minimal SQLite/MySQL shim used by runs/workflows/tclog/internal APIs.
 */
import mysql from "mysql2/promise";
import { existsSync, readFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";
import { parse as parseYaml } from "yaml";

export type Row = Record<string, unknown>;

export type ExecResult = {
  affectedRows: number;
  insertId?: number;
};

export type DbType = "sqlite" | "mysql" | "noop";

export interface DbDialect {
  /** Return a value suitable for MySQL TIMESTAMP / SQLite INTEGER columns. */
  now(): number | Date;
}

export interface IDatabase {
  readonly dbType: DbType;
  readonly dialect: DbDialect;
  query<T = Row>(sql: string, params?: unknown[]): Promise<T[]>;
  exec(sql: string, params?: unknown[]): Promise<ExecResult>;
  transaction<T>(fn: (db: IDatabase) => Promise<T>): Promise<T>;
  close(): Promise<void>;
}

class NoOpDatabase implements IDatabase {
  readonly dbType = "noop" as const;
  readonly dialect: DbDialect = { now: () => Date.now() };
  async query<T>(): Promise<T[]> { return []; }
  async exec(): Promise<ExecResult> { return { affectedRows: 0 }; }
  async transaction<T>(fn: (db: IDatabase) => Promise<T>): Promise<T> { return fn(this); }
  async close(): Promise<void> {}
}

class MysqlDatabase implements IDatabase {
  readonly dbType: "mysql" = "mysql";
  readonly dialect: DbDialect = { now: () => new Date() };
  constructor(private pool: mysql.Pool) {}
  async query<T>(sql: string, params?: unknown[]): Promise<T[]> {
    const [rows] = await this.pool.query(sql, params as mysql.QueryValues | undefined);
    return rows as T[];
  }
  async exec(sql: string, params?: unknown[]): Promise<ExecResult> {
    const [result] = await this.pool.query(sql, params as mysql.QueryValues | undefined);
    const r = result as mysql.ResultSetHeader;
    return { affectedRows: r.affectedRows, insertId: r.insertId || undefined };
  }
  async transaction<T>(fn: (db: IDatabase) => Promise<T>): Promise<T> {
    const conn = await this.pool.getConnection();
    try {
      await conn.beginTransaction();
      const txDb = new MysqlTransactionDb(conn);
      const result = await fn(txDb);
      await conn.commit();
      return result;
    } catch (err) {
      await conn.rollback();
      throw err;
    } finally {
      conn.release();
    }
  }
  async close(): Promise<void> {
    await this.pool.end();
  }
}

class MysqlTransactionDb implements IDatabase {
  readonly dbType: "mysql" = "mysql";
  readonly dialect: DbDialect = { now: () => new Date() };
  constructor(private conn: mysql.PoolConnection) {}
  async query<T>(sql: string, params?: unknown[]): Promise<T[]> {
    const [rows] = await this.conn.query(sql, params as mysql.QueryValues | undefined);
    return rows as T[];
  }
  async exec(sql: string, params?: unknown[]): Promise<ExecResult> {
    const [result] = await this.conn.query(sql, params as mysql.QueryValues | undefined);
    const r = result as mysql.ResultSetHeader | null;
    return { affectedRows: r?.affectedRows ?? 0, insertId: r?.insertId || undefined };
  }
  async transaction<T>(fn: (db: IDatabase) => Promise<T>): Promise<T> { return fn(this); }
  async close(): Promise<void> {}
}

type DbConfig = {
  mode: DbType;
  sqlitePath: string;
  mysql: {
    host: string;
    port: number;
    user: string;
    password: string;
    database: string;
  };
};

function getEnv(key: string): string | undefined {
  return process.env[key];
}

function envInt(key: string, fallback: number): number {
  const raw = getEnv(key);
  if (!raw) return fallback;
  const parsed = parseInt(raw, 10);
  return Number.isNaN(parsed) ? fallback : parsed;
}

function findConfigFile(): string | null {
  const searchDirs = [
    process.cwd(),
    join(process.cwd(), "configs"),
  ];
  for (const dir of searchDirs) {
    for (const filename of ["application.yaml", "application.yml"]) {
      const candidate = join(dir, filename);
      if (existsSync(candidate)) return candidate;
    }
  }
  return null;
}

function readYamlConfig(): Record<string, unknown> {
  const filePath = findConfigFile();
  if (!filePath) return {};
  try {
    const raw = readFileSync(filePath, "utf-8");
    return parseYaml(raw) as Record<string, unknown>;
  } catch (err) {
    console.warn(`[evolvetrace] Failed to parse ${filePath}:`, err instanceof Error ? err.message : String(err));
    return {};
  }
}

function resolveDbConfig(): DbConfig {
  const yaml = readYamlConfig();
  const dbConfig = (yaml.database ?? {}) as Record<string, unknown>;
  const sqliteConfig = (dbConfig.sqlite ?? {}) as Record<string, unknown>;
  const mysqlConfig = (dbConfig.mysql ?? {}) as Record<string, unknown>;

  const envMode = getEnv("DATABASE_MODE") as DbType | undefined;
  const mode = envMode ?? (dbConfig.mode as DbType | undefined) ?? "sqlite";

  const sqlitePathRaw =
    getEnv("SQLITE_PATH")
    ?? (sqliteConfig.path as string | undefined)
    ?? join(homedir(), ".evolvetrace", "engine.db");
  const sqlitePath = sqlitePathRaw.replace(/^~/, homedir());
  return {
    mode,
    sqlitePath,
    mysql: {
      host: getEnv("MYSQL_HOST") ?? (mysqlConfig.host as string | undefined) ?? "127.0.0.1",
      port: envInt("MYSQL_PORT", (mysqlConfig.port as number | undefined) ?? 3306),
      user: getEnv("MYSQL_USER") ?? (mysqlConfig.user as string | undefined) ?? "",
      password: getEnv("MYSQL_PASSWORD") ?? (mysqlConfig.password as string | undefined) ?? "",
      database: getEnv("MYSQL_DATABASE") ?? (mysqlConfig.database as string | undefined) ?? "",
    },
  };
}

export function resolveServerConfig(): { port: number } {
  const yaml = readYamlConfig();
  const server = (yaml.server ?? {}) as Record<string, unknown>;
  const raw = server.port;
  if (typeof raw === "number") return { port: raw };
  if (typeof raw === "string") {
    const parsed = parseInt(raw, 10);
    if (!Number.isNaN(parsed)) return { port: parsed };
  }
  return { port: 3001 };
}

async function initMysql(cfg: DbConfig["mysql"]): Promise<IDatabase> {
  const pool = mysql.createPool({
    multipleStatements: true,
    host: cfg.host,
    port: cfg.port,
    user: cfg.user,
    password: cfg.password,
    database: cfg.database,
    waitForConnections: true,
    connectionLimit: 10,
    queueLimit: 0,
  });
  return new MysqlDatabase(pool);
}

async function initSqlite(path: string): Promise<IDatabase> {
  const { dirname } = await import("node:path");
  mkdirSync(dirname(path), { recursive: true });
  // better-sqlite3 removed from dependencies; use dynamic import if available.
  const { default: Database } = await import("better-sqlite3");
  return new SqliteDatabaseWrapper(new Database(path));
}

class SqliteDatabaseWrapper implements IDatabase {
  readonly dbType = "sqlite" as const;
  readonly dialect: DbDialect = { now: () => Math.floor(Date.now() / 1000) };
  constructor(private db: any) {}
  async query<T>(sql: string, params?: unknown[]): Promise<T[]> {
    const stmt = this.db.prepare(sql);
    const rows = params ? stmt.all(...params) : stmt.all();
    return rows as T[];
  }
  async exec(sql: string, params?: unknown[]): Promise<ExecResult> {
    if (params && params.length > 0) {
      const stmt = this.db.prepare(sql);
      const result = stmt.run(...params);
      return { affectedRows: result.changes, insertId: result.lastInsertRowid as number | undefined };
    }
    // better-sqlite3 Database.exec() supports multi-statement SQL (e.g. schema init).
    this.db.exec(sql);
    return { affectedRows: 0 };
  }
  async transaction<T>(fn: (db: IDatabase) => Promise<T>): Promise<T> {
    this.db.exec("BEGIN");
    try {
      const result = await fn(this);
      this.db.exec("COMMIT");
      return result;
    } catch (err) {
      this.db.exec("ROLLBACK");
      throw err;
    }
  }
  async close(): Promise<void> {
    this.db.close();
  }
}

let cached: IDatabase | null = null;

export async function initDatabase(): Promise<IDatabase> {
  if (cached) return cached;
  const cfg = resolveDbConfig();
  let db: IDatabase;
  try {
    if (cfg.mode === "mysql") {
      db = await initMysql(cfg.mysql);
    } else {
      db = await initSqlite(cfg.sqlitePath);
    }
  } catch (err) {
    console.warn(`[evolvetrace] Database init failed: ${err instanceof Error ? err.message : String(err)}`);
    console.warn("[evolvetrace] Falling back to NoOp database");
    db = new NoOpDatabase();
  }
  cached = db;
  return db;
}

export async function closeDatabase(): Promise<void> {
  if (cached) {
    await cached.close();
    cached = null;
  }
}

export function resolveSignatureConfig(): { publicKeyB64: string } {
  const yaml = readYamlConfig();
  const internal = (yaml.internal ?? {}) as Record<string, unknown>;
  return {
    publicKeyB64: getEnv("EVOLVETRACE_INTERNAL_PUBLIC_KEY_B64")
      ?? (internal.public_key_b64 as string | undefined)
      ?? "",
  };
}

export function resolveAdminConfig(): { admins: Set<string> } {
  const yaml = readYamlConfig();
  const security = (yaml.security ?? {}) as Record<string, unknown>;
  const raw =
    getEnv("ADMIN_USER_IDS")
    ?? (security.admin_user_ids as string | undefined)
    ?? "";
  return { admins: new Set(raw.split(",").map((s) => s.trim()).filter(Boolean)) };
}

/** Stub BaaS config resolver — returns empty config in open-source version. */
export function resolveBaasConfig(): { apiKey: string; iamtoken: string; baseUrl: string } {
  return {
    apiKey: getEnv("BAAS_API_KEY") ?? "",
    iamtoken: getEnv("BAAS_IAMTOKEN") ?? "",
    baseUrl: getEnv("BAAS_BASE_URL") ?? "",
  };
}

/** Stub flow control config type for Evolvetrace. */
export type FlowControlAppConfig = {
  enabled?: boolean;
  maxConcurrentFlows?: number;
  maxNodesPerFlow?: number;
};
