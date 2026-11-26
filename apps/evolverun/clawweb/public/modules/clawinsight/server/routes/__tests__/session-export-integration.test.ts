// @vitest-environment node
import Database from "better-sqlite3";
import express from "express";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { runMigrations, SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import {
  createSessionExportIntegrationRouter,
} from "../session-export-integration.js";

import type { SessionAisOptions } from "@avernet/clawevolve/server/contracts/ais-executor";
import { createSessionAnalysisRouter, validateAisResult } from "@avernet/clawevolve/server/routes/session-analysis";
import { createEvolveRouter } from "@avernet/clawevolve/server/routes/evolve";
import { parseAisArtifactContract, validateAisArtifactRequest } from "@avernet/clawevolve/server/services/ais/ais-artifact-contract";

let aisOptions: SessionAisOptions;
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
      device_props TEXT,
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
      entity_type TEXT NOT NULL DEFAULT 'staff',
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
  aisOptions = { legacySnapshotId: 1, deadlineSeconds: 7200 };
  const app = express();
  app.use(express.json());
  app.use("/api/integrations/v1/session-exports", createSessionExportIntegrationRouter({
    repo,
    ais: { execute },
    aisOptions,
    officeDownloadStore: { createSignedUrl: officeSignedUrl },
    productionDownloadStore: { createSignedUrl: productionSignedUrl },
    now: () => Date.parse("2026-08-20T02:10:00Z"),
  }));
  app.use("/api/session-analyses", (req, _res, next) => { req.isClawEvolveAdmin = true; next(); },
    createSessionAnalysisRouter({ repo, aisOptions, publicBaseUrl: "http://localhost:3001",
      artifactUploadMode: "broker",
      ais: { execute, getJobStatus: vi.fn(), getJobStatusDetail: vi.fn(), stopExecution: vi.fn() },
      uploadStore: { createSignedUrl: officeSignedUrl } as never,
    }));
  app.use("/api/evolve", createEvolveRouter(repo, {
    aisArtifactUrlStore: { createSignedUrl: officeSignedUrl } as never,
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
  it.each([[17, "bot"], [29, "bot"], [17, "single"], [29, "single"]] as const)("uses configured Snapshot %s for %s exports with Base uploads and callbacks", async (snapshotId, exportScope) => {
    aisOptions.deployment = { snapshotId, packageId: "clawevolve-ais-diagnose" };
    const response = await request("/api/integrations/v1/session-exports", {
      method: "POST", headers: { "Idempotency-Key": "configured-export-0001" },
      body: JSON.stringify({ exportScope, ...(exportScope === "single" ? { sessionIdentifier: "session-1" } : {}),
        target: { userId: "197444", botId: "bot-1", stage: "service" } }),
    });
    expect(response.status).toBe(202);
    const { exportId } = await response.json();
    const config = JSON.parse((await repo.findTask(exportId))!.config_json);
    expect(execute.mock.calls[0][2]).toBe(snapshotId);
    const envelope = JSON.parse(execute.mock.calls[0][1]["${clawevolve_params}"]);
    expect(envelope).toMatchObject({ taskType: "session_export", taskId: exportId,
      input: { userId: "197444", botId: "bot-1", stage: "service", llmAnalysis: false },
      runtime: { package: { packageId: "clawevolve-ais-diagnose" } } });
    expect(envelope.runtime).not.toHaveProperty("outputDir");
    const contract = parseAisArtifactContract(config)!;
    expect(contract).not.toBeNull();
    const artifacts = Object.fromEntries(Object.entries(contract.artifacts).map(([name, spec]) => {
      const meta = { objectKey: spec.objectKey, contentType: spec.contentType, size: 10, sha256: "a".repeat(64) };
      expect(() => validateAisArtifactRequest(contract, name, meta)).not.toThrow();
      return [name, meta];
    }));
    expect(artifacts).toHaveProperty("runtimeBundle");
    expect(() => validateAisResult({ taskId: exportId, success: true, artifacts }, config)).not.toThrow();
    const { raw, ...withoutRaw } = artifacts;
    expect(() => validateAisResult({ taskId: exportId, success: true, artifacts: withoutRaw }, config)).toThrow();
    const path = `/api/evolve/internal/tasks/${exportId}/steps/${config.stepId}`;
    const upload = await request(`${path}/artifacts/upload-url`, { method: "POST",
      body: JSON.stringify({ executor: "ais", artifactName: "raw", ...raw }) });
    expect(upload.status).toBe(200);
    const report = await request(`${path}/report`, { method: "POST", body: JSON.stringify({
      status: "succeeded", output: { taskId: exportId, success: true, sessionIds: ["session-1"], artifacts },
    }) });
    expect(report.status).toBe(200);
    const view = await request(`/api/integrations/v1/session-exports/${exportId}`);
    expect(await view.json()).toMatchObject({ status: "succeeded",
      artifact: { contentType: exportScope === "single" ? "application/x-ndjson" : "application/gzip" },
      resolution: { resolvedSessionIds: ["session-1"] } });
  });

  it("moves a failed legacy export to Base on retry and retains that selection on later retries", async () => {
    const response = await request("/api/integrations/v1/session-exports", {
      method: "POST", headers: { "Idempotency-Key": "legacy-export-retry-0001" },
      body: JSON.stringify({ exportScope: "single", sessionIdentifier: "session-1",
        target: { userId: "197444", botId: "bot-1", stage: "service" } }),
    });
    const { exportId } = await response.json();
    expect(execute.mock.calls[0][2]).toBe(1);
    await repo.markDispatchFailed(`${exportId}-AIS`, "old runtime");
    aisOptions.deployment = { snapshotId: 17, packageId: "clawevolve-ais-diagnose" };
    for (const attempt of [2, 3]) {
      const retry = await request(`/api/session-analyses/${exportId}/retry`, {
        method: "POST", headers: { "X-Staff-Id": "operator" }, body: "{}",
      });
      expect(retry.status).toBe(202);
      const cfg = JSON.parse((await repo.findTask(exportId))!.config_json);
      expect(cfg.aisBase.snapshotId).toBe(17);
      expect(cfg.stepId).toBe(`${exportId}-AIS-${attempt}`);
      expect(cfg.artifacts.raw.contentType).toBe("application/x-ndjson");
      expect(cfg.artifacts.runtimeBundle.objectKey).toContain(`/attempt-${attempt}/`);
      expect(execute.mock.calls.at(-1)![2]).toBe(17);
      const payload = JSON.parse(execute.mock.calls.at(-1)![1]["${clawevolve_params}"]);
      expect(payload.runtime.package.packageId).toBe("clawevolve-ais-diagnose");
      expect(payload.input.userId).toBe("197444");
      await repo.markDispatchFailed(cfg.stepId, "retry fixture");
      aisOptions.deployment.snapshotId = 29;
    }
  });

  it("rejects invalid deployment before creating or dispatching an export", async () => {
    aisOptions.configurationError = "SESSION_AIS_CONFIG_INVALID";
    const response = await request("/api/integrations/v1/session-exports", {
      method: "POST", headers: { "Idempotency-Key": "invalid-config-0001" },
      body: JSON.stringify({ exportScope: "bot", target: { userId: "197444", botId: "bot-1", stage: "service" } }),
    });
    expect(response.status).toBe(503);
    expect(execute).not.toHaveBeenCalled();
  });

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
