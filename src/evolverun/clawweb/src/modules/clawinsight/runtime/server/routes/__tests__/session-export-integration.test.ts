import Database from "better-sqlite3";
import express from "express";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { runMigrations, SqliteDatabase } from "../../db.js";
import { EvolveRepository } from "../../repositories/evolve-repository.js";
import {
  createSessionExportIntegrationRouter,
} from "../session-export-integration.js";

let db: SqliteDatabase;
let repo: EvolveRepository;
let server: ReturnType<express.Application["listen"]> | null;
let baseUrl: string;
const execute = vi.fn();
const officeSignedUrl = vi.fn();
const productionSignedUrl = vi.fn();

async function request(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  return fetch(`${baseUrl}${path}`, {
    ...init,
    headers,
  });
}

beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  await db.exec(
    `CREATE TABLE ac_entity_device_binding (
      id INTEGER PRIMARY KEY,
      device_provider TEXT,
      device_id TEXT,
      status TEXT,
      env TEXT
    )`,
  );
  await db.exec(
    `CREATE TABLE ac_bots (
      id INTEGER PRIMARY KEY,
      bot_id TEXT NOT NULL,
      bot_name TEXT,
      owner_id TEXT,
      entity_id TEXT,
      is_delete INTEGER NOT NULL DEFAULT 0,
      active_engine TEXT,
      bot_type TEXT,
      status TEXT,
      binding_id INTEGER,
      env TEXT
    )`,
  );
  await db.exec(
    `INSERT INTO ac_bots
      (id, bot_id, bot_name, owner_id, entity_id, active_engine, bot_type, status, env)
     VALUES (1, 'bot-1', '测试 Bot', '197444', '197444', 'openclaw', 'service', 'active', 'prod')`,
  );
  repo = new EvolveRepository(db);
  execute.mockReset();
  execute.mockResolvedValue("331000001");
  officeSignedUrl.mockReset();
  officeSignedUrl.mockResolvedValue("https://office-oss.example/session");
  productionSignedUrl.mockReset();
  productionSignedUrl.mockResolvedValue("https://production-oss.example/session");
  const app = express();
  app.use(express.json());
  app.use("/api/integrations/v1/session-exports", createSessionExportIntegrationRouter({
    repo,
    ais: { execute },
    officeDownloadStore: { createSignedUrl: officeSignedUrl },
    productionDownloadStore: { createSignedUrl: productionSignedUrl },
    now: () => Date.parse("2026-08-20T02:10:00Z"),
  }));
  const started = await new Promise<ReturnType<express.Application["listen"]>>((resolve) => {
    const instance = app.listen(0, () => resolve(instance));
  });
  server = started;
  baseUrl = `http://127.0.0.1:${(started.address() as { port: number }).port}`;
});

afterEach(async () => {
  const active = server;
  server = null;
  if (active) await new Promise<void>((resolve) => active.close(() => resolve()));
  await db.close();
});

describe("Session Export Integration API", () => {
  it("is public and returns not found for an unknown export", async () => {
    const response = await fetch(`${baseUrl}/api/integrations/v1/session-exports/missing`);
    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({
      code: "EXPORT_NOT_FOUND",
      message: "导出任务不存在",
    });
  });

  it("creates a single export and keeps creation idempotent", async () => {
    const body = {
      exportScope: "single",
      target: { userId: "197444", botId: "bot-1", stage: "all", engineType: "openclaw" },
      sessionIdentifier: "session-key-or-id",
      requestName: "CASE-123 Session 导出",
    };
    const headers = { "Idempotency-Key": "case-123-request-0001" };
    const first = await request("/api/integrations/v1/session-exports", {
      method: "POST", headers, body: JSON.stringify(body),
    });
    expect(first.status).toBe(202);
    const firstPayload = await first.json() as { exportId: string; status: string };
    expect(firstPayload).toMatchObject({ status: "dispatched" });

    const repeated = await request("/api/integrations/v1/session-exports", {
      method: "POST", headers, body: JSON.stringify(body),
    });
    expect(repeated.status).toBe(202);
    expect((await repeated.json() as { exportId: string }).exportId).toBe(firstPayload.exportId);
    expect(execute).toHaveBeenCalledTimes(1);

    const task = await repo.findTask(firstPayload.exportId);
    expect(task).not.toBeNull();
    expect(task?.task_type).toBe("session_export");
    expect(task?.created_by).toBe("integration:public");
    const config = JSON.parse(task?.config_json ?? "{}") as Record<string, unknown>;
    expect(config).toMatchObject({
      source: "integration_api",
      mode: "EXPORT_SINGLE",
      exportScope: "single",
      sessionIdentifier: "session-key-or-id",
    });
    const globalParams = execute.mock.calls[0][1] as Record<string, string>;
    const envelope = JSON.parse(globalParams["${clawevolve_params}"]) as Record<string, unknown>;
    expect(envelope).toMatchObject({
      taskType: "session_export",
      execution: { executor: "ais", action: "package" },
      input: { userId: "197444", botId: "bot-1", sessionIdentifier: "session-key-or-id" },
    });

    const conflict = await request("/api/integrations/v1/session-exports", {
      method: "POST",
      headers,
      body: JSON.stringify({ ...body, sessionIdentifier: "different-session" }),
    });
    expect(conflict.status).toBe(409);
    expect(await conflict.json()).toMatchObject({ code: "IDEMPOTENCY_CONFLICT" });
  });

  it("returns a new office-network OSS URL for a completed single export", async () => {
    const create = await request("/api/integrations/v1/session-exports", {
      method: "POST",
      headers: { "Idempotency-Key": "completed-case-request-01" },
      body: JSON.stringify({
        exportScope: "single",
        target: { userId: "197444", botId: "bot-1", stage: "all" },
        sessionIdentifier: "session-key",
      }),
    });
    const { exportId } = await create.json() as { exportId: string };
    const task = await repo.findTask(exportId);
    const config = JSON.parse(task?.config_json ?? "{}") as {
      stepId: string;
      artifacts: { raw: { objectKey: string } };
    };
    await repo.updateStepStatus(config.stepId, {
      status: "succeeded",
      output: {
        success: true,
        sessionIds: ["resolved-session-id"],
        artifacts: {
          raw: {
            objectKey: config.artifacts.raw.objectKey,
            size: 198650,
            sha256: "a".repeat(64),
            contentType: "application/x-ndjson",
          },
        },
      },
    });
    await repo.completeTask(exportId);

    const response = await request(`/api/integrations/v1/session-exports/${exportId}`);
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      exportId,
      status: "succeeded",
      phase: "completed",
      resolution: {
        inputType: "session_key",
        resolvedSessionIds: ["resolved-session-id"],
        fileCount: 1,
      },
      artifact: {
        filename: "resolved-session-id.jsonl",
        contentType: "application/x-ndjson",
        downloadUrl: "https://office-oss.example/session",
        downloadUrlExpiresAt: "2026-08-20T02:15:00.000Z",
      },
    });
    expect(officeSignedUrl).toHaveBeenCalledWith(config.artifacts.raw.objectKey, "GET", 300);
    expect(productionSignedUrl).not.toHaveBeenCalled();
  });

  it("creates a public Bot export without a Session identifier and selects the requested download network", async () => {
    const response = await request("/api/integrations/v1/session-exports", {
      method: "POST",
      headers: { "Idempotency-Key": "service-bot-export-001" },
      body: JSON.stringify({
        exportScope: "bot",
        target: { userId: "197444", botId: "bot-1", stage: "service" },
      }),
    });
    expect(response.status).toBe(202);
    const { exportId } = await response.json() as { exportId: string };
    const task = await repo.findTask(exportId);
    expect(JSON.parse(task?.config_json ?? "{}")).toMatchObject({
      mode: "EXPORT_ALL",
      exportScope: "bot",
      stage: "service",
    });
    const globalParams = execute.mock.calls[0][1] as Record<string, string>;
    const envelope = JSON.parse(globalParams["${clawevolve_params}"]) as {
      input: Record<string, unknown>;
    };
    expect(envelope.input).not.toHaveProperty("sessionIdentifier");
    expect(envelope.input).toMatchObject({ stage: "service", isServiceBot: true });

    const pending = await request(`/api/integrations/v1/session-exports/${exportId}`);
    expect(pending.status).toBe(200);
    expect(await pending.json()).toMatchObject({ status: "dispatched" });

    const config = JSON.parse(task?.config_json ?? "{}") as {
      stepId: string;
      artifacts: { raw: { objectKey: string } };
    };
    await repo.updateStepStatus(config.stepId, {
      status: "succeeded",
      output: {
        success: true,
        sessionIds: ["session-1", "session-2"],
        artifacts: {
          raw: {
            objectKey: config.artifacts.raw.objectKey,
            size: 42_000,
            sha256: "b".repeat(64),
            contentType: "application/gzip",
          },
        },
      },
    });
    await repo.completeTask(exportId);
    const completed = await request(
      `/api/integrations/v1/session-exports/${exportId}?downloadNetwork=production`,
    );
    expect(await completed.json()).toMatchObject({
      status: "succeeded",
      artifact: {
        filename: "197444-bot-1-service-sessions.tar.gz",
        downloadUrl: "https://production-oss.example/session",
      },
    });
    expect(productionSignedUrl).toHaveBeenCalledWith(config.artifacts.raw.objectKey, "GET", 300);
  });
});
