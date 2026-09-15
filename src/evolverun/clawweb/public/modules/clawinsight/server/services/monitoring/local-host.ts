/** Loopback-only integration host. Uses the production router/repository and real SQLite. */
import express from "express";
import Database from "better-sqlite3";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import { createMonitoringRouter } from "../../routes/monitoring.js";
import { initializeMonitoringSqlite } from "./schema.js";
import { createMonitoringRuntime } from "./monitoring-runtime.js";

const path = resolve(process.env.MONITORING_LOCAL_DB_PATH ?? ".local/monitoring.sqlite3");
mkdirSync(dirname(path), { recursive: true });
const sqlite = new Database(path);
sqlite.pragma("journal_mode = WAL");
sqlite.pragma("synchronous = FULL");
const db = new SqliteDatabase(sqlite);
await initializeMonitoringSqlite(db);
const runtime = createMonitoringRuntime(() => db);
if (!runtime.service) throw new Error("Invalid monitoring configuration");
const port = Number(process.env.MONITORING_LOCAL_PORT ?? "3101");
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Invalid local port");
const app = express();
app.use("/api/insight/v1", createMonitoringRouter(runtime));
const server = app.listen(port, "127.0.0.1", () => console.info(`[monitoring] local API: http://127.0.0.1:${port}/api/insight/v1; SQLite: ${path}`));
let stopping = false;
const stop = () => {
  if (stopping) return;
  stopping = true;
  server.close(() => { void db.close(); });
};
process.once("SIGINT", stop);
process.once("SIGTERM", stop);
