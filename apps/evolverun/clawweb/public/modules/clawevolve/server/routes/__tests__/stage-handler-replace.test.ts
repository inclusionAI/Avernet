import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "../../repositories/evolve-repository.js";
import { BenchDomainRepository } from "../../repositories/bench-domain-repository.js";
import { BenchTemplateRepository } from "../../repositories/bench-template-repository.js";
import { BenchRunRepository } from "../../repositories/bench-run-repository.js";
import { SkillAssetRepository } from "../../repositories/skill-asset-repository.js";
import { StageSkillRepository } from "../../repositories/stage-skill-repository.js";
import { createEvolveRouter } from "../evolve.js";

let db: SqliteDatabase;
let repo: EvolveRepository;
let benchDomainRepo: BenchDomainRepository;
let benchTemplateRepo: BenchTemplateRepository;
let benchRunRepo: BenchRunRepository;
let skillAssetRepo: SkillAssetRepository;
let stageSkillRepo: StageSkillRepository;
let server: ReturnType<express.Application["listen"]> | null;
let baseUrl: string;
let lostReportResponse = false;
const dispatch = vi.fn();
const dispatchTaskLogArchive = vi.fn();
const cancelExecution = vi.fn();
const createSignedUrl = vi.fn();
const getObject = vi.fn();
const putObject = vi.fn();
const hostLocalSkills = {
  listLocalSkills: vi.fn(),
  exportLocalSkill: vi.fn(),
  replaceLocalSkill: vi.fn(),
};

beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  repo = new EvolveRepository(db);
  benchDomainRepo = new BenchDomainRepository(db);
  benchTemplateRepo = new BenchTemplateRepository(db);
  benchRunRepo = new BenchRunRepository(db);
  skillAssetRepo = new SkillAssetRepository(db);
  stageSkillRepo = new StageSkillRepository(db);
  dispatch.mockReset();
  dispatch.mockResolvedValue({ runId: "run-1", sessionId: "session-1" });
  dispatchTaskLogArchive.mockReset();
  dispatchTaskLogArchive.mockResolvedValue({
    runId: "log-run-1", sessionId: null,
    platformResponse: { evolve_dispatch: { provider: "baas", transport: "baas_execute_command" } },
  });
  cancelExecution.mockReset();
  cancelExecution.mockResolvedValue({ transport: "message" });
  createSignedUrl.mockReset();
  createSignedUrl.mockResolvedValue("https://oss.example.test/signed");
  getObject.mockReset();
  putObject.mockReset();
  putObject.mockResolvedValue({ etag: "etag" });
  hostLocalSkills.listLocalSkills.mockReset();
  hostLocalSkills.exportLocalSkill.mockReset();
  hostLocalSkills.exportLocalSkill.mockResolvedValue({
    packageBytes: Buffer.from("local-skill-package"),
    sha256: `sha256:${"a".repeat(64)}`,
    displayName: "local-skill",
  });
  hostLocalSkills.replaceLocalSkill.mockReset();
  hostLocalSkills.replaceLocalSkill.mockResolvedValue({ sha256: `sha256:${"b".repeat(64)}` });
  const app = express();
  lostReportResponse = false;
  app.use(express.json());
  app.use((req, res, next) => {
    if (req.header("X-ClawEvolve-Report-Id") === reportId(99) && !lostReportResponse) {
      res.json = () => { lostReportResponse = true; res.destroy(); return res; };
    }
    next();
  });
  app.use("/api/evolve", createEvolveRouter(repo, {
    dispatch, dispatchTaskLogArchive, cancelExecution, benchDomainRepo, benchTemplateRepo, benchRunRepo,
    artifactStore: { getObject, putObject, createSignedUrl },
    artifactUrlStore: { createSignedUrl },
    skillAssetRepo, stageSkillRepo, hostLocalSkills,
    modelConfig: { defaultModel: "GLM-5.1", models: ["GLM-5.1"] },
  }));
  const startedServer = await new Promise<ReturnType<express.Application["listen"]>>((resolve, reject) => {
    const instance = app.listen(0, "127.0.0.1", () => resolve(instance));
    instance.once("error", reject);
  });
  server = startedServer;
  baseUrl = `http://127.0.0.1:${(startedServer.address() as { port: number }).port}`;
});

afterEach(async () => {
  const activeServer = server;
  server = null;
  if (activeServer) await new Promise<void>((resolve) => activeServer.close(() => resolve()));
  await db.close();
});

async function seedArcaBot(userId = "user-1", botId = "bot-arca", env = "pre") {
  await db.exec(`CREATE TABLE IF NOT EXISTS ac_entity_device_binding (
    id INTEGER PRIMARY KEY, device_provider TEXT, device_id TEXT, device_props TEXT, status TEXT, env TEXT
  )`);
  await db.exec(`CREATE TABLE IF NOT EXISTS ac_bots (
    id INTEGER PRIMARY KEY, bot_id TEXT NOT NULL, bot_name TEXT, owner_id TEXT, entity_id TEXT,
    is_delete INTEGER NOT NULL DEFAULT 0, active_engine TEXT, bot_type TEXT, status TEXT,
    binding_id INTEGER, env TEXT
  )`);
  await db.exec(
    "INSERT INTO ac_entity_device_binding (id, device_provider, device_id, status, env) VALUES (?, 'arca', ?, 'active', ?)",
    [91, `ARCA-${botId}`, env],
  );
  await db.exec(
    `INSERT INTO ac_bots
      (id, bot_id, bot_name, owner_id, entity_id, is_delete, active_engine, bot_type, status, binding_id, env)
     VALUES (?, ?, ?, ?, ?, 0, 'openclaw', 'personal', 'active', ?, ?)`,
    [92, botId, botId, userId, userId, 91, env],
  );
}

async function callback(taskId: string, stepId: string, body: Record<string, unknown>, reportId?: string) {
  const response = await fetch(`${baseUrl}/api/evolve/internal/tasks/${taskId}/steps/${stepId}/report`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(reportId ? { "X-ClawEvolve-Report-Id": reportId } : {}) },
    body: JSON.stringify(body),
  });
  return { response, body: await response.json() as Record<string, unknown> };
}

async function seedStageImplementation(
  mode: "preprocess" | "postprocess" | "replace" = "preprocess",
  stage: "diagnose" | "plan" | "optimize" | "hardening" = "diagnose",
  flow?: "skill_evolution" | "bot_evolution" | "skill_hardening",
  executionContract?: string,
) {
  const implementationId = `IMPL-${stage}-${mode}`;
  let stageSkillId = `STAGESKILL-${stage}-${mode}`;
  if (flow) {
    const development = await stageSkillRepo.createDevelopment({
      ownerUserId: "user-1",
      displayName: "与流程选择无关的任意名称", flow, stage, mode,
    });
    stageSkillId = String(development.id);
  }
  await stageSkillRepo.createImplementation({
    stageSkillId,
    implementationId,
    ownerUserId: "user-1",
    displayName: `${stage} ${mode}`,
    stage,
    mode,
    versionNo: 1,
    packageRef: `oss://clawevolve-artifacts/evolve/stage-implementations/${implementationId}/v1/package.zip`,
    packageSha256: "c".repeat(64),
    staticValidation: { status: "passed", ...(executionContract ? { executionContract } : {}) },
  });
  await stageSkillRepo.registerImplementation(implementationId);
  return implementationId;
}

// Exercise public routes and persisted Steps: no mocks of scheduling or result
// processing, and no assumptions about the not-yet-defined loop protocol.
const diagnosis = { diagnosis: { summary: "observed", issues: [] }, cases: { total: 0, goodCount: 0, badCount: 0, items: [] } };
const cases = [
  { stage: "diagnose", command: "/clawevolve-diagnose", input: { diagnose_goal: "Inspect selected sessions", session_source: { mode: "local", session_ids: ["session-a"] } } },
  { stage: "plan", command: "/clawevolve-plan", input: { goal: "Improve task success", diagnose_result: diagnosis } },
  { stage: "optimize", command: "/clawevolve-workflow --stage optimize", input: { round: 1, plan_result: { goal: { summary: "Improve task success", metrics: [] }, spec: { version: "v0", content_type: "text", content: "Improve the test Skill" }, benchDomains: { trainBenchDomainId: "TRAIN", testBenchDomainId: "TEST" }, benchCases: { trainCount: 1, testCount: 1, items: [{ sourceCaseId: "fixture-train", taskId: "fixture-train", split: "train", template: { ownerUserId: "user-1", domainId: "TRAIN", templateName: "fixture" } }, { sourceCaseId: "fixture-test", taskId: "fixture-test", split: "test", template: { ownerUserId: "user-1", domainId: "TEST", templateName: "fixture" } }] } } } },
  { stage: "hardening", command: "/clawevolve-hardening", input: { goal: "Harden the fixture" } },
] as const;

async function post(path: string, body: unknown) {
  const response = await fetch(`${baseUrl}/api/evolve${path}`, {
    method: "POST", headers: { "Content-Type": "application/json", "X-User-Id": "user-1" },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  expect(response.ok, JSON.stringify(payload)).toBe(true);
  return payload;
}

describe("Stage Handler replacement boundary", () => {
  it.each(cases)("keeps $stage replacement inside the Stage Step", async ({ stage, command, input }) => {
    const implementationId = await seedStageImplementation("replace", stage, stage === "hardening" ? "skill_hardening" : undefined);
    const task = await post(`/stage-skills/${implementationId}/integration-tests`, { botId: "bot-1", caseInput: input });
    const steps = await repo.listSteps(task.taskId);
    expect.soft(steps.filter(step => step.command !== "stage-test supplied Plan result").map(step => step.step_type)).toEqual([stage]);
    expect.soft(await stageSkillRepo.listExtensionRuns(task.taskId), "core replacement must not create ce_stage_extension_runs").toEqual([]);
    expect.soft(dispatch).toHaveBeenCalledWith(expect.objectContaining({
      stepType: stage,
      command: expect.stringContaining(command),
      agentMessageOnly: false,
    }));
    const response = await fetch(`${baseUrl}/api/evolve/tasks/${task.taskId}`, { headers: { "X-User-Id": "user-1" } });
    const detail = await response.json();
    expect(detail.steps.find((step: { stepType: string }) => step.stepType === stage)).toMatchObject({ stepType: stage, stageExtension: null,
      coreImplementation: { stage, mode: "replace", implementationId, displayName: `${stage} replace` } });
  });

  it("rejects feedback Loop for Optimize replacement without blocking same-Step HITL", async () => {
    const implementationId = await seedStageImplementation("replace", "optimize");
    const task = await post(`/stage-skills/${implementationId}/integration-tests`, {
      botId: "bot-1", caseInput: cases.find(item => item.stage === "optimize")!.input,
    });
    const step = (await repo.listSteps(task.taskId)).find(item => item.step_type === "optimize")!;
    const dispatched = dispatch.mock.calls.length;
    const rejected = await callback(task.taskId, step.step_id, {
      status: "succeeded", output: { hitl: false, result: {}, loop: {
        action: "request_feedback", prompt: "Continue?", accepts: { text: true, files: [] },
      } },
    });
    expect(rejected.response.status).toBe(422);
    expect(rejected.body.error).toContain("Optimize replace 不支持反馈 Loop");
    expect((await repo.findStep(step.step_id))?.status).toBe(step.status);
    expect(await stageSkillRepo.findWaitingInteraction(task.taskId, step.step_id)).toBeNull();
    expect(dispatch).toHaveBeenCalledTimes(dispatched);
    const waiting = await callback(task.taskId, step.step_id, {
      status: "succeeded", output: { hitl: true, question: { format: "form", title: "Confirm scope", contents: [],
        questions: [{ id: "scope", type: "short_text", title: "Scope", required: true }],
      } },
    });
    expect(waiting.response.status, JSON.stringify(waiting.body)).toBe(200);
    expect((await repo.findStep(step.step_id))?.status).toBe("waiting_context");
    expect((await repo.listSteps(task.taskId)).filter(item => item.step_type === "optimize")).toHaveLength(1);
  });

  it("runs preprocess, the real Diagnose Stage, then postprocess in order", async () => {
    const pre = await seedStageImplementation("preprocess");
    const postId = await seedStageImplementation("postprocess");
    for (const implementationId of [pre, postId]) await stageSkillRepo.updateIntegrationTest(implementationId, `TEST-${implementationId}`, "test_passed");
    const task = await post("/tasks", {
      taskType: "diagnose", taskName: "Ordered handlers", userId: "user-1", botId: "bot-1",
      model: "fixture-model", diagnoseIntent: "Inspect selected sessions", runtimeMaintenance: false,
      stageSelection: { diagnose: true, plan: false, optimize: false },
      stageExtensions: { diagnose: { preprocess: { enabled: true, implementationId: pre }, postprocess: { enabled: true, implementationId: postId } } },
    });
    let steps = await repo.listSteps(task.task_id);
    expect(steps.map(step => step.step_type)).toEqual(["stage_extension"]);
    const prepared = await callback(task.task_id, steps[0].step_id, { status: "succeeded", output: { hitl: false, result: { summary: "prepared", changed: false } } });
    expect(prepared.response.status, JSON.stringify(prepared.body)).toBe(200);
    steps = await repo.listSteps(task.task_id);
    expect(steps.map(step => step.step_type)).toEqual(["stage_extension", "diagnose"]);
    expect(steps[1].command).toContain("fixture-model");
    const diagnosed = await callback(task.task_id, steps[1].step_id, { status: "succeeded", output: diagnosis });
    expect(diagnosed.response.status, JSON.stringify(diagnosed.body)).toBe(200);
    steps = await repo.listSteps(task.task_id);
    expect(steps.map(step => step.step_type)).toEqual(["stage_extension", "diagnose", "stage_extension"]);
    expect((await stageSkillRepo.listExtensionRuns(task.task_id)).map(run => run.extension_mode)).toEqual(["preprocess", "postprocess"]);
    const finished = await callback(task.task_id, steps[2].step_id, { status: "succeeded", output: { hitl: false, result: { result_patch: { diagnosis: { summary: "reviewed" } } } } });
    expect(finished.response.status, JSON.stringify(finished.body)).toBe(200);
    expect((await repo.findTask(task.task_id))?.status).toBe("completed");
    expect(JSON.parse((await repo.findStep(steps[2].step_id))!.output_json!)).toMatchObject({ diagnosis: { summary: "reviewed" } });
  });

  it.each([false, true])("preserves Hardening model, prepared candidate and result processing (replace=%s)", async replace => {
    await seedArcaBot();
    await skillAssetRepo.createAsset({
      assetId: "SKILL-HARDENING", versionId: "SKILL-HARDENING-V1", ownerUserId: "user-1",
      botId: "bot-arca", externalSkillId: "hardening-target", displayName: "Hardening target",
      packageRef: "oss://clawevolve-artifacts/evolve/skills/SKILL-HARDENING/versions/v1/package.zip",
      packageSha256: `sha256:${"a".repeat(64)}`,
    });
    const implementationId = await seedStageImplementation("replace", "hardening");
    await stageSkillRepo.updateIntegrationTest(implementationId, "TEST-HARDENING", "test_passed");
    const task = await post("/tasks", {
      taskType: "hardening", taskName: "Hardening boundary", userId: "user-1", botId: "bot-arca", botEnv: "pre",
      targetSkillAssetId: "SKILL-HARDENING", goal: "Preserve business intent", model: "hardening-model", runtimeMaintenance: false,
      stageExtensions: { hardening: { replace: { enabled: replace, implementationId } } },
    });
    expect(task.steps.map((step: { stepType: string }) => step.stepType)).toEqual(["skill_prepare"]);
    const workspace = `/home/admin/.openclaw/clawevolve_workspaces/${task.task_id}/workspace`;
    const targetSkillPath = `${workspace}/skills/skills-local/local-skill`;
    const preparation = { status: "succeeded", output: { prepared: true, workspace, targetSkillPath } };
    const prepared = await callback(task.task_id, task.steps[0].stepId, preparation, reportId(10));
    const dispatchCount = dispatch.mock.calls.length;
    const duplicate = await callback(task.task_id, task.steps[0].stepId, preparation, reportId(10));
    expect(duplicate.response.status).toBe(200);
    expect(duplicate.body.nextStep).toEqual(prepared.body.nextStep);
    expect(dispatch).toHaveBeenCalledTimes(dispatchCount);
    expect(prepared.response.status, JSON.stringify(prepared.body)).toBe(200);
    const next = prepared.body.nextStep as { stepId: string; stepType: string };
    const step = (await repo.findStep(next.stepId))!;
    expect.soft(next.stepType).toBe("hardening");
    expect.soft(await stageSkillRepo.listExtensionRuns(task.task_id)).toEqual([]);
    // The dispatch must still enter Hardening's platform wrapper with its model
    // and isolated candidate, regardless of which core implementation it selects.
    expect.soft(step.command).toContain("/clawevolve-hardening");
    expect.soft(step.command).toContain("--model hardening-model");
    expect.soft(step.command).toContain(`--target '${targetSkillPath}'`);
    expect.soft(dispatch).toHaveBeenLastCalledWith(expect.objectContaining({
      stepType: "hardening",
      command: expect.stringContaining("/clawevolve-hardening"),
      agentMessageOnly: false,
    }));
    const inputResponse = await fetch(`${baseUrl}/api/evolve/internal/tasks/${task.task_id}/steps/${next.stepId}/input`);
    expect(inputResponse.status).toBe(200);
    const runtimeInput = await inputResponse.json();
    const replacementPackageKey = `evolve/stage-implementations/${implementationId}/v1/package.zip`;
    if (replace) {
      // A wrapper that simply ignores the override must also fail this test.
      expect.soft(JSON.stringify(runtimeInput)).toContain(implementationId);
      expect.soft(createSignedUrl).toHaveBeenCalledWith(replacementPackageKey, "GET", expect.any(Number));
    } else {
      expect.soft(createSignedUrl.mock.calls.some(([key]) => key === replacementPackageKey)).toBe(false);
    }
    const businessOutput = {
      summary: "Business-specific hardening", changed: true, changed_files: ["SKILL.md"],
      business: { policy: "retain-domain-rules", evidence: [{ id: "rule-1", passed: true }], count: 0, note: null },
    };
    // The default implementation reports the native Handler output. A custom
    // implementation uses the Stage runtime envelope, which the Handler unwraps
    // before applying the same validation, persistence and finalization logic.
    const reportedOutput = replace ? { hitl: false, result: businessOutput } : businessOutput;
    const report = { status: "succeeded", output: reportedOutput };
    const completed = await callback(task.task_id, next.stepId, report, reportId(11));
    const finalDispatches = dispatch.mock.calls.length;
    expect((await callback(task.task_id, next.stepId, report, reportId(11))).response.status).toBe(200);
    expect(dispatch).toHaveBeenCalledTimes(finalDispatches);
    expect((await callback(task.task_id, next.stepId, report, reportId(12))).response.status).toBe(409);
    expect.soft(completed.response.status, JSON.stringify(completed.body)).toBe(200);
    expect.soft(JSON.parse((await repo.findStep(next.stepId))!.output_json!)).toEqual(businessOutput);
    expect.soft(completed.body.nextStep).toMatchObject({ stepType: "skill_finalize" });
  });
});

const reportId = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;

it("replays accepted HITL and Loop reports after resume without creating another interaction", async () => {
  const impl = await seedStageImplementation("replace", "hardening", "skill_hardening");
  const task = await post(`/stage-skills/${impl}/integration-tests`, { botId: "bot-1", caseInput: { goal: "Harden fixture" } });
  const step = (await repo.listSteps(task.taskId))[0];
  const form = { status: "succeeded", output: { hitl: true, question: {
    format: "form", title: "Scope", contents: [],
    questions: [{ id: "scope", type: "short_text", title: "Scope", required: true }],
  } } };
  const first = await callback(task.taskId, step.step_id, form, reportId(1));
  expect(first.response.status, JSON.stringify(first.body)).toBe(200);
  const interaction = first.body.interaction as { interactionId: string };
  const replay = await callback(task.taskId, step.step_id, form, reportId(1));
  expect(replay.response.status).toBe(200);
  expect(replay.body.interaction).toMatchObject({ interactionId: interaction.interactionId });
  expect(await stageSkillRepo.listInteractions(task.taskId)).toHaveLength(1);
  expect((await callback(task.taskId, step.step_id, { ...form, summary: "changed" }, reportId(1))).response.status).toBe(409);
  await post(`/tasks/${task.taskId}/steps/${step.step_id}/interactions/${interaction.interactionId}/answer`, {
    answer: { answers: { scope: { value: "Only candidate Skill" } } },
  });
  const dispatched = dispatch.mock.calls.length;
  expect((await callback(task.taskId, step.step_id, form, reportId(1))).response.status).toBe(200);
  expect((await repo.findStep(step.step_id))?.status).toBe("dispatched");
  expect(await stageSkillRepo.listInteractions(task.taskId)).toHaveLength(1);
  expect(dispatch).toHaveBeenCalledTimes(dispatched);
  const loop = { status: "succeeded", output: { hitl: false,
    result: { summary: "Done", changed: false, changed_files: [] },
    loop: { action: "request_feedback", prompt: "Continue?", accepts: { text: true, files: [] } },
  } };
  const waiting = await callback(task.taskId, step.step_id, loop, reportId(2));
  expect(waiting.response.status, JSON.stringify(waiting.body)).toBe(200);
  expect((await callback(task.taskId, step.step_id, loop, reportId(2))).response.status).toBe(200);
  expect(await stageSkillRepo.listInteractions(task.taskId)).toHaveLength(2);
  const loopInteraction = waiting.body.interaction as { interactionId: string };
  await post(`/tasks/${task.taskId}/steps/${step.step_id}/interactions/${loopInteraction.interactionId}/answer`, {
    answer: { action: "accept" },
  });
  const finalDispatches = dispatch.mock.calls.length;
  expect((await callback(task.taskId, step.step_id, loop, reportId(2))).response.status).toBe(200);
  expect((await repo.findStep(step.step_id))?.status).toBe("succeeded");
  expect(await stageSkillRepo.listInteractions(task.taskId)).toHaveLength(2);
  expect(dispatch).toHaveBeenCalledTimes(finalDispatches);
});

it("serializes concurrent report delivery across repositories and never retries uncertain orchestration", async () => {
  const impl = await seedStageImplementation("replace", "hardening", "skill_hardening");
  const task = await post(`/stage-skills/${impl}/integration-tests`, { botId: "bot-1", caseInput: { goal: "Harden fixture" } });
  const step = (await repo.listSteps(task.taskId))[0];
  const other = new EvolveRepository(db);
  const claims = await Promise.all([
    repo.stepReports.claim(task.taskId, step.step_id, reportId(3), "same"),
    other.stepReports.claim(task.taskId, step.step_id, reportId(3), "same"),
  ]);
  expect(claims.filter(item => item.claimed)).toHaveLength(1);
  expect(claims.filter(item => !item.claimed)).toMatchObject([{ response: { statusCode: 503 } }]);
  expect(await other.stepReports.claim(task.taskId, step.step_id, reportId(4), "new"))
    .toMatchObject({ claimed: false, response: { statusCode: 503 } });
  await repo.stepReports.complete(task.taskId, step.step_id, reportId(3), { statusCode: 200, body: { ok: true } });
  expect(await other.stepReports.claim(task.taskId, step.step_id, reportId(3), "same"))
    .toMatchObject({ claimed: false, response: { statusCode: 200, body: { ok: true } } });
});

it("recovers a socket dropped after the real report handler commits without dispatching twice", async () => {
  const impl = await seedStageImplementation("replace", "hardening", "skill_hardening");
  const task = await post(`/stage-skills/${impl}/integration-tests`, { botId: "bot-1", caseInput: { goal: "Harden fixture" } });
  const step = (await repo.listSteps(task.taskId))[0];
  const body = { status: "succeeded", output: { hitl: false,
    result: { summary: "Done", changed: false, changed_files: [] },
  } };
  await expect(callback(task.taskId, step.step_id, body, reportId(99))).rejects.toThrow();
  expect(lostReportResponse).toBe(true);
  expect((await repo.findStep(step.step_id))?.status).toBe("succeeded");
  const calls = dispatch.mock.calls.length;
  const replay = await callback(task.taskId, step.step_id, body, reportId(99));
  expect(replay.response.status).toBe(200);
  expect(replay.body.ok).toBe(true);
  expect(dispatch).toHaveBeenCalledTimes(calls);
});

it("a normal task retry starts a new Step without inheriting an uncertain delivery claim", async () => {
  const impl = await seedStageImplementation("replace", "hardening", "skill_hardening");
  const task = await post(`/stage-skills/${impl}/integration-tests`, { botId: "bot-1", caseInput: { goal: "Harden fixture" } });
  const step = (await repo.listSteps(task.taskId))[0];
  await repo.stepReports.claim(task.taskId, step.step_id, reportId(5), "uncertain");
  await repo.updateStepStatus(step.step_id, { status: "failed", errorMessage: "delivery unresolved" });
  await post(`/tasks/${task.taskId}/steps/${step.step_id}/retry`, {});
  const retried = (await repo.listSteps(task.taskId)).at(-1)!;
  expect(retried.step_id).not.toBe(step.step_id);
  expect(JSON.parse(retried.ext_json ?? "{}").report_delivery).toBeUndefined();
  expect(await repo.stepReports.claim(task.taskId, retried.step_id, reportId(6), "new report"))
    .toEqual({ claimed: true });
});
