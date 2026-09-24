import { afterEach, beforeEach, describe, expect, it } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "../../repositories/evolve-repository.js";
import { StageSkillRepository } from "../../repositories/stage-skill-repository.js";
import type { FrozenTaskStageExtensions } from "../../services/evolve/stage-execution.js";
import type { StageExtensionMode, StageKey } from "../../services/evolve/stage-catalog.js";
import { createEvolveRouter } from "../evolve.js";

const owner = "task-owner";
const taskId = "TASK-MAPPING";
let db: SqliteDatabase;
let tasks: EvolveRepository;
let stages: StageSkillRepository;
let server: ReturnType<express.Application["listen"]> | undefined;
let url: string;

type ExtensionView = { stage: string; mode: string; implementationId: string; displayName: string | null };
type StepView = {
  stepId: string;
  stepType: string;
  roundNo: number | null;
  stageExtension: ExtensionView | null;
  stageLoop: { rootStepId: string; round: number; previousStepId: string | null } | null;
};

beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  tasks = new EvolveRepository(db);
  stages = new StageSkillRepository(db);
  const app = express();
  app.use("/api/evolve", createEvolveRouter(tasks, { stageSkillRepo: stages }));
  const started = await new Promise<ReturnType<express.Application["listen"]>>((resolve, reject) => {
    const instance = app.listen(0, "127.0.0.1", () => resolve(instance));
    instance.once("error", reject);
  });
  server = started;
  url = `http://127.0.0.1:${(started.address() as { port: number }).port}/api/evolve/tasks`;
});

afterEach(async () => {
  if (server) await new Promise<void>((resolve) => server!.close(() => resolve()));
  server = undefined;
  await db?.close();
});

async function seedTask(extensions: FrozenTaskStageExtensions = {}, id = taskId, user = owner) {
  await tasks.createTask({ taskId: id, taskType: "full", taskName: "Step mapping regression", userId: user,
    botId: "fixture-bot", createdBy: user, configJson: JSON.stringify({ stageExtensions: extensions }) });
}

async function seedImplementation(implementationId: string, stage: StageKey = "diagnose", mode: StageExtensionMode = "preprocess", displayName = "Current implementation name") {
  await stages.createImplementation({ implementationId, stageSkillId: `SKILL-${implementationId}`, ownerUserId: owner,
    displayName, stage, mode, versionNo: 1, packageRef: "fixture:package", packageSha256: "fixture-checksum",
    staticValidation: { status: "passed" } });
}

async function seedStep(stepId: string, stepNo: number, roundNo: number | null = null, stepType = "stage_extension", id = taskId) {
  await tasks.createStep({ stepId, stepNo, roundNo, stepType, taskId: id,
    // Deliberately ambiguous: display projection must use persisted runs, not
    // parse the command or infer a binding from the task's configured slots.
    command: "fixture-command --implementationId=not-the-source-of-truth" });
}

async function seedRun(stepId: string, implementationId: string, stage: StageKey = "diagnose", mode: StageExtensionMode = "preprocess", id = taskId) {
  await stages.createExtensionRun({ stepId, taskId: id, implementationId, stage, mode,
    initialInput: { privateRunInput: "must-not-be-in-stage-extension-view" } });
}

async function detail(id = taskId) {
  const response = await fetch(`${url}/${id}`, { headers: { "X-User-Id": owner } });
  expect(response.status, await response.clone().text()).toBe(200);
  return await response.json() as { steps: StepView[]; [key: string]: unknown };
}

describe("GET task step Stage extension projection (real repositories)", () => {
  it("maps every pre/post Step across rounds to its own run, independently of insertion order", async () => {
    await seedTask({
      diagnose: {
        preprocess: { enabled: true, implementationId: "diag-pre", displayName: "Frozen diagnose pre" },
        postprocess: { enabled: true, implementationId: "diag-post", displayName: "Frozen diagnose post" },
      },
      optimize: {
        preprocess: { enabled: true, implementationId: "opt-pre", displayName: "Frozen optimize pre" },
        postprocess: { enabled: true, implementationId: "opt-post", displayName: "Frozen optimize post" },
      },
    });
    for (const [id, stage, mode] of [
      ["diag-pre", "diagnose", "preprocess"], ["diag-post", "diagnose", "postprocess"],
      ["opt-pre", "optimize", "preprocess"], ["opt-post", "optimize", "postprocess"],
    ] as const) await seedImplementation(id, stage, mode, `Live renamed ${id}`);
    const plan = [
      { stepId: "D-PRE", round: null, stage: "diagnose", mode: "preprocess", implementation: "diag-pre" },
      { stepId: "DIAGNOSE", round: null },
      { stepId: "D-POST", round: null, stage: "diagnose", mode: "postprocess", implementation: "diag-post" },
      { stepId: "R1-PRE", round: 1, stage: "optimize", mode: "preprocess", implementation: "opt-pre" },
      { stepId: "OPTIMIZE-1", round: 1 },
      { stepId: "R1-POST", round: 1, stage: "optimize", mode: "postprocess", implementation: "opt-post" },
      { stepId: "R2-PRE", round: 2, stage: "optimize", mode: "preprocess", implementation: "opt-pre" },
      { stepId: "OPTIMIZE-2", round: 2 },
      { stepId: "R2-POST", round: 2, stage: "optimize", mode: "postprocess", implementation: "opt-post" },
    ] as const;
    for (const [index, step] of plan.entries()) {
      await seedStep(step.stepId, index + 1, step.round, "implementation" in step ? "stage_extension" : step.round === null ? "diagnose" : "optimize");
    }
    for (const step of [...plan].reverse()) {
      if ("implementation" in step) await seedRun(step.stepId, step.implementation, step.stage, step.mode);
    }
    const response = await detail();
    expect(response.steps.map(step => ({ stepId: step.stepId, round: step.roundNo, stageExtension: step.stageExtension }))).toEqual([
      { stepId: "D-PRE", round: null, stageExtension: { stage: "diagnose", mode: "preprocess", implementationId: "diag-pre", displayName: "Frozen diagnose pre" } },
      { stepId: "DIAGNOSE", round: null, stageExtension: null },
      { stepId: "D-POST", round: null, stageExtension: { stage: "diagnose", mode: "postprocess", implementationId: "diag-post", displayName: "Frozen diagnose post" } },
      { stepId: "R1-PRE", round: 1, stageExtension: { stage: "optimize", mode: "preprocess", implementationId: "opt-pre", displayName: "Frozen optimize pre" } },
      { stepId: "OPTIMIZE-1", round: 1, stageExtension: null },
      { stepId: "R1-POST", round: 1, stageExtension: { stage: "optimize", mode: "postprocess", implementationId: "opt-post", displayName: "Frozen optimize post" } },
      { stepId: "R2-PRE", round: 2, stageExtension: { stage: "optimize", mode: "preprocess", implementationId: "opt-pre", displayName: "Frozen optimize pre" } },
      { stepId: "OPTIMIZE-2", round: 2, stageExtension: null },
      { stepId: "R2-POST", round: 2, stageExtension: { stage: "optimize", mode: "postprocess", implementationId: "opt-post", displayName: "Frozen optimize post" } },
    ]);
  });

  it.each([
    { reason: "old binding without a frozen name", extensions: { diagnose: { preprocess: { enabled: true, implementationId: "actual" } } } },
    { reason: "no frozen binding", extensions: {} },
    { reason: "frozen name belongs to another implementation", extensions: { diagnose: { preprocess: { enabled: true, implementationId: "other", displayName: "Wrong frozen name" } } } },
    { reason: "frozen name belongs to another mode", extensions: { diagnose: { postprocess: { enabled: true, implementationId: "actual", displayName: "Wrong frozen slot" } } } },
  ] satisfies Array<{ reason: string; extensions: FrozenTaskStageExtensions }>)("falls back to the run implementation's name for $reason", async ({ extensions }) => {
    await seedTask(extensions);
    await seedImplementation("actual");
    await seedStep("STEP", 1);
    await seedRun("STEP", "actual");
    expect((await detail()).steps[0]?.stageExtension).toEqual({
      stage: "diagnose", mode: "preprocess", implementationId: "actual", displayName: "Current implementation name",
    });
  });

  it.each([true, false])("preserves actual run identity if its implementation record is missing (frozenName=%s)", async frozenName => {
    await seedTask({ diagnose: { preprocess: { enabled: true, implementationId: "removed",
      ...(frozenName ? { displayName: "Historical frozen name" } : {}) } } });
    await seedStep("STEP", 1);
    await seedRun("STEP", "removed");
    expect((await detail()).steps[0]?.stageExtension).toEqual({
      stage: "diagnose", mode: "preprocess", implementationId: "removed", displayName: frozenName ? "Historical frozen name" : null,
    });
  });

  it("returns explicit null for a non-extension Step even if an inconsistent run references it", async () => {
    await seedTask({ diagnose: { preprocess: { enabled: true, implementationId: "actual", displayName: "Frozen name" } } });
    await seedImplementation("actual");
    await seedStep("BUILTIN", 1, null, "diagnose");
    await seedRun("BUILTIN", "actual");
    expect((await detail()).steps[0]).toMatchObject({ stepId: "BUILTIN", stepType: "diagnose", stageExtension: null });
  });

  it("does not guess a missing run from another Step, a frozen binding, or the command", async () => {
    await seedTask({ optimize: { preprocess: { enabled: true, implementationId: "actual", displayName: "Frozen name" } } });
    await seedImplementation("actual", "optimize");
    await seedStep("ROUND-1", 1, 1);
    await seedStep("ROUND-2-MISSING-RUN", 2, 2);
    await seedRun("ROUND-1", "actual", "optimize");
    const response = await detail();
    expect(response.steps.map(step => step.stepId)).toEqual(["ROUND-1", "ROUND-2-MISSING-RUN"]);
    expect(response.steps[0]?.stageExtension).toMatchObject({ implementationId: "actual", displayName: "Frozen name" });
    expect(response.steps[1]?.stageExtension).toBeNull();
  });

  it("does not expose another task's runs, including a foreign run incorrectly referencing this task's Step", async () => {
    await seedTask();
    await seedTask({}, "OTHER-TASK", "other-owner");
    await seedStep("LOCAL-STEP", 1, 1);
    await seedStep("FOREIGN-STEP", 1, 1, "stage_extension", "OTHER-TASK");
    await seedImplementation("PRIVATE-OTHER-IMPLEMENTATION", "optimize", "postprocess", "PRIVATE-OTHER-NAME");
    await seedRun("FOREIGN-STEP", "PRIVATE-OTHER-IMPLEMENTATION", "optimize", "postprocess", "OTHER-TASK");
    // Model inconsistent historical linkage; task_id is still the run's owner.
    await seedRun("LOCAL-STEP", "PRIVATE-OTHER-IMPLEMENTATION", "optimize", "postprocess", "OTHER-TASK");
    const response = await detail();
    expect(response.steps).toEqual([expect.objectContaining({ stepId: "LOCAL-STEP", stageExtension: null })]);
    expect(JSON.stringify(response)).not.toMatch(/PRIVATE-OTHER|FOREIGN-STEP|OTHER-TASK|privateRunInput/);
  });

  it("projects persisted Stage Loop lineage for the business execution timeline", async () => {
    await seedTask();
    await seedStep("ROUND-1", 1, null, "hardening");
    await seedStep("ROUND-2", 2, null, "hardening");
    await tasks.updateStepStatus("ROUND-1", { status: "created", ext: { loop: {
      root_step_id: "ROUND-1", round: 1, previous_step_id: null,
    } } });
    await tasks.updateStepStatus("ROUND-2", { status: "created", ext: { loop: {
      root_step_id: "ROUND-1", round: 2, previous_step_id: "ROUND-1",
    } } });
    const response = await detail();
    expect(response.steps.map((step) => ({ stepId: step.stepId, stageLoop: step.stageLoop }))).toEqual([
      { stepId: "ROUND-1", stageLoop: { rootStepId: "ROUND-1", round: 1, previousStepId: null } },
      { stepId: "ROUND-2", stageLoop: { rootStepId: "ROUND-1", round: 2, previousStepId: "ROUND-1" } },
    ]);
  });

  it("does not disclose Step mappings to a caller without task access", async () => {
    await seedTask();
    await seedImplementation("private-implementation");
    await seedStep("PRIVATE-STEP", 1);
    await seedRun("PRIVATE-STEP", "private-implementation");
    const response = await fetch(`${url}/${taskId}`, { headers: { "X-User-Id": "outsider" } });
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ code: "TASK_NOT_SHARED", error: "权限不足，请联系任务 Owner 开启分享" });
  });
});
