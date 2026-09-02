import Database from "better-sqlite3";
import express from "express";
import { existsSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { createClawevolveModule } from "./create-module.js";
import { runMigrations, SqliteDatabase } from "./db.js";

function positivePort(value: string | undefined): number {
  const port = Number(value ?? 3210);
  if (!Number.isSafeInteger(port) || port < 1 || port > 65_535) {
    throw new Error("CLAWEVOLVE_PORT must be an integer between 1 and 65535");
  }
  return port;
}

function createLocalDatabase(dataDirectory: string): SqliteDatabase {
  const dataRoot = resolve(dataDirectory);
  mkdirSync(dataRoot, { recursive: true, mode: 0o700 });
  const raw = new Database(join(dataRoot, "clawevolve.db"));
  raw.pragma("journal_mode = WAL");
  raw.pragma("foreign_keys = ON");
  raw.pragma("busy_timeout = 5000");
  return new SqliteDatabase(raw);
}

async function main(): Promise<void> {
  const port = positivePort(process.env.CLAWEVOLVE_PORT ?? process.env.PORT);
  const db = createLocalDatabase(
    process.env.CLAWEVOLVE_DATA_DIR ?? join(homedir(), ".clawevolve"),
  );
  await runMigrations(db, "sqlite");

  const module = createClawevolveModule({
    db,
    // Singlebox intentionally has no enterprise transport. Creating and
    // inspecting tasks remains available while dispatch is reported locally.
    dispatch: async (input) => ({
      runId: `local-${input.stepId}`,
      sessionId: null,
      platformResponse: {
        message: "Singlebox local dispatcher accepted the step",
        evolve_dispatch: { provider: "local", transport: "message" },
      },
    }),
  });
  await module.start();

  const app = express();
  app.use(express.json({ limit: "10mb" }));
  app.get("/health", (_request, response) => {
    response.json({ status: "ok", db: db.dbType, mode: "singlebox" });
  });
  app.use("/api/evolve", module.publicRouter);
  app.use("/api/internal/evolve", module.internalRouter);
  app.use("/api/internal/task-guard", module.taskGuardRouter);

  const staticDirectory = join(import.meta.dirname, "..", "singlebox");
  if (existsSync(staticDirectory)) {
    app.use(express.static(staticDirectory));
    app.get("{*path}", (request, response) => {
      if (request.path.startsWith("/api/")) {
        response.status(404).json({ error: "Not Found" });
        return;
      }
      response.sendFile(join(staticDirectory, "index.html"));
    });
  }

  const server = app.listen(port, "127.0.0.1", () => {
    console.log(`[clawevolve] Singlebox listening on http://127.0.0.1:${port}/evolve`);
  });
  const shutdown = async () => {
    server.close();
    await module.stop();
    await db.close();
  };
  process.once("SIGINT", () => void shutdown());
  process.once("SIGTERM", () => void shutdown());
}

main().catch((error) => {
  console.error("[clawevolve] Singlebox failed to start:", error);
  process.exitCode = 1;
});
