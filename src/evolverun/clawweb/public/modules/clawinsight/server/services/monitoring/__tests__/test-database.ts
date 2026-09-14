import Database from "better-sqlite3";
import { createRequire } from "node:module";
import { SqliteDatabase, type IDatabase } from "@avernet/clawweb-shared/server/db";
import { sqliteDialect } from "@avernet/clawweb-shared/server/db/dialect";
/** Explicit supplemental SQL conformance driver, never a production fallback.
 * Default tests use the actual shared better-sqlite3 adapter. On a machine missing its native addon,
 * MONITORING_TEST_SQLITE_DRIVER=node exercises SQL/HTTP only; it does NOT certify the production driver.
 */
export function database(path: string): IDatabase {
  if (process.env.MONITORING_TEST_SQLITE_DRIVER !== "node") return new SqliteDatabase(new Database(path));
  const { DatabaseSync } = createRequire(import.meta.url)("node:sqlite");
  const sqlite = new DatabaseSync(path);
  return {
    dbType: "sqlite", dialect: sqliteDialect,
    async query<T>(sql: string, params: unknown[] = []): Promise<T[]> { return sqlite.prepare(sql).all(...params as never[]) as T[]; },
    async exec(sql, params) {
      if (params === undefined) { sqlite.exec(sql); return { affectedRows: 0 }; }
      let result;
      try { result = sqlite.prepare(sql).run(...params as never[]); } catch (error) {
        // Translate only the unique-constraint code exposed differently by the supplemental driver.
        if ((error as { errcode?: number }).errcode === 2067) Object.assign(error as object, { code: "SQLITE_CONSTRAINT_UNIQUE" });
        throw error;
      }
      return { affectedRows: Number(result.changes), insertId: Number(result.lastInsertRowid) };
    },
    async transaction(fn) { sqlite.exec("BEGIN"); try { const result = await fn(this); sqlite.exec("COMMIT"); return result; } catch (error) { sqlite.exec("ROLLBACK"); throw error; } },
    async close() { sqlite.close(); },
  };
}
