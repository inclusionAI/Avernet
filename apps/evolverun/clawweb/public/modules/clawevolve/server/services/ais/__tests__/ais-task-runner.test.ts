import { describe, expect, it, vi } from "vitest";
import type { EvolveRepository } from "../../../repositories/evolve-repository.js";
import { AisTaskRunner, type AisTaskDefinition } from "../ais-task-runner.js";

type Config = { artifacts: Record<string, { objectKey: string; contentType?: string }> };

function runner(createSignedUrl: ReturnType<typeof vi.fn>) {
  const definition: AisTaskDefinition<Config> = {
    taskTypes: ["test"],
    snapshotId: 1,
    buildGlobalParams: (_config, uploads) => ({ payload: JSON.stringify(uploads) }),
  };
  return new AisTaskRunner(
    {} as EvolveRepository,
    { createSignedUrl },
    { execute: vi.fn() },
    definition,
  );
}

describe("AisTaskRunner", () => {
  it("creates a signed upload with the declared Content-Type", async () => {
    const createSignedUrl = vi.fn(async () => "https://oss.example/typed");
    const params = await runner(createSignedUrl).prepare({
      artifacts: { result: { objectKey: "evolution/task/result.json", contentType: "application/json" } },
    });
    expect(createSignedUrl).toHaveBeenCalledWith(
      "evolution/task/result.json", "PUT", 86_400, { "Content-Type": "application/json" },
    );
    expect(JSON.parse(params.payload)).toEqual({ result: {
      objectKey: "evolution/task/result.json", contentType: "application/json",
      putUrl: "https://oss.example/typed",
    } });
  });

  it("uses the task-selected Snapshot and records the external job", async () => {
    type RoutedConfig = Config & { environment: "pre" | "prod" };
    const execute = vi.fn(async () => "job-1");
    const markExternalDispatched = vi.fn(async () => undefined);
    const routed = new AisTaskRunner(
      { markExternalDispatched } as unknown as EvolveRepository,
      { createSignedUrl: vi.fn() },
      { execute, jobUrl: jobId => `https://ais.example/jobs/${jobId}` },
      {
        taskTypes: ["session_analysis"],
        snapshotId: config => config.environment === "pre" ? 11 : 22,
        artifactTransport: "none",
        buildGlobalParams: config => ({ environment: config.environment }),
      } satisfies AisTaskDefinition<RoutedConfig>,
    );
    await routed.dispatch({
      task_type: "session_analysis",
      config_json: JSON.stringify({ artifacts: {}, environment: "prod" }),
    } as never, "step-1", "user-1");
    expect(execute).toHaveBeenCalledWith("user-1", { environment: "prod" }, 22);
    expect(markExternalDispatched).toHaveBeenCalledWith(
      "step-1", "job-1", expect.objectContaining({
        snapshotId: 22,
        jobUrl: "https://ais.example/jobs/job-1",
      }),
    );
  });
});
