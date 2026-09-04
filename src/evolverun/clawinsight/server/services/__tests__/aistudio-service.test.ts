import { describe, expect, it, vi } from "vitest";
import { AistudioService, DEFAULT_AISTUDIO_PROJECT_NAME, resolveAistudioConfig, SESSION_ANALYSIS_SNAPSHOT_ID } from "../aistudio-service.js";

describe("AistudioService", () => {
  it("defaults only non-secret AIStudio configuration", () => {
    const config = resolveAistudioConfig({});
    expect(config.projectName).toBe(DEFAULT_AISTUDIO_PROJECT_NAME);
    expect(config.snapshotId).toBe(62310015);
    expect(config.token).toBe("");
  });

  it("accepts an AIStudio token only from the deployment environment", () => {
    expect(resolveAistudioConfig({ CLAWWEB_AISTUDIO_TOKEN: "mist-value" }).token).toBe("mist-value");
  });

  it("uses the Java executeSnapshot JSON contract", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ success: true, data: { jobId: "123" } }), { status: 200 }));
    const service = new AistudioService({ token: "secret", projectName: "project", snapshotId: SESSION_ANALYSIS_SNAPSHOT_ID }, fetcher as typeof fetch);
    await expect(service.execute("197444", { "${clawevolve_params}": "{}" })).resolves.toBe("123");
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toContain("/executeSnapshot");
    expect(JSON.parse(String(init?.body))).toEqual({ userNumber: "197444", token: "secret", snapshotId: 62310015,
      userGlobalParameter: { "${clawevolve_params}": "{}" }, inputParams: null, projectName: "project" });
  });

  it("preserves the standard AIStudio dispatch failure envelope", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({
      success: false,
      errorCode: "DMS51001",
      errorMessage: "invalid user number",
    }), { status: 200 }));
    const service = new AistudioService(
      { token: "secret", projectName: "project", snapshotId: SESSION_ANALYSIS_SNAPSHOT_ID },
      fetcher as typeof fetch,
    );

    await expect(service.execute("invalid-user", { "${clawevolve_params}": "{}" }))
      .rejects.toThrow('AIS executeSnapshot 失败: {"success":false,"errorCode":"DMS51001","errorMessage":"invalid user number"}');
  });

  it("maps AIStudio execution statuses", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ success: true, data: { executeStatus: { status: "success" } } }), { status: 200 }));
    const service = new AistudioService({ token: "secret", projectName: "project", snapshotId: 62310015 }, fetcher as typeof fetch);
    await expect(service.getJobStatus("123")).resolves.toBe("success");
    expect(String(fetcher.mock.calls[0][0])).toContain("getAlgoJobStatusAndResult");
  });

  it("uses the AIStudio stopExecution GET contract", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ success: true }), { status: 200 }));
    const service = new AistudioService({ token: "secret", projectName: "project", snapshotId: 62310015 }, fetcher as typeof fetch);

    await expect(service.stopExecution("123")).resolves.toBeUndefined();

    const [request, init] = fetcher.mock.calls[0];
    const url = new URL(String(request));
    expect(url.pathname).toBe("/api/v2.0/guiApi/stopExecution");
    expect(url.searchParams.get("recordId")).toBe("123");
    expect(url.searchParams.get("token")).toBe("secret");
    expect(init?.method).toBe("GET");
  });

  it("keeps queued jobs in the running lifecycle", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ success: true, data: { executeStatus: { status: "queued" } } }), { status: 200 }));
    const service = new AistudioService({ token: "secret", projectName: "project", snapshotId: SESSION_ANALYSIS_SNAPSHOT_ID }, fetcher as typeof fetch);
    await expect(service.getJobStatusDetail("123")).resolves.toEqual({
      status: "running", rawStatus: "queued", errorMessage: null,
    });
  });

  it("preserves the AIStudio failure reason", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({
      success: true,
      data: { executeStatus: { status: "failed", errorMessage: "main process return code: 2" } },
    }), { status: 200 }));
    const service = new AistudioService({ token: "secret", projectName: "project", snapshotId: 62310015 }, fetcher as typeof fetch);
    await expect(service.getJobStatusDetail("123")).resolves.toEqual({
      status: "failed", rawStatus: "failed", errorMessage: "main process return code: 2",
    });
  });
});
