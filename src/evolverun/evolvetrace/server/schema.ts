/**
 * Evolvetrace database schema initialization.
 * Reads SQL files from scripts/sql/ and applies them to SQLite/MySQL.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { IDatabase } from "./db.js";

const __dirname = dirname(fileURLToPath(import.meta.url));

function projectRoot(): string {
  // server/schema.ts -> project root
  return join(__dirname, "..");
}

function readSqlFile(name: string): string {
  const path = join(projectRoot(), "scripts", "sql", name);
  return readFileSync(path, "utf-8");
}

async function ensureSchemaVersionTable(db: IDatabase): Promise<void> {
  await db.exec(`
    CREATE TABLE IF NOT EXISTS schema_version (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      version INTEGER NOT NULL,
      description TEXT,
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
    )
  `);
}

async function getCurrentSchemaVersion(db: IDatabase): Promise<number> {
  try {
    const rows = await db.query<{ version: number }>(
      "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
    );
    return rows[0]?.version ?? 0;
  } catch {
    return 0;
  }
}

async function recordSchemaVersion(db: IDatabase, version: number, description: string): Promise<void> {
  await db.exec(
    "INSERT INTO schema_version (version, description) VALUES (?, ?)",
    [version, description]
  );
}

const SCHEMA_VERSION = 2;

export async function initSchema(db: IDatabase): Promise<void> {
  if (db.dbType === "noop") {
    console.log("[evolvetrace] NoOp database: skipping schema initialization");
    return;
  }

  const fileName = db.dbType === "mysql" ? "evolvetrace_mysql.sql" : "evolvetrace_sqlite.sql";
  const sql = readSqlFile(fileName);

  await db.transaction(async (tx) => {
    if (db.dbType === "sqlite") {
      await ensureSchemaVersionTable(tx);
      const currentVersion = await getCurrentSchemaVersion(tx);
      if (currentVersion >= SCHEMA_VERSION) {
        console.log(`[evolvetrace] Schema already at version ${currentVersion}`);
        return;
      }
    }

    console.log(`[evolvetrace] Applying schema from ${fileName} (target version ${SCHEMA_VERSION})`);
    // Execute the whole SQL file at once. All statements use IF NOT EXISTS,
    // so re-applying on an existing database is safe — it will only create missing tables.
    await tx.exec(sql);

    if (db.dbType === "sqlite") {
      await recordSchemaVersion(tx, SCHEMA_VERSION, `Evolvetrace schema v${SCHEMA_VERSION} (includes TcLog tables)`);
    }
  });

  console.log("[evolvetrace] Schema initialized successfully");
}
