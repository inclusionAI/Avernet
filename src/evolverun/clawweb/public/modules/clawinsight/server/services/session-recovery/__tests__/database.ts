import Database from "better-sqlite3";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import { initializeRecoveryDeliverySqlite } from "../schema.js";
export async function recoveryDatabase(path = ":memory:") {
  const raw = new Database(path); raw.pragma("busy_timeout = 5000");
  const db = new SqliteDatabase(raw);
  await initializeRecoveryDeliverySqlite(db);
  return db;
}
