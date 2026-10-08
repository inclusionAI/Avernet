/**
 * Database factory — creates the appropriate IDatabase implementation
 * based on configuration from YAML file and environment variables.
 *
 * Configuration priority (highest to lowest):
 * 1. Explicit options passed to createDatabase()
 * 2. Environment variables (DATABASE_MODE, ZDAS_*)
 * 3. configs/application.yaml
 * 4. Defaults
 *
 * Falls back to NoOpDatabase if initialization fails, ensuring
 * the workflow engine never crashes due to database unavailability.
 */
import type { IDatabase, Row, ExecResult, MySqlConfig } from "./types.js";
import { SqliteDatabase } from "./sqlite-database.js";
import { SchemaMigrator } from "./migrator.js";
import { loadDatabaseConfig, resolveConfigPath } from "./config.js";

// ── NoOpDatabase ──

/** Graceful degradation: logs warnings and returns empty/default results. */
export class NoOpDatabase implements IDatabase {
  readonly dbType = "noop" as const;

  private warn(msg: string): void {
    if (!this._silenced) {
      console.warn(`[db] NoOpDatabase: ${msg}`);
    }
  }

  constructor(private _silenced = false) {}

  async query<T = Row>(_sql: string, _params?: unknown[]): Promise<T[]> {
    this.warn("query() called but database is unavailable — returning empty array");
    return [];
  }

  async exec(_sql: string, _params?: unknown[]): Promise<ExecResult> {
    this.warn("exec() called but database is unavailable — returning no-op result");
    return { affectedRows: 0 };
  }

  async transaction<T>(fn: (db: IDatabase) => Promise<T>): Promise<T> {
    this.warn("transaction() called but database is unavailable — running fn with NoOp");
    return fn(this);
  }

  async close(): Promise<void> {
    // No-op
  }
}

// ── Factory ──

export type CreateDatabaseOptions = {
  /** Override DATABASE_MODE env var / YAML config. */
  mode?: "sqlite" | "api";
  /** Override SQLite database path. */
  sqlitePath?: string;
  /** Override MySQL config (instead of env vars / YAML). */
  mysqlConfig?: MySqlConfig;
  /** If true, return NoOpDatabase on failure instead of throwing. @default true */
  fallbackOnFailure?: boolean;
  /** Explicit path to application.yaml. If omitted, auto-discovered. */
  configPath?: string;
};

/**
 * Create and initialize a database instance based on configuration.
 *
 * Reads configs/application.yaml and environment variables, with the
 * following priority (highest wins):
 *   1. Explicit options passed here
 *   2. Environment variables (DATABASE_MODE, ZDAS_*)
 *   3. configs/application.yaml
 *   4. Built-in defaults
 *
 * After creating the adapter, runs schema migrations automatically.
 * On failure, returns a NoOpDatabase if `fallbackOnFailure` is true (default).
 */
export async function createDatabase(options: CreateDatabaseOptions = {}): Promise<IDatabase> {
  const fallback = options.fallbackOnFailure ?? true;

  // Load config from YAML, with env var overrides
  const config = loadDatabaseConfig(options.configPath);

  // Explicit options override everything
  // config.type is "sqlite" | "mysql" | "api", but our mode is "sqlite" | "prod" | "api"
  const mode: "sqlite" | "api" = options.mode ?? (config.type === "mysql" ? "api" : config.type);
  const sqlitePath = options.sqlitePath ?? config.sqlitePath;

  // API mode: no local database — all reads/writes go through clawweb
  if (mode === "api") {
    console.error(`[db] API mode: delegating to clawweb at ${config.api?.baseUrl ?? "http://localhost:3001"}`);
    console.error("[db] No local schema migrations needed — clawweb manages the database");
    return new NoOpDatabase(true); // silenced — NoOp is expected in API mode
  }

  // Log resolved config source for diagnostics
  const configSource = resolveConfigPath(options.configPath);
  if (configSource) {
    console.error(`[db] Loaded config from ${configSource} (mode=${mode})`);
  } else {
    console.error(`[db] No application.yaml found, using defaults (mode=${mode})`);
  }

  const maxRetries = 3;
  let lastError: unknown;

  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    try {
      let db: IDatabase;
      let dbType: "sqlite" | "mysql";

      // Community: MySQL/ZDAS mode is handled by corp extensions
      // If mode is not sqlite, fall through to sqlite as safe default
      if (false) {
        // Placeholder — corp extension overrides via extensions.createDatabase
      } else {
        const sqliteDb = new SqliteDatabase({ path: sqlitePath });
        await sqliteDb.connect();
        db = sqliteDb;
        dbType = "sqlite";
        console.error(`[db] Using SQLite at ${sqlitePath}`);
      }

      // Run schema migrations
      const migrator = new SchemaMigrator(db, dbType);
      await migrator.migrate();

      return db;
    } catch (error) {
      lastError = error;
      const msg = error instanceof Error ? error.message : String(error);
      const isTransient = msg.includes("nil pointer") || msg.includes("ECONNREFUSED") || msg.includes("ECONNRESET") || msg.includes("timed out") || msg.includes("PROTOCOL_CONNECTION_LOST");

      if (isTransient && attempt < maxRetries) {
        const delay = attempt * 1000;
        console.warn(`[db] Transient error on attempt ${attempt}/${maxRetries}: ${msg}. Retrying in ${delay}ms...`);
        await new Promise((r) => setTimeout(r, delay));
        continue;
      }

      if (fallback) {
        console.warn(`[db] Database initialization failed (${msg}), falling back to NoOpDatabase`);
        return new NoOpDatabase();
      }
      throw error;
    }
  }

  // Unreachable, but TypeScript needs it
  throw lastError;
}