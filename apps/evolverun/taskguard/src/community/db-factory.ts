/**
 * Community default database factory.
 * Supports SQLite (local) and NoOp (API mode without corp extension).
 * Corp extensions provide MySQL/ZDAS via extensions.createDatabase.
 */
import type { IDatabase, DatabaseConfig } from "../db/types.js";
import { SqliteDatabase } from "../db/sqlite-database.js";
import { SchemaMigrator } from "../db/migrator.js";
import { NoOpDatabase } from "../db/factory.js";

export async function createCommunityDatabase(config: DatabaseConfig): Promise<IDatabase> {
  if (config.type === "sqlite") {
    const db = new SqliteDatabase({ path: config.sqlitePath ?? '' });
    await db.connect();
    const migrator = new SchemaMigrator(db, "sqlite");
    await migrator.migrate();
    return db;
  }
  // API mode or unknown: NoOp (corp extension provides real impl)
  return new NoOpDatabase(true);
}
