import { afterEach, describe, expect, it, vi } from "vitest";
import express from "express";
import JSZip from "jszip";
import { createStageSkillsRouter } from "../stage-skills.js";

const implementation = {
  id: 1,
  stage_skill_id: "STAGESKILL-1",
  implementation_id: "IMPL-1",
  owner_user_id: "user-1",
  display_name: "诊断预处理",
  stage_key: "diagnose",
  extension_mode: "preprocess",
  version_no: 1,
  status: "validated",
  package_ref: "oss://clawevolve-artifacts/implementation.zip",
  package_sha256: "a".repeat(64),
  static_validation_json: "{}",
  integration_test_task_id: null,
  gmt_create: 1,
  gmt_modified: 1,
} as const;

let server: ReturnType<express.Application["listen"]> | null = null;

afterEach(async () => {
  const active = server;
  server = null;
  if (active) await new Promise<void>((resolve) => active.close(() => resolve()));
});

async function startRouter(overrides: Record<string, unknown> = {}) {
  const steps = new Map<string, Record<string, unknown>>();
  const evolveRepo = {
    createTaskWithStep: vi.fn(async ({ step }: { step: Record<string, unknown> }) => {
      steps.set(String(step.stepId), { id: 1, step_id: step.stepId, step_type: step.stepType });
    }),
    createStep: vi.fn(async (step: Record<string, unknown>) => {
      const row = { id: 2, step_id: step.stepId, step_type: step.stepType };
      steps.set(String(step.stepId), row);
      return row;
    }),
    findStep: vi.fn(async (stepId: string) => steps.get(stepId) ?? null),
    resolveEvolveBotRuntime: vi.fn(async () => ({ env: "pre", provider: "arca" })),
    markDispatched: vi.fn(),
    markDispatchFailed: vi.fn(),
  };
  const stageRepo = {
    findImplementation: vi.fn(async () => implementation),
    createExtensionRun: vi.fn(),
    updateIntegrationTest: vi.fn(),
  };
  const skillAssetRepo = {
    findAsset: vi.fn(async () => ({
      asset_id: "SKILL-1", owner_user_id: "user-1", bot_id: "bot-1",
      ocb_skill_id: "local-skill", display_name: "local-skill",
    })),
  };
  const putObject = vi.fn(async () => ({ etag: "etag" }));
  const packageZip = new JSZip();
  packageZip.file("AGENT_TASK.md", "# 开发说明\n");
  packageZip.file("stage-skill.json", "{}");
  packageZip.file("implementation/SKILL.md", "# 诊断预处理\n\n读取真实输入并完成处理。\n");
  const packageBytes = await packageZip.generateAsync({ type: "nodebuffer" });
  const getObject = vi.fn(async () => ({
    content: packageBytes,
    etag: null,
    contentType: "application/zip",
  }));
  const ocbLocalSkills = {
    exportLocalSkill: vi.fn(async () => ({
      packageBytes: Buffer.from("real skill package"),
      sha256: `sha256:${"b".repeat(64)}`,
      displayName: "local-skill",
    })),
  };
  const dispatch = vi.fn(async () => ({ runId: "run-1", sessionId: "session-1", platformResponse: {} }));
  const app = express();
  app.use(express.json());
  app.use("/api/evolve", createStageSkillsRouter({
    repo: stageRepo as never,
    evolveRepo: evolveRepo as never,
    artifactStore: { putObject, getObject } as never,
    skillAssetRepo: skillAssetRepo as never,
    ocbLocalSkills: ocbLocalSkills as never,
    dispatch,
    ...overrides,
  }));
  const started = await new Promise<ReturnType<express.Application["listen"]>>((resolve) => {
    const instance = app.listen(0, () => resolve(instance));
  });
  server = started;
  return {
    baseUrl: `http://127.0.0.1:${(started.address() as { port: number }).port}`,
    evolveRepo, stageRepo, skillAssetRepo, ocbLocalSkills, putObject, getObject, dispatch,
  };
}

describe("Stage Skill package view", () => {
  it("shows the uploaded implementation files instead of only validation status", async () => {
    const test = await startRouter();
    const response = await fetch(`${test.baseUrl}/api/evolve/stage-skills/IMPL-1/content`, {
      headers: { "X-User-Id": "user-1" },
    });

    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      files: expect.arrayContaining([
        expect.objectContaining({ path: "implementation/SKILL.md", text: true }),
      ]),
      selected: {
        path: "implementation/SKILL.md",
        content: expect.stringContaining("读取真实输入"),
      },
    });
    expect(test.getObject).toHaveBeenCalledWith("implementation.zip");
  });
});
