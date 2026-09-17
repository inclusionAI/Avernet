import type { AddressInfo } from "node:net";
import express from "express";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AisExecutor } from "../../contracts/ais-executor.js";
import type { EvolveRepository, EvolveTaskRow } from "../../repositories/evolve-repository.js";
import type { ObjectStore } from "../../services/object-storage/oss-object-store.js";
import { createSessionAnalysisRouter } from "../session-analysis.js";

const servers: Array<ReturnType<ReturnType<typeof express>["listen"]>> = [];

afterEach(async () => {
  await Promise.all(servers.splice(0).map(server => new Promise<void>((resolve, reject) => {
    server.close(error => error ? reject(error) : resolve());
  })));
});

async function postJson(router: express.Router, path: string, body: unknown) {
  const app = express();
  app.use(express.json());
  app.use(router);
  const server = app.listen(0);
  servers.push(server);
  await new Promise<void>(resolve => server.once("listening", resolve));
  const { port } = server.address() as AddressInfo;
  return fetch(`http://127.0.0.1:${port}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Staff-Id": "160855" },
    body: JSON.stringify(body),
  });
}

describe("session analysis Router", () => {
  it("dispatches the compact AIS Base task and preserves the no-LLM choice", async () => {
    let task: EvolveTaskRow | undefined;
    const repo = {
      resolveAccessibleEvolveBotRuntime: vi.fn().mockResolvedValue({
        ownerId: "160855", runtime: { activeEngine: "openclaw" },
      }),
      createTask: vi.fn(async input => {
        task = {
          id: 1,
          task_id: input.taskId,
          task_type: input.taskType,
          task_name: input.taskName,
          user_id: input.userId,
          bot_id: input.botId,
          created_by: input.createdBy,
          remark: input.remark ?? null,
          config_json: input.configJson,
          status: "pending",
          error_message: null,
          gmt_create: "2026-09-17T00:00:00Z",
          gmt_modified: "2026-09-17T00:00:00Z",
        } as EvolveTaskRow;
      }),
      createStep: vi.fn(),
      findTask: vi.fn(async () => task),
      markExternalDispatched: vi.fn(),
      markDispatchFailed: vi.fn(),
    } as unknown as EvolveRepository;
    const execute = vi.fn(async () => "ais-job-1");
    const ais = {
      execute,
      getJobStatus: vi.fn(),
      getJobStatusDetail: vi.fn(),
      stopExecution: vi.fn(),
    } satisfies AisExecutor;
    const store = {
      getObject: vi.fn(),
      createSignedUrl: vi.fn(),
    } as unknown as ObjectStore;
    const router = createSessionAnalysisRouter({
      repo, uploadStore: store, ais,
      aisOptions: {
        legacySnapshotId: 1,
        deadlineSeconds: 7200,
        deployment: { snapshotId: 17, packageId: "clawevolve-ais-diagnose" },
      },
      publicBaseUrl: "http://localhost:3001",
      artifactUploadMode: "none",
      environmentLabel: "test",
    });

    const response = await postJson(router, "/", {
      taskName: "只导出目标 Session",
      botId: "20260629_g3xxlxd3",
      mode: "ANALYZE_SINGLE",
      sessionId: "8831abfd-5ab9-43a9-8df2-114074125c4a",
      llmAnalysis: false,
    });
    expect(response.status).toBe(202);
    expect(await response.json()).toEqual(expect.objectContaining({
      status: "running", aisJobId: "ais-job-1",
    }));
    expect(execute).toHaveBeenCalledTimes(1);
    const [userId, globalParams, snapshotId] = execute.mock.calls[0];
    expect(userId).toBe("160855");
    expect(snapshotId).toBe(17);
    const payload = JSON.parse(globalParams["${clawevolve_params}"]);
    expect(payload).toEqual(expect.objectContaining({
      taskType: "session_analysis",
      attempt: 1,
      input: expect.objectContaining({
        sessionId: "8831abfd-5ab9-43a9-8df2-114074125c4a",
        llmAnalysis: false,
      }),
      runtime: expect.objectContaining({
        package: { packageId: "clawevolve-ais-diagnose" },
      }),
    }));
  });

  it("accepts one verified success callback for the current attempt", async () => {
    const artifacts = Object.fromEntries([
      "raw", "manifest", "report", "analysis", "result", "trajectory", "trajectoryPath",
      "runtimeBundle", "openclawSessions",
    ].map(name => [name, { objectKey: `evolution/SA-1/${name}` }]));
    const task = {
      task_id: "SA-1",
      task_type: "session_analysis",
      config_json: JSON.stringify({
        taskId: "SA-1",
        stepId: "SA-1-AIS",
        attempt: 1,
        mode: "ANALYZE_SINGLE",
        aisBase: { snapshotId: 17, packageId: "clawevolve-ais-diagnose", deadlineAt: 10_000 },
        artifacts,
      }),
    } as EvolveTaskRow;
    const applySessionAisStatus = vi.fn().mockResolvedValue(true);
    const repo = {
      findTask: vi.fn().mockResolvedValue(task),
      findStep: vi.fn().mockResolvedValue({
        task_id: "SA-1", step_id: "SA-1-AIS", status: "running",
      }),
      applySessionAisStatus,
    } as unknown as EvolveRepository;
    const store = { getObject: vi.fn(), createSignedUrl: vi.fn() } as unknown as ObjectStore;
    const ais = {
      execute: vi.fn(), getJobStatus: vi.fn(), getJobStatusDetail: vi.fn(), stopExecution: vi.fn(),
    } satisfies AisExecutor;
    const router = createSessionAnalysisRouter({
      repo, uploadStore: store, ais,
      aisOptions: {
        legacySnapshotId: 1, deadlineSeconds: 7200,
        deployment: { snapshotId: 17, packageId: "clawevolve-ais-diagnose" },
      },
      publicBaseUrl: "http://localhost:3001",
      artifactUploadMode: "none",
      environmentLabel: "test",
    });
    const optional = new Set(["trajectory", "trajectoryPath"]);
    const contentType = (name: string) => ["raw", "trajectory"].includes(name)
      ? "application/x-ndjson"
      : name === "report" ? "text/markdown; charset=utf-8"
        : ["runtimeBundle", "openclawSessions"].includes(name) ? "application/gzip" : "application/json";
    const uploaded = Object.fromEntries(Object.entries(artifacts)
      .filter(([name]) => !optional.has(name))
      .map(([name, item]) => [name, {
        ...item, size: 10, sha256: "a".repeat(64), contentType: contentType(name),
      }]));
    const response = await postJson(router, "/internal/SA-1/steps/SA-1-AIS/report", {
      status: "succeeded",
      summary: "会话诊断完成",
      output: { taskId: "SA-1", analysisId: "SA-1", success: true, artifacts: uploaded },
    });
    expect(response.status).toBe(200);
    expect(applySessionAisStatus).toHaveBeenCalledWith("SA-1", "SA-1-AIS", expect.objectContaining({
      status: "succeeded",
      summary: "会话诊断完成",
    }));
  });
});
