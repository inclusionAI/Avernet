import Database from "better-sqlite3";
import express from "express";
import { existsSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { createClawevolveModule } from "./create-module.js";
import { runMigrations, SqliteDatabase } from "./db.js";
import { FilesystemObjectStore } from "./services/object-storage/filesystem-object-store.js";

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
  const dataDirectory = resolve(process.env.CLAWEVOLVE_DATA_DIR ?? join(homedir(), ".clawevolve"));
  const db = createLocalDatabase(dataDirectory);
  await runMigrations(db, "sqlite");
  const artifactStore = new FilesystemObjectStore(join(dataDirectory, "artifacts"));

  const module = createClawevolveModule({
    db,
    artifactStore,
    publicBaseUrl: `http://127.0.0.1:${port}`,
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
  app.put(
    "/api/singlebox/artifacts/:token",
    express.raw({ type: "*/*", limit: "10mb" }),
    async (request, response) => {
      try {
        const key = artifactStore.resolveSignedRequest(String(request.params.token), "PUT");
        const result = await artifactStore.putObject(
          key,
          Buffer.isBuffer(request.body) ? request.body : Buffer.from(request.body ?? ""),
          request.header("content-type") ?? "application/octet-stream",
        );
        response.set("ETag", result.etag).status(204).end();
      } catch (error) {
        response.status(400).json({ error: error instanceof Error ? error.message : String(error) });
      }
    },
  );
  app.get("/api/singlebox/artifacts/:token", async (request, response) => {
    try {
      const key = artifactStore.resolveSignedRequest(String(request.params.token), "GET");
      const object = await artifactStore.getObject(key);
      if (object.etag) response.set("ETag", object.etag);
      response.type(object.contentType ?? "application/octet-stream").send(object.content);
    } catch (error) {
      response.status(400).json({ error: error instanceof Error ? error.message : String(error) });
    }
  });
  app.use(express.json({ limit: "10mb" }));
  app.use((request, _response, next) => {
    // Singlebox binds only to loopback and uses one explicit local identity.
    request.headers["x-user-id"] ??= "singlebox";
    request.isAdmin = true;
    next();
  });
  app.get("/api/auth/me", (_request, response) => {
    response.json({
      userId: "singlebox",
      nickName: "Singlebox User",
      userName: "singlebox",
      displayName: "Singlebox User",
      avatarUrl: "",
      isAdmin: true,
      isClawEvolveAdmin: true,
    });
  });
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
