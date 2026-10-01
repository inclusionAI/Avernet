/**
 * SchemaMigrator — manages database schema versions.
 *
 * Reads/writes a `schema_version` table, applies migrations in order,
 * and adapts SQLite/MySQL syntax differences.
 */
import type { IDatabase, Row } from "./types.js";
import { adaptDdl, migrations, type DbType } from "./schema.js";

const SCHEMA_VERSION_TABLE_SQL = `CREATE TABLE IF NOT EXISTS schema_version (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  version INTEGER NOT NULL,
  description TEXT,
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
)`;

export class SchemaMigrator {
  constructor(
    private db: IDatabase,
    private dbType: DbType,
  ) {}

  /**
   * Run all pending migrations. Creates the schema_version table if needed.
   * Each migration is wrapped in a transaction; on failure, that migration
   * is rolled back and an error is logged.
   */
  async migrate(): Promise<void> {
    const canDdl = await this.ensureSchemaVersionTable();
    if (!canDdl) {
      console.warn("[db] DDL denied — skipping migrations, assuming schema exists in managed DB");
      return;
    }
    const currentVersion = await this.getCurrentVersion();

    const pending = migrations.filter((m) => m.version > currentVersion);
    if (pending.length === 0) return;

    for (const migration of pending) {
      await this.applyMigration(migration);
    }
  }

  /** Get the current schema version (0 if no migrations applied yet). */
  async getCurrentVersion(): Promise<number> {
    try {
      const rows = await this.db.query<Row & { version: number }>(
        "SELECT MAX(version) AS version FROM schema_version",
      );
      if (rows.length === 0 || rows[0].version == null) return 0;
      return rows[0].version as number;
    } catch {
      // Table may not exist yet
      return 0;
    }
  }

  private async ensureSchemaVersionTable(): Promise<boolean> {
    const sql = adaptDdl(SCHEMA_VERSION_TABLE_SQL, this.dbType);
    try {
      await this.db.exec(sql);
      return true;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes("denied") || msg.includes("CREATE") || msg.includes("DDL")) {
        console.warn(`[db] DDL denied (assumes schema exists in managed DB): ${msg}`);
        return false;
      }
      throw err;
    }
  }

  private async applyMigration(migration: { version: number; description: string; sql: string[]; sqliteOnly?: boolean; mysqlOnly?: boolean }): Promise<void> {
    // Skip migrations that don't apply to the current DB type
    if (migration.sqliteOnly && this.dbType !== "sqlite") return;
    if (migration.mysqlOnly && this.dbType !== "mysql") return;

    const adaptedSql = migration.sql.map((s) => adaptDdl(s, this.dbType));

    try {
      await this.db.transaction(async (tx) => {
        for (const ddl of adaptedSql) {
          await tx.exec(ddl);
        }
        await tx.exec(
          "INSERT INTO schema_version (version, description) VALUES (?, ?)",
          [migration.version, migration.description],
        );
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.error(
        `[db] Migration v${migration.version} (${migration.description}) failed: ${msg}. ` +
          "Schema version not updated; migration rolled back.",
      );
      throw error;
    }
  }
}