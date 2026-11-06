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
      "artifactBundle", "runtimeBundle", "openclawSessions",
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
    const applyAisStatus = vi.fn().mockResolvedValue(true);
    const repo = {
      findTask: vi.fn().mockResolvedValue(task),
      findStep: vi.fn().mockResolvedValue({
        task_id: "SA-1", step_id: "SA-1-AIS", status: "running",
      }),
      applyAisStatus,
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
        : ["artifactBundle", "runtimeBundle", "openclawSessions"].includes(name) ? "application/gzip" : "application/json";
    const uploaded = Object.fromEntries(Object.entries(artifacts)
      .filter(([name]) => !optional.has(name))
      .map(([name, item]) => [name, {
        ...item, size: 10, sha256: "a".repeat(64), contentType: contentType(name),
      }]));
    const response = await postJson(router, "/internal/SA-1/steps/SA-1-AIS/report", {
      status: "succeeded",
      summary: "会话诊断完成",
      output: { taskId: "SA-1", success: true, artifacts: uploaded },
    });
    expect(response.status).toBe(200);
    expect(applyAisStatus).toHaveBeenCalledWith("SA-1", "SA-1-AIS", expect.objectContaining({
      status: "succeeded",
      summary: "会话诊断完成",
    }));
  });
});

describe("session preview availability", () => {
  it.each([
    { name: "small raw without Agent analysis", uploaded: { raw: { size: 10 } }, expected: null },
    { name: "exact size limit", uploaded: { raw: { size: 2097152 } }, expected: null },
    { name: "large raw", uploaded: { raw: { size: 2097153 } }, expected: "2 MiB" },
    { name: "unknown size", uploaded: { raw: {} }, expected: "大小信息不可用" },
    { name: "trajectory only", uploaded: { trajectory: { size: 10 } }, expected: "仅提供运行轨迹" },
    { name: "no evidence", uploaded: {}, expected: "未提供原始 Session" },
    { name: "upload disabled", uploaded: { raw: { size: 10 } }, uploadMode: "none", expected: "未启用会话文件上传" },
    { name: "unsupported format", uploaded: { raw: { size: 10 } }, key: "raw.zip", expected: "格式不支持" },
  ])("$name", async ({ uploaded, expected, uploadMode, key }) => {
    const task = {
      task_id: "SA-preview", task_type: "session_analysis", user_id: "160855",
      created_by: "160855", status: "completed",
      config_json: JSON.stringify({
        mode: "ANALYZE_SINGLE", llmAnalysis: false, artifactUploadMode: uploadMode ?? "broker",
        aisBase: { snapshotId: 17, packageId: "diagnose", deadlineAt: 100 },
        artifacts: { raw: { objectKey: key ?? "raw.jsonl" }, report: { objectKey: "report.md" } },
      }),
    } as EvolveTaskRow;
    const repo = {
      findTask: vi.fn().mockResolvedValue(task),
      listSteps: vi.fn().mockResolvedValue([{ output_json: JSON.stringify({ artifacts: { ...uploaded, report: {} } }) }]),
      listEvolveBots: vi.fn().mockResolvedValue([]),
    } as unknown as EvolveRepository;
    const getObject = vi.fn(async (objectKey: string) => ({
      content: Buffer.from(objectKey === "report.md" ? "# Report" : '{"type":"message"}\n'),
    }));
    const app = express();
    app.use(createSessionAnalysisRouter({
      repo, uploadStore: { getObject } as unknown as ObjectStore,
      ais: {} as AisExecutor,
      aisOptions: { legacySnapshotId: 1, deadlineSeconds: 7200 },
      publicBaseUrl: "http://localhost:3001", artifactUploadMode: "broker",
    }));
    const server = app.listen(0);
    servers.push(server);
    await new Promise<void>(resolve => server.once("listening", resolve));
    const response = await fetch(`http://127.0.0.1:${(server.address() as AddressInfo).port}/SA-preview`, {
      headers: { "X-Staff-Id": "160855" },
    });
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.llmAnalysis).toBe(false);
    expect(body.reportMarkdown).toBe(uploadMode === "none" ? null : "# Report");
    if (expected) {
      expect(body.sessionPreview).toBeNull();
      expect(body.sessionPreviewUnavailableReason).toContain(expected);
      expect(getObject.mock.calls.every(([key]) => key === "report.md")).toBe(true);
    } else {
      expect(body.sessionPreview.eventCount).toBe(1);
      expect(body.sessionPreviewUnavailableReason).toBeNull();
    }
  });
});
