import express from "express";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EvolveRepository, EvolveStepRow, EvolveTaskRow } from "../../repositories/evolve-repository.js";
import { createEvolveRouter } from "../evolve.js";

const taskId = "SA-1";
const stepId = "SA-1-AIS";
const config = {
  aisBase: { packageId: "clawevolve-ais-diagnose" }, stepId,
  artifacts: {
    result: {
      objectKey: "evolution/SA-1/result.json", contentType: "application/json",
      requiredOnSuccess: true, allowEmpty: false,
    },
  },
};

describe("ClawEvolve AIS Base transport", () => {
  let server: ReturnType<express.Application["listen"]> | undefined;

  afterEach(async () => {
    if (server) await new Promise<void>(resolve => server!.close(() => resolve()));
    server = undefined;
  });

  async function start(status = "running", options: { handlerName?: string; handler?: import("../../services/ais/status-handler.js").AisStatusHandler; fixedContract?: boolean } = {}) {
    const applyAisStatus = vi.fn().mockResolvedValue(true);
    const createSignedUrl = vi.fn().mockResolvedValue("https://oss.example/legacy-signed");
    const createAisSignedUrl = vi.fn().mockResolvedValue("https://oss.example/ais-signed");
    const repo = {
      findTask: vi.fn().mockResolvedValue({ task_id: taskId, config_json: JSON.stringify({ ...config, ...(options.fixedContract ? { stepId: "new-execution" } : {}), aisBase: { ...config.aisBase, ...(options.handlerName ? { statusHandler: options.handlerName } : {}) } }) } as EvolveTaskRow),
      findStep: vi.fn().mockResolvedValue({
        task_id: taskId, step_id: stepId, step_type: "session_ais", status,
        ...(options.fixedContract ? { ext_json: JSON.stringify({ aisExecution: { ...config, aisBase: { statusHandler: "custom" } } }) } : {}),
      } as EvolveStepRow),
      applyAisStatus,
    } as unknown as EvolveRepository;
    const app = express();
    app.use(express.json());
    app.use("/api/evolve", createEvolveRouter(repo, {
      aisStatusHandlers: options.handler ? { custom: options.handler } : undefined,
      artifactUrlStore: { createSignedUrl } as never,
      aisArtifactUrlStore: { createSignedUrl: createAisSignedUrl } as never,
    }));
    server = await new Promise(resolve => {
      const instance = app.listen(0, () => resolve(instance));
    });
    const port = (server.address() as { port: number }).port;
    return {
      base: `http://127.0.0.1:${port}/api/evolve`, applyAisStatus,
      createSignedUrl, createAisSignedUrl,
    };
  }

  it("issues an upload URL only for the frozen artifact contract", async () => {
    const { base, createSignedUrl, createAisSignedUrl } = await start();
    const response = await fetch(`${base}/internal/tasks/${taskId}/steps/${stepId}/artifacts/upload-url`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        executor: "ais", artifactName: "result", size: 10,
        sha256: "a".repeat(64), contentType: "application/json",
      }),
    });
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ method: "PUT", objectKey: "evolution/SA-1/result.json" });
    expect(createAisSignedUrl).toHaveBeenCalledWith(
      "evolution/SA-1/result.json", "PUT", 86_400, { "Content-Type": "application/json" },
    );
    expect(createSignedUrl).not.toHaveBeenCalled();
  });

  it("selects the AIS upload contract from the explicit executor", async () => {
    const { base, createSignedUrl, createAisSignedUrl } = await start();
    const response = await fetch(`${base}/internal/tasks/${taskId}/steps/${stepId}/artifacts/upload-url`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ executor: "ais", size: 10,
        sha256: "a".repeat(64), contentType: "application/json" }),
    });
    expect(response.status).toBe(422);
    expect(await response.json()).toMatchObject({ error: "Artifact 名称不合法" });
    expect(createAisSignedUrl).not.toHaveBeenCalled();
    expect(createSignedUrl).not.toHaveBeenCalled();
  });

  it("validates and atomically applies the AIS terminal callback", async () => {
    const { base, applyAisStatus } = await start();
    const output = { taskId, success: true, artifacts: { result: {
      objectKey: "evolution/SA-1/result.json", size: 10,
      sha256: "a".repeat(64), contentType: "application/json",
    } } };
    const response = await fetch(`${base}/internal/tasks/${taskId}/steps/${stepId}/report`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "succeeded", summary: "done", output }),
    });
    expect(response.status).toBe(200);
    expect(applyAisStatus).toHaveBeenCalledWith(taskId, stepId, {
      status: "succeeded", summary: "done", output,
    });
  });
  it("dispatches execution failure without applying the default business terminal state", async () => {
    const handler = vi.fn().mockResolvedValue({ applied: true });
    const { base, applyAisStatus } = await start("running", { handlerName: "custom", handler });
    const response = await fetch(`${base}/internal/tasks/${taskId}/steps/${stepId}/report`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "failed", error: { code: "TASK_TIMEOUT", message: "timeout" } }),
    });
    expect(response.status).toBe(200);
    expect(handler).toHaveBeenCalledWith(expect.objectContaining({
      task: expect.objectContaining({ task_id: taskId }),
      step: expect.objectContaining({ step_id: stepId }),
      report: expect.objectContaining({ status: "failed", errorCode: "TASK_TIMEOUT" }),
    }));
    expect(applyAisStatus).not.toHaveBeenCalled();
  });

  it("fails closed when a configured handler is unavailable", async () => {
    const { base, applyAisStatus } = await start("running", { handlerName: "custom" });
    const response = await fetch(`${base}/internal/tasks/${taskId}/steps/${stepId}/report`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: "failed" }),
    });
    expect(response.status).toBe(503);
    expect(applyAisStatus).not.toHaveBeenCalled();
  });

  it("validates artifacts before invoking a custom handler", async () => {
    const handler = vi.fn().mockResolvedValue({ applied: true });
    const { base } = await start("running", { handlerName: "custom", handler });
    const response = await fetch(`${base}/internal/tasks/${taskId}/steps/${stepId}/report`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "succeeded", output: { taskId: "wrong", success: true, artifacts: {} } }),
    });
    expect(response.status).toBe(422);
    expect(handler).not.toHaveBeenCalled();
  });

  it("uses an old execution's frozen contract after the task starts a newer execution", async () => {
    const handler = vi.fn().mockResolvedValue({ applied: true });
    const { base, createAisSignedUrl, applyAisStatus } = await start("running", { fixedContract: true, handler });
    const upload = await fetch(`${base}/internal/tasks/${taskId}/steps/${stepId}/artifacts/upload-url`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ executor: "ais", artifactName: "result", size: 10, sha256: "a".repeat(64), contentType: "application/json" }),
    });
    expect(upload.status).toBe(200); expect(createAisSignedUrl).toHaveBeenCalled();
    const report = await fetch(`${base}/internal/tasks/${taskId}/steps/${stepId}/report`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: "failed", summary: "old execution timeout" }),
    });
    expect(report.status).toBe(200); expect(handler).toHaveBeenCalledOnce(); expect(applyAisStatus).not.toHaveBeenCalled();
  });

});
