import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "../../repositories/evolve-repository.js";
import { BenchDomainRepository } from "../../repositories/bench-domain-repository.js";
import { BenchTemplateRepository } from "../../repositories/bench-template-repository.js";
import { BenchRunRepository } from "../../repositories/bench-run-repository.js";
import { InsightImprovementRepository } from "../../repositories/insight-improvement-repository.js";
import { createEvolveRouter } from "../evolve.js";

let db: SqliteDatabase;
let repo: EvolveRepository;
let improvementRepo: InsightImprovementRepository;
let benchDomainRepo: BenchDomainRepository;
let benchTemplateRepo: BenchTemplateRepository;
let benchRunRepo: BenchRunRepository;
let server: ReturnType<express.Application["listen"]> | null;
let baseUrl: string;
let seededImprovementId: number | null;
const dispatch = vi.fn();
const dispatchTaskLogArchive = vi.fn();
const cancelExecution = vi.fn();
const createSignedUrl = vi.fn();
const getObject = vi.fn();

beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  repo = new EvolveRepository(db);
  improvementRepo = new InsightImprovementRepository(db);
  benchDomainRepo = new BenchDomainRepository(db);
  benchTemplateRepo = new BenchTemplateRepository(db);
  benchRunRepo = new BenchRunRepository(db);
  seededImprovementId = null;
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
  const app = express();
  app.use(express.json());
  app.use("/api/evolve", createEvolveRouter(repo, {
    dispatch, dispatchTaskLogArchive, cancelExecution, improvementRepo, benchDomainRepo, benchTemplateRepo, benchRunRepo,
    artifactStore: { getObject, createSignedUrl },
    artifactUrlStore: { createSignedUrl },
  }));
  const startedServer = await new Promise<ReturnType<express.Application["listen"]>>((resolve) => {
    const instance = app.listen(0, () => resolve(instance));
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

async function createDiagnosis(model = "GLM-5.1") {
  const response = await fetch(`${baseUrl}/api/evolve/diagnoses`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
    body: JSON.stringify({
      taskName: "诊断任务示例",
      remark: "验证任务名称和备注",
      userId: "user-1", botId: "bot-1", apiKey: "temporary-secret",
      model, diagnoseIntent: "扫描最近3天的历史 session；抽取4个 bad case 和1个 good case；重点关注影响任务完成率的主要问题。",
    }),
  });
  return { response, body: await response.json() as Record<string, unknown> };
}

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

async function seedBaasDraftBotWithService(userId = "user-1", botId = "bot-service-source", env = "prod") {
  await db.exec(`CREATE TABLE IF NOT EXISTS ac_entity_device_binding (
    id INTEGER PRIMARY KEY, device_provider TEXT, device_id TEXT, device_props TEXT, status TEXT, env TEXT
  )`);
  await db.exec(`CREATE TABLE IF NOT EXISTS ac_bots (
    id INTEGER PRIMARY KEY, bot_id TEXT NOT NULL, bot_name TEXT, owner_id TEXT, entity_id TEXT,
    is_delete INTEGER NOT NULL DEFAULT 0, active_engine TEXT, bot_type TEXT, status TEXT,
    binding_id INTEGER, env TEXT
  )`);
  await db.exec(`CREATE TABLE IF NOT EXISTS ac_bot_publish (
    id INTEGER PRIMARY KEY, source_bot_pk INTEGER, env TEXT, status TEXT
  )`);
  await db.exec(
    "INSERT INTO ac_entity_device_binding (id, device_provider, device_id, status, env) VALUES (?, 'baas', ?, 'active', ?)",
    [191, `BAAS-${botId}`, env],
  );
  await db.exec(
    `INSERT INTO ac_bots
      (id, bot_id, bot_name, owner_id, entity_id, is_delete, active_engine, bot_type, status, binding_id, env)
     VALUES (?, ?, ?, ?, ?, 0, 'openclaw', 'personal', 'active', ?, ?)`,
    [192, botId, botId, userId, userId, 191, env],
  );
  await db.exec(
    "INSERT INTO ac_bot_publish (id, source_bot_pk, env, status) VALUES (?, ?, ?, 'success')",
    [193, 192, env],
  );
}

async function seedServiceBotWithBaasDraftBinding(userId = "user-1", botId = "bot-service-runtime", env = "prod") {
  await db.exec(`CREATE TABLE IF NOT EXISTS ac_entity_device_binding (
    id INTEGER PRIMARY KEY, device_provider TEXT, device_id TEXT, device_props TEXT, status TEXT, env TEXT
  )`);
  await db.exec(`CREATE TABLE IF NOT EXISTS ac_bots (
    id INTEGER PRIMARY KEY, bot_id TEXT NOT NULL, bot_name TEXT, owner_id TEXT, entity_id TEXT,
    is_delete INTEGER NOT NULL DEFAULT 0, active_engine TEXT, bot_type TEXT, status TEXT,
    binding_id INTEGER, env TEXT
  )`);
  await db.exec(
    "INSERT INTO ac_entity_device_binding (id, device_provider, device_id, status, env) VALUES (?, 'baas', ?, 'active', ?)",
    [291, `BAAS-${botId}`, env],
  );
  await db.exec(
    `INSERT INTO ac_bots
      (id, bot_id, bot_name, owner_id, entity_id, is_delete, active_engine, bot_type, status, binding_id, env)
     VALUES (?, ?, ?, ?, ?, 0, 'openclaw', 'service', 'active', ?, ?)`,
    [292, botId, botId, userId, userId, 291, env],
  );
}

async function callback(taskId: string, stepId: string, body: Record<string, unknown>) {
  const response = await fetch(`${baseUrl}/api/evolve/internal/tasks/${taskId}/steps/${stepId}/report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return { response, body: await response.json() as Record<string, unknown> };
}

async function seedPlannedDiagnosis(taskId = "EV-PLANNED-SOURCE") {
  await repo.createTask({
    taskId, taskType: "diagnose", userId: "user-1", botId: "bot-1",
    taskName: "已完成规划", remark: null, configJson: "{}", createdBy: "owner-1",
  });
  await repo.createStep({ stepId: `${taskId}-DIAG`, taskId, stepType: "diagnose", stepNo: 1, command: "/clawevolve-diagnose" });
  await repo.updateStepStatus(`${taskId}-DIAG`, { status: "succeeded", output: diagnoseOutput() });
  await repo.createStep({ stepId: `${taskId}-PLAN`, taskId, stepType: "plan", stepNo: 2, command: "/clawevolve-plan" });
  await repo.updateStepStatus(`${taskId}-PLAN`, { status: "succeeded", output: {
    benchDomains: { trainBenchDomainId: "TRAIN-001", testBenchDomainId: "TEST-001" },
  } });
  return taskId;
}

function diagnoseOutput(summary = "issue", total = 1) {
  return {
    diagnosis: { summary },
    cases: {
      total, goodCount: 0, badCount: total,
      items: Array.from({ length: total }, (_, index) => ({
        caseId: `diagnose-case-${index + 1}`, type: "bad", summary: `case ${index + 1}`,
      })),
    },
  };
}

async function seedImprovement(
  identity: { ownerUserId?: string; botOwnerUserId?: string; createdBy?: string } = {},
) {
  const ownerUserId = identity.ownerUserId ?? "owner-1";
  const botOwnerUserId = identity.botOwnerUserId ?? ownerUserId;
  const result = await improvementRepo.create({
    ownerUserId,
    botOwnerUserId,
    botId: "bot-1",
    title: "修复工具环境缺失",
    userGuidance: "优先补齐必需环境变量。",
    sourceType: "USER_SELECTED",
    sourceRuleId: null,
    dataStartTime: "2026-07-25T00:00:00.000Z",
    dataEndTime: "2026-07-26T00:00:00.000Z",
    dataAsOf: "2026-07-26T03:00:00.000Z",
    batchId: "insight-20260726",
    contentFingerprint: `fingerprint-${ownerUserId}-${botOwnerUserId}`,
    idempotencyKey: `create-${ownerUserId}-${botOwnerUserId}`,
    createdBy: identity.createdBy ?? ownerUserId,
    evidence: [{
      sessionId: "session-insight-1",
      taskIndex: 0,
      ordinal: 0,
      taskDescription: "验证工具执行环境",
      failureClass: "TOOL_FAILURE",
      reasoningSummary: "缺少必需环境变量",
      messageRange: [0, 10],
      payloadRef: "oss://clawevolve-artifacts/evolution/pre/evidence/owner-1/bot-1/20260726/session-insight-1.json",
      payloadEtag: "etag-1",
      payloadVersionId: null,
      sourceDt: "20260726",
      batchId: "insight-20260726",
    }],
  });
  seededImprovementId = result.item.id;
  return result.item.id;
}

async function createDiagnosisFromImprovement(input: Partial<Record<string, unknown>> = {}, owner = "owner-1") {
  const response = await fetch(`${baseUrl}/api/evolve/diagnoses`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-User-Id": owner },
    body: JSON.stringify({
      taskName: "效果中心改进项诊断",
      remark: "来自效果中心",
      userId: owner,
      botId: "bot-1",
      apiKey: "temporary-secret",
      model: "GLM-5.1",
      diagnoseIntent: "扫描最近3天的历史 session；抽取4个 bad case 和1个 good case；重点关注影响任务完成率的主要问题。",
      improvementId: seededImprovementId,
      improvementRequestId: "insight-evolve-request-1",
      ...input,
    }),
  });
  return { response, body: await response.json() as Record<string, unknown> };
}

describe("ClawEvolve step protocol", () => {
  it("rejects API Judge for ARCA before creating or dispatching a task", async () => {
    await seedArcaBot();
    const response = await fetch(`${baseUrl}/api/evolve/diagnoses`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "user-1" },
      body: JSON.stringify({
        taskName: "ARCA API Judge", userId: "user-1", botId: "bot-arca", botEnv: "pre",
        judgeBackend: "api", apiKey: "must-not-enter-message", model: "GLM-5.1",
        diagnoseIntent: "扫描最近3天，抽取1个 bad case。",
      }),
    });
    expect(response.status).toBe(422);
    expect(await response.json()).toEqual(expect.objectContaining({ code: "ARCA_API_JUDGE_UNSUPPORTED" }));
    expect(dispatch).not.toHaveBeenCalled();
  });

  it("dispatches an ARCA business step directly without creating an initializer", async () => {
    await seedArcaBot();
    const response = await fetch(`${baseUrl}/api/evolve/diagnoses`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "user-1" },
      body: JSON.stringify({
        taskName: "ARCA Agent Judge", userId: "user-1", botId: "bot-arca", botEnv: "pre",
        judgeBackend: "subagent", model: "GLM-5.1",
        diagnoseIntent: "扫描最近3天，抽取1个 bad case。",
      }),
    });
    const body = await response.json() as Record<string, unknown>;
    expect(response.status).toBe(201);
    const steps = body.steps as Array<{ stepId: string; stepType: string; status: string; command: string }>;
    expect(steps.map((step) => step.stepType)).toEqual(["diagnose"]);
    expect(steps[0].command).not.toContain("api-key");
    expect(steps[0].status).toBe("dispatched");
    expect(dispatch).toHaveBeenCalledTimes(1);
    expect(dispatch).toHaveBeenLastCalledWith(expect.objectContaining({
      stepType: "diagnose",
      command: expect.stringContaining("--judge-backend subagent"),
    }));
  });

  it("retries a historical failed ARCA initializer by dispatching its pending business step", async () => {
    await seedArcaBot();
    await repo.createTask({
      taskId: "EV-ARCA-LEGACY", taskType: "diagnose", userId: "user-1", botId: "bot-arca",
      taskName: "ARCA Init Retry", configJson: JSON.stringify({ botEnv: "pre", dispatchMode: "message" }),
      createdBy: "user-1",
    });
    await repo.createStep({
      stepId: "STEP-ARCA-INIT", taskId: "EV-ARCA-LEGACY", stepType: "skill_init", stepNo: 0,
      command: "legacy initializer",
    });
    await repo.updateStepStatus("STEP-ARCA-INIT", {
      status: "failed", errorCode: "ARCA_SKILL_INIT_FAILED", errorMessage: "gateway restart failed", retryable: true,
    });
    await repo.createStep({
      stepId: "STEP-ARCA-DIAG", taskId: "EV-ARCA-LEGACY", stepType: "diagnose", stepNo: 1,
      command: "/clawevolve-diagnose --judge-backend subagent --task-id EV-ARCA-LEGACY --step-id STEP-ARCA-DIAG",
    });

    const retry = await fetch(`${baseUrl}/api/evolve/tasks/EV-ARCA-LEGACY/steps/STEP-ARCA-INIT/retry`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "user-1" },
      body: "{}",
    });
    const retryBody = await retry.json() as { step: { stepType: string; status: string; command: string } };
    expect(retry.status).toBe(201);
    expect(retryBody.step).toEqual(expect.objectContaining({ stepType: "diagnose", status: "dispatched" }));
    expect(retryBody.step.command).not.toContain("api-key");
    const savedSteps = await repo.listSteps("EV-ARCA-LEGACY");
    expect(savedSteps.filter((step) => step.step_type === "diagnose")).toHaveLength(1);
    expect(savedSteps.find((step) => step.step_type === "diagnose")?.status).toBe("dispatched");
    expect(dispatch).toHaveBeenLastCalledWith(expect.objectContaining({ stepType: "diagnose" }));
  });

  it("keeps an ARCA direct-runner Step alive when Gateway restart breaks the Bot Callback", async () => {
    await seedArcaBot();
    dispatch.mockResolvedValueOnce({
      runId: "run-arca-direct", sessionId: "session-arca-direct",
      platformResponse: { evolve_dispatch: { provider: "arca", transport: "message", runner_mode: "direct" } },
    });
    const createdResponse = await fetch(`${baseUrl}/api/evolve/diagnoses`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "user-1" },
      body: JSON.stringify({
        taskName: "ARCA Direct Callback Failure", userId: "user-1", botId: "bot-arca", botEnv: "pre",
        judgeBackend: "subagent", model: "GLM-5.1", diagnoseIntent: "扫描最近3天，抽取1个 bad case。",
      }),
    });
    const task = await createdResponse.json() as Record<string, unknown>;
    const businessStep = (task.steps as Array<{ stepId: string; stepType: string }>)[0];
    const transport = await fetch(
      `${baseUrl}/api/evolve/internal/tasks/${task.task_id}/steps/${businessStep.stepId}/bot-callback`,
      {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_id: "run-arca-direct", status: "FAILED", error: "gateway restarted" }),
      },
    );
    expect(transport.status).toBe(200);
    expect((await repo.findStep(businessStep.stepId))?.status).toBe("dispatched");
  });

  it("extracts a valid Runner startup receipt from an ARCA Callback without completing the Step", async () => {
    await seedArcaBot();
    dispatch.mockResolvedValueOnce({
      runId: "run-arca-direct", sessionId: "session-arca-direct",
      platformResponse: { evolve_dispatch: { provider: "arca", transport: "message", runner_mode: "direct" } },
    });
    const createdResponse = await fetch(`${baseUrl}/api/evolve/diagnoses`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "user-1" },
      body: JSON.stringify({
        taskName: "ARCA Runner Receipt", userId: "user-1", botId: "bot-arca", botEnv: "pre",
        judgeBackend: "subagent", model: "GLM-5.1", diagnoseIntent: "扫描最近3天，抽取1个 bad case。",
      }),
    });
    const task = await createdResponse.json() as Record<string, unknown>;
    const businessStep = (task.steps as Array<{ stepId: string; stepType: string }>)[0];
    const transport = await fetch(
      `${baseUrl}/api/evolve/internal/tasks/${task.task_id}/steps/${businessStep.stepId}/bot-callback`,
      {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          run_id: "run-arca-direct", status: "COMPLETED",
          result: { payloads: [{ text: JSON.stringify({
            ok: true, status: "started", pid: 321,
            task_id: task.task_id, step_id: businessStep.stepId,
          }) }] },
        }),
      },
    );
    expect(transport.status).toBe(200);
    expect(await transport.json()).toEqual(expect.objectContaining({
      runnerStart: expect.objectContaining({ status: "started", pid: 321 }),
    }));
    expect((await repo.findStep(businessStep.stepId))?.status).toBe("dispatched");
  });

  it("creates a Bench evolution task with train and test domains", async () => {
    for (const domainId of ["blog-train", "blog-test"]) {
      await benchDomainRepo.create({ domainId, name: domainId, ownerUserId: "user-1" });
      await benchTemplateRepo.create({
        domainId, templateName: `${domainId}-case`, displayName: domainId,
        ownerUserId: "user-1", status: "published",
      });
      await benchTemplateRepo.update("user-1", domainId, `${domainId}-case`, { publishedVersion: 1 });
    }
    const response = await fetch(`${baseUrl}/api/evolve/bench-optimizations`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-User-Id": "user-1" },
      body: JSON.stringify({
        taskName: "Blog Bench Evolution", userId: "user-1", botId: "bot-1",
        objective: "提升博客质量并保证测试集不回退",
        trainBenchDomainId: "blog-train", testBenchDomainId: "blog-test", maxRounds: 3,
      }),
    });
    const body = await response.json() as Record<string, unknown>;
    expect(response.status).toBe(201);
    expect(body.task_type).toBe("bench_optimize");
    const steps = body.steps as Array<{ stepType: string; command: string }>;
    expect(steps[0].stepType).toBe("bench_plan");
    expect(steps[0].command).toContain("/clawevolve-workflow --stage bench-plan");
    expect(steps[0].command).toContain("--train-domain-id blog-train");
    expect(steps[0].command).toContain("--test-domain-id blog-test");
    expect(steps[0].command).toContain("--owner-id user-1");
    expect(steps[0].command).not.toContain("--final-action loop");
    expect(steps[0].command).not.toContain("提升博客质量");
    const saved = await repo.findTask(String(body.task_id));
    const config = JSON.parse(saved!.config_json) as Record<string, unknown>;
    expect(config).toEqual(expect.objectContaining({
      trainBenchDomainId: "blog-train", testBenchDomainId: "blog-test",
      objective: "提升博客质量并保证测试集不回退",
      pinnedBenchDomains: expect.objectContaining({
        "blog-train": [{ templateName: "blog-train-case", templateVersion: 1 }],
        "blog-test": [{ templateName: "blog-test-case", templateVersion: 1 }],
      }),
    }));
    const taskId = String(body.task_id);
    const planStepId = String((body.steps as Array<{ stepId: string }>)[0].stepId);
    for (const [role, domainId] of [["train", "blog-train"], ["test", "blog-test"]] as const) {
      await benchRunRepo.create({
        benchRunId: `bench-${role}`, domainId, templateName: "__domain__", templateVersion: 0,
        ownerUserId: "user-1", status: "succeeded",
        runConfigJson: JSON.stringify({ evolveTaskId: taskId, evolveStepId: planStepId, role: `baseline_${role}` }),
      });
    }
    const findBenchRun = benchRunRepo.findByBenchRunId.bind(benchRunRepo);
    vi.spyOn(benchRunRepo, "findByBenchRunId").mockImplementation(async (benchRunId) => {
      const run = await findBenchRun(benchRunId);
      return run ? { ...run, owner_user_id: Buffer.from(String(run.owner_user_id)) } : null;
    });
    const progressed = await callback(taskId, planStepId, {
      status: "succeeded", summary: "Baseline 与 Spec v0 已完成",
      output: {
        objective: { text: "提升博客质量并保证测试集不回退", path: "/workspace/objective.md" },
        spec: {
          version: "v0", content_type: "text", content: "# Spec v0\n\n初始优化规格",
          path: "/workspace/spec-v0.md",
        },
        baseline: {
          train: { role: "train", producerStepId: planStepId, source: "generated", ownerUserId: "user-1", benchRunId: "bench-train", domainId: "blog-train", metrics: { score: 0.6 } },
          test: { role: "test", producerStepId: planStepId, source: "generated", ownerUserId: "user-1", benchRunId: "bench-test", domainId: "blog-test", metrics: { score: 0.5 } },
        },
      },
    });
    expect(progressed.response.status).toBe(200);
    expect(progressed.body.nextStep).toEqual(expect.objectContaining({ stepType: "optimize", roundNo: 1 }));
    const updatedSteps = await repo.listSteps(taskId);
    expect(updatedSteps).toHaveLength(2);
    expect(updatedSteps[1].command).toContain("/clawevolve-workflow --stage optimize");
    expect(updatedSteps[1].command).toContain("--train-bench-domain-id blog-train");
    expect(updatedSteps[1].command).toContain("--test-bench-domain-id blog-test");
  });

  it("creates a Bench task from a published domain using existing tables", async () => {
    await benchDomainRepo.create({ domainId: "blog-writing", name: "Blog Writing", ownerUserId: "user-1" });
    await benchTemplateRepo.create({
      domainId: "blog-writing", templateName: "write-blog", displayName: "写 Blog",
      ownerUserId: "user-1", status: "published",
    });
    await benchTemplateRepo.update("user-1", "blog-writing", "write-blog", { publishedVersion: 1 });
    await benchTemplateRepo.create({
      domainId: "blog-writing", templateName: "draft-blog", displayName: "草稿模板",
      ownerUserId: "user-1", status: "draft",
    });
    const response = await fetch(`${baseUrl}/api/evolve/benches`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "user-1" },
      body: JSON.stringify({
        taskName: "Blog Bench", userId: "user-1", botId: "bot-1",
        benchDomainId: "blog-writing", model: "antchat/GLM-5", suite: "all",
      }),
    });
    const body = await response.json() as Record<string, unknown>;
    expect(response.status).toBe(201);
    expect(body.task_type).toBe("bench");
    const steps = body.steps as Array<{ stepId: string; stepType: string; command: string }>;
    expect(steps[0]).toEqual(expect.objectContaining({ stepType: "bench" }));
    expect(steps[0].command).toContain("--domain-id blog-writing");
    expect(steps[0].command).toContain("--owner-id user-1");
    expect(steps[0].command).toContain("--model antchat/GLM-5");
    expect(steps[0].command).toContain("--suite all");
    expect(steps[0].command).toContain("--scene claw-evolve-bench");
    expect(steps[0].command).toContain("--template-version 1");
    expect(dispatch).toHaveBeenCalledWith(expect.objectContaining({
      stepType: "bench", userId: "user-1", botId: "bot-1",
      command: expect.stringContaining("/clawevolve-bench"),
    }));
    expect(await db.query("SELECT * FROM ce_tasks WHERE task_type = 'bench'")).toHaveLength(1);
    expect(await db.query("SELECT * FROM ce_steps WHERE step_type = 'bench'")).toHaveLength(1);
    const taskId = String(body.task_id);
    const inputResponse = await fetch(`${baseUrl}/api/evolve/internal/tasks/${taskId}/steps/${steps[0].stepId}/input`);
    const stepInput = await inputResponse.json() as { task: { config: { pinnedTemplates: unknown[] } } };
    expect(stepInput.task.config.pinnedTemplates).toEqual([{ templateName: "write-blog", templateVersion: 1 }]);
    // A shared Domain may be executed by a Task owned by another user. Bench
    // Run ownership remains the frozen template owner, not the Task initiator.
    await db.exec("UPDATE ce_tasks SET user_id = ? WHERE task_id = ?", ["task-initiator", taskId]);
    await benchRunRepo.create({
      benchRunId: "bench-wrong-domain", domainId: "other-domain", templateName: "__domain__",
      templateVersion: 0, ownerUserId: "user-1", status: "succeeded",
      runConfigJson: JSON.stringify({ evolveTaskId: taskId, evolveStepId: steps[0].stepId }),
    });
    const mismatched = await callback(taskId, steps[0].stepId, {
      status: "succeeded",
      output: { benchRunId: "bench-wrong-domain", domainId: "blog-writing", metrics: {}, detailUrl: "/wrong" },
    });
    expect(mismatched.response.status).toBe(422);
    expect((await repo.findTask(taskId))?.status).toBe("running");
    await benchRunRepo.create({
      benchRunId: "bench-blog-1", domainId: "blog-writing", templateName: "__domain__",
      templateVersion: 0, ownerUserId: "user-1", status: "running",
      runConfigJson: JSON.stringify({ evolveTaskId: taskId, evolveStepId: steps[0].stepId }),
    });
    await benchRunRepo.update("bench-blog-1", { status: "succeeded", score: 60, maxScore: 100, passRate: 0.6 });
    const completed = await callback(taskId, steps[0].stepId, {
      status: "succeeded", summary: "score 60/100",
      output: {
        benchRunId: "bench-blog-1", domainId: "blog-writing",
        metrics: { score: 60, maxScore: 100 },
        detailUrl: "/evolve/bench/runs/bench-blog-1",
      },
    });
    expect(completed.response.status).toBe(200);
    expect((await repo.findTask(taskId))?.status).toBe("completed");
    const savedStep = await repo.findStep(steps[0].stepId);
    expect(JSON.parse(savedStep!.output_json!)).toEqual(expect.objectContaining({
      domainId: "blog-writing",
      detailUrl: "/evolve/bench/runs/bench-blog-1",
      metrics: expect.objectContaining({ score: 60, maxScore: 100, scoreRatio: 0.6, passRate: 0.6 }),
    }));
  });

  it("persists a valid Optimize report with no ClawWeb warnings", async () => {
    const taskId = "EV-BENCH-LINKS";
    const stepId = "STEP-BENCH-LINKS";
    await repo.createTask({
      taskId, taskType: "optimize", userId: "user-1", botId: "bot-1",
      taskName: "Bench links", remark: null, configJson: JSON.stringify({ maxRounds: 1 }), createdBy: "user-1",
    });
    await repo.createStep({
      stepId, taskId, stepType: "optimize", stepNo: 1, roundNo: 1,
      command: "/clawevolve-workflow --stage optimize",
    });
    for (const [benchRunId, domainId, role] of [
      ["bench-baseline-train", "train-domain", "baseline_train"],
      ["bench-baseline-test", "test-domain", "baseline_test"],
      ["bench-candidate-train", "train-domain", "candidate_train"],
      ["bench-candidate-test", "test-domain", "candidate_test"],
    ] as const) {
      await benchRunRepo.create({
        benchRunId, domainId, templateName: "__domain__", templateVersion: 0,
        ownerUserId: "user-1", status: "succeeded",
        runConfigJson: JSON.stringify({ evolveTaskId: taskId, evolveStepId: stepId, role }),
      });
    }
    const report = await callback(taskId, stepId, {
      status: "succeeded",
      output: {
        baseline: {
          train: { role: "train", producerStepId: stepId, source: "generated", ownerUserId: "user-1", domainId: "train-domain", benchRunId: "bench-baseline-train", metrics: { score: 0.5 } },
          test: { role: "test", producerStepId: stepId, source: "generated", ownerUserId: "user-1", domainId: "test-domain", benchRunId: "bench-baseline-test", metrics: { score: 0.4 } },
        },
        metrics: [
          { role: "candidate_train", key: "train_task_completion_rate", value: 0.8, ownerUserId: "user-1", domainId: "train-domain", benchRunId: "bench-candidate-train" },
          { role: "candidate_test", key: "task_completion_rate", value: 0.7, ownerUserId: "user-1", domainId: "test-domain", benchRunId: "bench-candidate-test" },
        ],
        diff: { summary: "improved", files: [], artifact: { kind: "diff", ref: `oss://clawevolve-artifacts/evolution/${taskId}/rounds/round-001/diff.patch`, size: 1, sha256: "a".repeat(64), contentType: "text/x-diff; charset=utf-8" } },
        roundArtifacts: { manifestRef: `oss://clawevolve-artifacts/evolution/${taskId}/rounds/round-001/round-manifest.json` },
        roundDecision: { stop: true, reason: "target reached" },
        // Additive Bench/Review fields must survive the existing Bot output contract.
        benchDecision: "passed",
        reviewStatus: "fallback",
        scoreComparison: { name: "test_score", baseline: 0.4, candidate: 0.7, delta: 0.3 },
      },
    });

    expect(report.response.status).toBe(200);
    expect(report.body).toEqual(expect.objectContaining({ ok: true, status: "succeeded" }));
    expect(JSON.parse((await repo.findStep(stepId))!.output_json!)).toEqual(expect.objectContaining({
      benchDecision: "passed",
      reviewStatus: "fallback",
      scoreComparison: expect.objectContaining({ baseline: 0.4, candidate: 0.7, delta: 0.3 }),
      clawwebWarnings: [],
    }));
  });

  it("records reused Candidate Bench roles as warnings and schedules the explicitly requested next round", async () => {
    const taskId = "EV-OPTIMIZE-WARNING";
    const round1StepId = "STEP-OPTIMIZE-R1";
    const round2StepId = "STEP-OPTIMIZE-R2";
    await repo.createTask({
      taskId, taskType: "optimize", userId: "user-1", botId: "bot-1",
      taskName: "Optimize warnings", remark: null,
      configJson: JSON.stringify({
        maxRounds: 3, dispatchMode: "run",
        trainBenchDomainId: "train-domain", testBenchDomainId: "test-domain",
      }),
      createdBy: "user-1",
    });
    await repo.createStep({
      stepId: round1StepId, taskId, stepType: "optimize", stepNo: 1, roundNo: 1,
      command: "/clawevolve-workflow --stage optimize --round 1",
    });
    await repo.updateStepStatus(round1StepId, { status: "succeeded", output: { roundDecision: { stop: false } } });
    await repo.createStep({
      stepId: round2StepId, taskId, stepType: "optimize", stepNo: 2, roundNo: 2,
      command: "/clawevolve-workflow --stage optimize --round 2",
    });
    await repo.markDispatched(round2StepId, "run-r2", null, {});

    for (const [benchRunId, domainId, producerStepId, role] of [
      ["bench-r1-candidate-train", "train-domain", round1StepId, "candidate_train"],
      ["bench-r1-candidate-test", "test-domain", round1StepId, "candidate_test"],
      ["bench-r2-candidate-train", "train-domain", round2StepId, "candidate_train"],
      ["bench-r2-candidate-test", "test-domain", round2StepId, "candidate_test"],
    ] as const) {
      await benchRunRepo.create({
        benchRunId, domainId, templateName: "__domain__", templateVersion: 0,
        ownerUserId: "user-1", status: "succeeded",
        runConfigJson: JSON.stringify({ evolveTaskId: taskId, evolveStepId: producerStepId, role }),
      });
    }

    const report = await callback(taskId, round2StepId, {
      status: "succeeded",
      output: {
        baseline: {
          train: { role: "train", producerStepId: round1StepId, source: "reused", ownerUserId: "user-1", domainId: "train-domain", benchRunId: "bench-r1-candidate-train" },
          test: { role: "test", producerStepId: round1StepId, source: "reused", ownerUserId: "user-1", domainId: "test-domain", benchRunId: "bench-r1-candidate-test" },
        },
        metrics: [
          { role: "candidate_train", ownerUserId: "user-1", domainId: "train-domain", benchRunId: "bench-r2-candidate-train" },
          { role: "candidate_test", ownerUserId: "user-1", domainId: "test-domain", benchRunId: "bench-r2-candidate-test" },
        ],
        diff: { summary: "improved", files: [], artifact: { kind: "diff", ref: `oss://clawevolve-artifacts/evolution/${taskId}/rounds/round-002/diff.patch`, size: 1, sha256: "a".repeat(64), contentType: "text/x-diff; charset=utf-8" } },
        roundArtifacts: { manifestRef: `oss://clawevolve-artifacts/evolution/${taskId}/rounds/round-002/round-manifest.json` },
        roundDecision: { stop: false, reason: "continue" },
      },
    });

    expect(report.response.status).toBe(200);
    expect(report.body.nextStep).toEqual(expect.objectContaining({ stepType: "optimize", roundNo: 3 }));
    const saved = JSON.parse((await repo.findStep(round2StepId))!.output_json!);
    expect(saved.clawwebWarnings).toEqual(expect.arrayContaining([
      expect.objectContaining({ code: "OPTIMIZE_BASELINE_RUN_WARNING" }),
    ]));
    expect((await repo.listSteps(taskId)).filter((step) => step.step_type === "optimize" && step.round_no === 3)).toHaveLength(1);

    const duplicate = await callback(taskId, round2StepId, {
      status: "succeeded",
      output: saved,
    });
    expect(duplicate.response.status).toBe(200);
    expect((await repo.listSteps(taskId)).filter((step) => step.step_type === "optimize" && step.round_no === 3)).toHaveLength(1);
  });

  it("does not infer Optimize continuation when roundDecision.stop is missing", async () => {
    const taskId = "EV-OPTIMIZE-NO-DECISION";
    const stepId = "STEP-OPTIMIZE-NO-DECISION";
    await repo.createTask({
      taskId, taskType: "optimize", userId: "user-1", botId: "bot-1",
      taskName: "Missing decision", remark: null,
      configJson: JSON.stringify({ maxRounds: 3, dispatchMode: "run" }), createdBy: "user-1",
    });
    await repo.createStep({
      stepId, taskId, stepType: "optimize", stepNo: 1, roundNo: 1,
      command: "/clawevolve-workflow --stage optimize --round 1",
    });
    await repo.markDispatched(stepId, "run-r1", null, {});

    const report = await callback(taskId, stepId, {
      status: "succeeded",
      output: { diff: {}, metrics: [], baseline: {} },
    });

    expect(report.response.status).toBe(200);
    expect(report.body.nextStep).toBeNull();
    expect((await repo.findTask(taskId))?.status).toBe("completed");
    expect((await repo.listSteps(taskId)).filter((step) => step.step_type === "optimize")).toHaveLength(1);
    const saved = JSON.parse((await repo.findStep(stepId))!.output_json!);
    expect(saved.clawwebWarnings).toEqual(expect.arrayContaining([
      expect.objectContaining({ code: "OPTIMIZE_ROUND_DECISION_MISSING" }),
    ]));
  });

  it("enforces maxRounds even when the Skill explicitly requests another round", async () => {
    const taskId = "EV-OPTIMIZE-MAX-ROUNDS";
    const stepId = "STEP-OPTIMIZE-MAX-ROUNDS";
    await repo.createTask({
      taskId, taskType: "optimize", userId: "user-1", botId: "bot-1",
      taskName: "Max rounds", remark: null,
      configJson: JSON.stringify({ maxRounds: 1, dispatchMode: "run" }), createdBy: "user-1",
    });
    await repo.createStep({
      stepId, taskId, stepType: "optimize", stepNo: 1, roundNo: 1,
      command: "/clawevolve-workflow --stage optimize --round 1",
    });
    await repo.markDispatched(stepId, "run-r1", null, {});

    const report = await callback(taskId, stepId, {
      status: "succeeded",
      output: { diff: {}, metrics: [], baseline: {}, roundDecision: { stop: false, reason: "continue" } },
    });

    expect(report.response.status).toBe(200);
    expect(report.body.nextStep).toBeNull();
    expect((await repo.findTask(taskId))?.status).toBe("completed");
  });

  it("uses the node command as the single source for Bench model, suite and judge", async () => {
    await benchDomainRepo.create({ domainId: "custom-bench", name: "Custom", ownerUserId: "user-1" });
    await benchTemplateRepo.create({
      domainId: "custom-bench", templateName: "case", displayName: "Case",
      ownerUserId: "user-1", status: "published",
    });
    await benchTemplateRepo.update("user-1", "custom-bench", "case", { publishedVersion: 1 });
    const response = await fetch(`${baseUrl}/api/evolve/benches`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-User-Id": "user-1" },
      body: JSON.stringify({
        taskName: "Custom Bench", userId: "user-1", botId: "bot-1", benchDomainId: "custom-bench",
        model: "ignored/model", suite: "ignored-suite",
        nodeCommandYamls: { bench: `version: "1.0"\ncommand: /clawevolve-bench --model antchat/Custom --suite smoke --judge antchat/Judge\n` },
      }),
    });
    expect(response.status).toBe(201);
    const body = await response.json() as { config: { bench: { model: string; suite: string; judge: { model: string } } }; steps: Array<{ command: string }> };
    expect(body.config.bench).toEqual(expect.objectContaining({
      model: "antchat/Custom", suite: "smoke", judge: expect.objectContaining({ model: "antchat/Judge" }),
    }));
    expect(body.steps[0].command.match(/--judge/g)).toHaveLength(1);
  });

  it("rejects Bench tasks without owned published templates", async () => {
    await benchDomainRepo.create({ domainId: "empty", name: "Empty", ownerUserId: "user-1" });
    const response = await fetch(`${baseUrl}/api/evolve/benches`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-User-Id": "user-1" },
      body: JSON.stringify({ taskName: "Empty Bench", userId: "user-1", botId: "bot-1", benchDomainId: "empty" }),
    });
    expect(response.status).toBe(422);
    expect(dispatch).not.toHaveBeenCalled();
  });

  it("creates diagnose step and dispatches command with task-id and step-id", async () => {
    const { response, body } = await createDiagnosis();
    expect(response.status).toBe(201);
    const steps = body.steps as Array<{ stepId: string }>;
    expect(steps).toHaveLength(1);
    expect(dispatch).toHaveBeenCalledWith(expect.objectContaining({
      stepId: steps[0].stepId,
      stepType: "diagnose",
      userId: "user-1",
      botId: "bot-1",
      mode: "message",
      callbackUrl: expect.stringMatching(/\/api\/evolve\/internal\/tasks\/EV-.+\/steps\/STEP-.+\/bot-callback$/),
      command: expect.stringContaining(`--task-id ${String(body.task_id)} --step-id ${steps[0].stepId}`),
    }));
    expect(dispatch.mock.calls[0]?.[0].command).toContain("--model GLM-5.1");
    expect(JSON.stringify(body)).not.toContain("temporary-secret");
  });

  it("creates Bot Diagnose with the default Subagent Judge and no API key", async () => {
    const response = await fetch(`${baseUrl}/api/evolve/diagnoses`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({
        taskName: "Agent 诊断",
        userId: "user-1",
        botId: "bot-1",
        judgeBackend: "subagent",
        model: "GLM-5.1",
        maxSessions: 12,
        diagnoseIntent: "扫描最近3天的历史 session；抽取1个 bad case；重点关注任务未完成。",
      }),
    });
    expect(response.status).toBe(201);
    const body = await response.json() as { steps: Array<{ command: string }> };
    expect(body.steps[0].command).toContain("--judge-backend subagent");
    expect(body.steps[0].command).toContain("--max-sessions 12");
    expect(body.steps[0].command).not.toContain("--api-key");
    expect(dispatch).toHaveBeenLastCalledWith(expect.objectContaining({
      command: expect.stringContaining("--judge-backend subagent"),
    }));
    expect(dispatch.mock.calls.at(-1)?.[0]).not.toHaveProperty("secrets");
  });

  it("runs service Session diagnosis on the draft Bot and only passes export source arguments", async () => {
    await seedBaasDraftBotWithService();
    const response = await fetch(`${baseUrl}/api/evolve/diagnoses`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "user-1" },
      body: JSON.stringify({
        taskName: "服务 Session 诊断",
        userId: "user-1",
        botId: "bot-service-source",
        botEnv: "prod",
        sessionSource: "service_export",
        judgeBackend: "subagent",
        model: "GLM-5.1",
        maxSessions: 10,
        diagnoseIntent: "扫描服务态历史 session；抽取1个 bad case；重点关注任务未完成。",
      }),
    });
    expect(response.status).toBe(201);
    const body = await response.json() as { config: Record<string, unknown>; steps: Array<{ command: string }> };
    expect(body.config).toMatchObject({ sessionSource: { mode: "service_export" } });
    expect(body.steps[0].command).toContain("--source service_export");
    expect(body.steps[0].command).toContain("--source-user-id user-1");
    expect(body.steps[0].command).toContain("--source-bot-id bot-service-source");
    expect(body.steps[0].command).toContain("--source-download-network office");
    expect(dispatch).toHaveBeenLastCalledWith(expect.objectContaining({
      userId: "user-1",
      botId: "bot-service-source",
      stepType: "diagnose",
      runtime: expect.objectContaining({ provider: "baas", botType: "personal", hasServiceBot: true }),
    }));
  });

  it("uses the same service Session source for diagnose-first full evolution", async () => {
    await seedBaasDraftBotWithService();
    const response = await fetch(`${baseUrl}/api/evolve/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "user-1" },
      body: JSON.stringify({
        taskType: "full",
        inputMode: "diagnose_goal",
        taskName: "服务 Session 全流程",
        userId: "user-1",
        botId: "bot-service-source",
        botEnv: "prod",
        sessionSource: "service_export",
        judgeBackend: "subagent",
        model: "GLM-5.1",
        maxSessions: 10,
        maxRounds: 3,
        diagnoseIntent: "扫描服务态历史 session；抽取1个 bad case。",
        goal: "提升服务 Bot 任务完成率",
      }),
    });
    expect(response.status).toBe(201);
    const body = await response.json() as {
      config: Record<string, unknown>;
      steps: Array<{ stepType: string; command: string }>;
    };
    expect(body.config).toMatchObject({
      inputMode: "diagnose_goal",
      sessionSource: { mode: "service_export" },
    });
    expect(body.steps).toHaveLength(1);
    expect(body.steps[0].stepType).toBe("diagnose");
    expect(body.steps[0].command).toContain("--source service_export");
    expect(dispatch).toHaveBeenLastCalledWith(expect.objectContaining({
      userId: "user-1",
      botId: "bot-service-source",
      stepType: "diagnose",
    }));
  });

  it("runs a selected service Bot on its BaaS draft binding while exporting service Sessions", async () => {
    await seedServiceBotWithBaasDraftBinding();
    const response = await fetch(`${baseUrl}/api/evolve/diagnoses`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "user-1" },
      body: JSON.stringify({
        taskName: "服务 Bot 草稿态诊断",
        userId: "user-1",
        botId: "bot-service-runtime",
        botEnv: "prod",
        sessionSource: "local",
        judgeBackend: "subagent",
        model: "GLM-5.1",
        maxSessions: 10,
        diagnoseIntent: "扫描服务态历史 session；抽取1个 bad case。",
      }),
    });
    expect(response.status).toBe(201);
    const body = await response.json() as { config: Record<string, unknown>; steps: Array<{ command: string }> };
    expect(body.config).toMatchObject({
      dispatchMode: "run",
      lifecycleStage: "draft",
      sessionSource: { mode: "service_export" },
    });
    expect(body.steps[0].command).toContain("--source service_export");
    expect(dispatch).toHaveBeenLastCalledWith(expect.objectContaining({
      botId: "bot-service-runtime",
      mode: "run",
      runtime: expect.objectContaining({ botType: "service", provider: "baas" }),
    }));
  });

  it("allows API Judge through the draft BaaS binding of a selected service Bot", async () => {
    await seedServiceBotWithBaasDraftBinding();
    const response = await fetch(`${baseUrl}/api/evolve/diagnoses`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "user-1" },
      body: JSON.stringify({
        taskName: "服务 Bot API Judge",
        userId: "user-1",
        botId: "bot-service-runtime",
        botEnv: "prod",
        sessionSource: "service_export",
        judgeBackend: "api",
        apiKey: "temporary-secret",
        model: "GLM-5.1",
        maxSessions: 10,
        diagnoseIntent: "扫描服务态历史 session；抽取1个 bad case。",
      }),
    });
    expect(response.status).toBe(201);
    expect(dispatch).toHaveBeenLastCalledWith(expect.objectContaining({
      stepType: "diagnose",
      runtime: expect.objectContaining({ botType: "service", provider: "baas" }),
      secrets: { diagnoseApiKey: "temporary-secret" },
    }));
  });

  it("persists and dispatches a pure natural-language Diagnose intent with one case", async () => {
    const diagnoseIntent = "扫描最近7天的历史 session；抽取1个 bad case，不需要 good case；重点关注语雀 MCP 未调用和用户输入中的 '$(whoami)'。";
    const response = await fetch(`${baseUrl}/api/evolve/diagnoses`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({
        taskName: "单 Case 自然语言诊断",
        userId: "user-1",
        botId: "bot-1",
        apiKey: "temporary-secret",
        model: "GLM-5.2",
        diagnoseIntent,
      }),
    });

    expect(response.status).toBe(201);
    const body = await response.json() as {
      config: Record<string, unknown>;
      steps: Array<{ command: string }>;
    };
    expect(body.config).toEqual(expect.objectContaining({ diagnoseIntent }));
    expect(body.config).not.toHaveProperty("lookbackDays");
    expect(body.config).not.toHaveProperty("caseCount");
    expect(body.config).not.toHaveProperty("badCaseRatio");
    expect(body.steps[0].command).toContain("--intent");
    expect(body.steps[0].command).toContain("--max-sessions 10");
    expect(body.steps[0].command).toContain("抽取1个 bad case");
    expect(dispatch).toHaveBeenLastCalledWith(expect.objectContaining({
      command: expect.stringContaining(`'"'"'$(whoami)'"'"'`),
    }));
  });

  it("rejects an empty Diagnose intent", async () => {
    const response = await fetch(`${baseUrl}/api/evolve/diagnoses`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({
        taskName: "空诊断要求",
        userId: "user-1",
        botId: "bot-1",
        apiKey: "temporary-secret",
        model: "GLM-5.1",
        diagnoseIntent: "   ",
      }),
    });
    expect(response.status).toBe(400);
    expect((await response.json()).error).toBe("diagnoseIntent 不能为空");
  });

  it("completes Diagnose without creating Plan when zero cases are found", async () => {
    const { body } = await createDiagnosis();
    const diagnoseStep = (body.steps as Array<{ stepId: string }>)[0];
    const result = await callback(String(body.task_id), diagnoseStep.stepId, {
      status: "succeeded",
      summary: "未找到符合条件的 case",
      output: diagnoseOutput("insufficient evidence", 0),
    });

    expect(result.response.status).toBe(200);
    expect(result.body).toEqual(expect.objectContaining({
      status: "succeeded",
      nextStep: null,
      reason: "diagnose_no_cases",
    }));
    expect(dispatch).toHaveBeenCalledTimes(1);
    const taskResponse = await fetch(`${baseUrl}/api/evolve/tasks/${String(body.task_id)}`);
    const task = await taskResponse.json() as { status: string; steps: unknown[] };
    expect(task.status).toBe("completed");
    expect(task.steps).toHaveLength(1);
  });

  it("moves a completed Insight optimization into verification automatically", async () => {
    const improvementId = await seedImprovement();
    const taskId = "EV-INSIGHT-AUTO-APPLY";
    const stepId = "EV-INSIGHT-AUTO-APPLY-OPT";
    await repo.createTask({
      taskId,
      taskType: "full",
      userId: "owner-1",
      botId: "bot-1",
      taskName: "Insight 自动应用测试",
      configJson: JSON.stringify({ input: { type: "insight_improvement", improvementId }, maxRounds: 1 }),
      createdBy: "owner-1",
    });
    await repo.createStep({
      stepId,
      taskId,
      stepType: "optimize",
      stepNo: 1,
      roundNo: 1,
      command: "/clawevolve-workflow --stage optimize",
    });
    await improvementRepo.linkEvolveTask({
      improvementId,
      ownerUserId: "owner-1",
      evolveTaskId: taskId,
      requestId: "insight-auto-apply-request",
      createdBy: "owner-1",
    });

    const completed = await callback(taskId, stepId, {
      status: "succeeded",
      summary: "Pack 已应用",
      output: { applied: true },
    });
    expect(completed.response.status).toBe(200);
    expect(completed.body).toEqual(expect.objectContaining({
      status: "succeeded",
      nextStep: null,
    }));
    expect(await repo.findTask(taskId)).toEqual(expect.objectContaining({ status: "completed" }));

    const detail = await improvementRepo.getDetail("owner-1", improvementId);
    expect(detail).toEqual(expect.objectContaining({
      status: "IN_PROGRESS",
      verificationStatus: "PENDING",
      appliedEvolveTaskId: taskId,
      appliedBy: "claw-evolve",
      appliedAt: expect.anything(),
    }));

    const duplicate = await callback(taskId, stepId, { status: "succeeded" });
    expect(duplicate.response.status).toBe(200);
    expect((await improvementRepo.getDetail("owner-1", improvementId))?.version).toBe(detail?.version);
  });

  it("accepts GLM-5.2 and rejects unsupported diagnose models", async () => {
    const accepted = await createDiagnosis("GLM-5.2");
    expect(accepted.response.status).toBe(201);
    expect(dispatch).toHaveBeenLastCalledWith(expect.objectContaining({
      command: expect.stringContaining("--model GLM-5.2"),
    }));

    const rejected = await createDiagnosis("GLM-5");
    expect(rejected.response.status).toBe(400);
    expect(rejected.body.error).toBe("API Judge 的 model 必须是 GLM-5.1 或 GLM-5.2");
  });

  it("links a diagnosis task to the owned improvement item", async () => {
    const improvementId = await seedImprovement();
    const { response, body } = await createDiagnosisFromImprovement();
    expect(response.status).toBe(201);
    const link = await improvementRepo.findEvolveLinkByRequest(improvementId, "insight-evolve-request-1");
    expect(link).toEqual(expect.objectContaining({
      improvement_id: improvementId,
      evolve_task_id: body.task_id,
      created_by: "owner-1",
    }));
    const detail = await improvementRepo.getDetail("owner-1", improvementId);
    expect(detail).toEqual(expect.objectContaining({
      status: "IN_PROGRESS",
      version: 2,
      latestEvolveTaskId: body.task_id,
      latestEvolveTaskStatus: "running",
    }));
    expect(detail?.evolveLinks).toEqual([
      expect.objectContaining({ evolveTaskId: body.task_id, taskName: "效果中心改进项诊断" }),
    ]);
  });

  it("runs diagnosis in the Bot Owner space when the handler is a different user", async () => {
    const improvementId = await seedImprovement({
      ownerUserId: "specialist-1",
      botOwnerUserId: "owner-1",
      createdBy: "admin-1",
    });
    const { response, body } = await createDiagnosisFromImprovement(
      { userId: "owner-1" },
      "specialist-1",
    );
    expect(response.status).toBe(201);

    const task = await repo.findTask(String(body.task_id));
    expect(task).toEqual(expect.objectContaining({
      user_id: "owner-1",
      bot_id: "bot-1",
      created_by: "specialist-1",
    }));
    expect(dispatch).toHaveBeenCalledWith(expect.objectContaining({
      userId: "owner-1",
      botId: "bot-1",
    }));
    const link = await improvementRepo.findEvolveLinkByRequest(
      improvementId,
      "insight-evolve-request-1",
    );
    expect(link).toEqual(expect.objectContaining({ created_by: "specialist-1" }));

    const wrongSpace = await createDiagnosisFromImprovement(
      { userId: "specialist-1", improvementRequestId: "wrong-space" },
      "specialist-1",
    );
    expect(wrongSpace.response.status).toBe(403);
    expect(String(wrongSpace.body.error)).toContain("Bot Owner");

    const botOwnerCannotOperate = await createDiagnosisFromImprovement(
      { userId: "owner-1", improvementRequestId: "wrong-actor" },
      "owner-1",
    );
    expect(botOwnerCannotOperate.response.status).toBe(404);
  });

  it("lists mine by task creator instead of target Bot owner", async () => {
    await repo.createTask({
      taskId: "EV-CREATOR-SCOPE", taskType: "diagnose",
      userId: "target-owner", botId: "target-bot",
      taskName: "管理员代发任务", configJson: "{}", createdBy: "task-creator",
    });

    const creatorResponse = await fetch(`${baseUrl}/api/evolve/tasks?scope=mine`, {
      headers: { "X-User-Id": "task-creator" },
    });
    const creatorBody = await creatorResponse.json() as { tasks: Array<{ task_id: string }> };
    expect(creatorResponse.status).toBe(200);
    expect(creatorBody.tasks.map((task) => task.task_id)).toContain("EV-CREATOR-SCOPE");

    const targetResponse = await fetch(`${baseUrl}/api/evolve/tasks?scope=mine`, {
      headers: { "X-User-Id": "target-owner" },
    });
    const targetBody = await targetResponse.json() as { tasks: Array<{ task_id: string }> };
    expect(targetResponse.status).toBe(200);
    expect(targetBody.tasks.map((task) => task.task_id)).not.toContain("EV-CREATOR-SCOPE");
  });

  it("lists Bench diagnosis under diagnosis and keeps Bench optimization under optimization", async () => {
    await repo.createTask({
      taskId: "EV-BENCH-DIAGNOSIS", taskType: "bench",
      userId: "owner-1", botId: "bot-1",
      taskName: "Bench诊断", configJson: "{}", createdBy: "owner-1",
    });
    await repo.createTask({
      taskId: "EV-BENCH-OPTIMIZATION", taskType: "bench_optimize",
      userId: "owner-1", botId: "bot-1",
      taskName: "Bench优化", configJson: "{}", createdBy: "owner-1",
    });

    const diagnosisResponse = await fetch(`${baseUrl}/api/evolve/tasks?scope=mine&category=diagnosis`, {
      headers: { "X-User-Id": "owner-1" },
    });
    const diagnosisBody = await diagnosisResponse.json() as { tasks: Array<{ task_id: string }> };
    expect(diagnosisResponse.status).toBe(200);
    expect(diagnosisBody.tasks.map((task) => task.task_id)).toContain("EV-BENCH-DIAGNOSIS");
    expect(diagnosisBody.tasks.map((task) => task.task_id)).not.toContain("EV-BENCH-OPTIMIZATION");

    const optimizationResponse = await fetch(`${baseUrl}/api/evolve/tasks?scope=mine&category=optimization`, {
      headers: { "X-User-Id": "owner-1" },
    });
    const optimizationBody = await optimizationResponse.json() as { tasks: Array<{ task_id: string }> };
    expect(optimizationResponse.status).toBe(200);
    expect(optimizationBody.tasks.map((task) => task.task_id)).toContain("EV-BENCH-OPTIMIZATION");
    expect(optimizationBody.tasks.map((task) => task.task_id)).not.toContain("EV-BENCH-DIAGNOSIS");
  });

  it("keeps tasks private until the creator enables sharing", async () => {
    await repo.createTask({
      taskId: "EV-SHARING", taskType: "diagnose",
      userId: "target-owner", botId: "target-bot",
      taskName: "分享测试", configJson: "{}", createdBy: "task-creator",
    });

    const privateResponse = await fetch(`${baseUrl}/api/evolve/tasks/EV-SHARING`, {
      headers: { "X-User-Id": "other-user" },
    });
    expect(privateResponse.status).toBe(403);
    expect(await privateResponse.json()).toEqual(expect.objectContaining({
      code: "TASK_NOT_SHARED",
    }));

    const legacyOwnerResponse = await fetch(`${baseUrl}/api/evolve/tasks/EV-SHARING`, {
      headers: { "X-User-Id": "target-owner" },
    });
    expect(legacyOwnerResponse.status).toBe(200);

    const forbiddenUpdate = await fetch(`${baseUrl}/api/evolve/tasks/EV-SHARING/share`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", "X-User-Id": "other-user" },
      body: JSON.stringify({ shared: true }),
    });
    expect(forbiddenUpdate.status).toBe(403);

    const shareResponse = await fetch(`${baseUrl}/api/evolve/tasks/EV-SHARING/share`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", "X-User-Id": "task-creator" },
      body: JSON.stringify({ shared: true }),
    });
    expect(shareResponse.status).toBe(200);

    const publicResponse = await fetch(`${baseUrl}/api/evolve/tasks/EV-SHARING`, {
      headers: { "X-User-Id": "other-user" },
    });
    expect(publicResponse.status).toBe(200);
    const publicBody = await publicResponse.json() as { config: { shared?: boolean } };
    expect(publicBody.config.shared).toBe(true);
  });

  it("reuses the linked task when an improvement diagnosis request is retried", async () => {
    const improvementId = await seedImprovement();
    const first = await createDiagnosisFromImprovement();
    const duplicate = await createDiagnosisFromImprovement();
    expect(first.response.status).toBe(201);
    expect(duplicate.response.status).toBe(200);
    expect(duplicate.body.task_id).toBe(first.body.task_id);
    expect(duplicate.body.idempotent).toBe(true);
    expect(dispatch).toHaveBeenCalledTimes(1);
    expect(await repo.listTasks()).toHaveLength(1);
    const detail = await improvementRepo.getDetail("owner-1", improvementId);
    expect(detail).toEqual(expect.objectContaining({ status: "IN_PROGRESS", version: 2 }));
    expect(detail?.evolveLinks).toHaveLength(1);
  });

  it("allows only ACTIVE improvements to start a new diagnosis request", async () => {
    const improvementId = await seedImprovement();
    const first = await createDiagnosisFromImprovement();
    expect(first.response.status).toBe(201);

    const inProgress = await createDiagnosisFromImprovement({
      improvementRequestId: "insight-evolve-request-2",
    });
    expect(inProgress.response.status).toBe(409);
    expect(inProgress.body.code).toBe("IMPROVEMENT_STATE_CONFLICT");

    await improvementRepo.update("owner-1", improvementId, {
      status: "ARCHIVED",
      expectedVersion: 2,
    });
    const archived = await createDiagnosisFromImprovement({
      improvementRequestId: "insight-evolve-request-3",
    });
    expect(archived.response.status).toBe(409);
    expect(String(archived.body.error)).toContain("先恢复处理");

    await improvementRepo.update("owner-1", improvementId, {
      status: "RESOLVED",
      expectedVersion: 3,
    });
    const resolved = await createDiagnosisFromImprovement({
      improvementRequestId: "insight-evolve-request-4",
    });
    expect(resolved.response.status).toBe(409);
    expect(String(resolved.body.error)).toContain("已处理完成");
    expect(await repo.listTasks()).toHaveLength(1);
    expect(dispatch).toHaveBeenCalledTimes(1);
  });

  it("keeps the original request idempotent after the improvement is archived", async () => {
    const improvementId = await seedImprovement();
    const first = await createDiagnosisFromImprovement();
    await improvementRepo.update("owner-1", improvementId, {
      status: "ARCHIVED",
      expectedVersion: 2,
    });

    const duplicate = await createDiagnosisFromImprovement();
    expect(duplicate.response.status).toBe(200);
    expect(duplicate.body.task_id).toBe(first.body.task_id);
    expect(duplicate.body.idempotent).toBe(true);
    expect(await repo.listTasks()).toHaveLength(1);
    expect(dispatch).toHaveBeenCalledTimes(1);
  });

  it("rejects an improvement diagnosis before task creation when ownership or Bot does not match", async () => {
    await seedImprovement();
    const wrongOwner = await createDiagnosisFromImprovement({}, "owner-2");
    expect(wrongOwner.response.status).toBe(404);

    const wrongTaskUser = await createDiagnosisFromImprovement({ userId: "user-1" });
    expect(wrongTaskUser.response.status).toBe(403);

    const wrongBot = await createDiagnosisFromImprovement({ botId: "bot-2" });
    expect(wrongBot.response.status).toBe(422);
    expect(await repo.listTasks()).toHaveLength(0);
    expect(dispatch).not.toHaveBeenCalled();
  });

  it("creates a full-flow task with a real diagnose step", async () => {
    const response = await fetch(`${baseUrl}/api/evolve/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({
        taskType: "full",
        taskName: "全流程任务示例",
        userId: "user-1",
        botId: "bot-1",
        apiKey: "temporary-secret",
        model: "GLM-5.1",
        diagnoseIntent: "扫描最近3天的历史 session；抽取4个 bad case 和1个 good case；重点关注影响任务完成率的主要问题。",
        goal: "  将任务完成率提升到 90%\n以上  ",
      }),
    });
    expect(response.status).toBe(201);
    const body = await response.json() as {
      task_id: string;
      task_type: string;
      config: { goal?: string };
      steps: Array<{ stepType: string }>;
    };
    expect(body.task_id).toMatch(/^EV-/);
    expect(body.task_type).toBe("full");
    expect(body.task_name).toBe("全流程任务示例");
    expect(body.config.goal).toBe("将任务完成率提升到 90% 以上");
    expect(body.steps).toHaveLength(1);
    expect(body.steps[0].stepType).toBe("diagnose");
  });

  it("creates a direct-goal full task with Plan as the first business step", async () => {
    const response = await fetch(`${baseUrl}/api/evolve/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({
        taskType: "full",
        inputMode: "direct_goal",
        taskName: "一句话目标进化",
        userId: "user-1",
        botId: "bot-1",
        goal: "将工具调用任务完成率提升到 90% 以上",
        maxRounds: 3,
      }),
    });
    expect(response.status).toBe(201);
    const body = await response.json() as {
      task_id: string;
      config: { inputMode: string; goal: string; diagnoseIntent?: string; nodeCommands: Record<string, string> };
      steps: Array<{ stepType: string; command: string }>;
    };
    expect(body.config).toEqual(expect.objectContaining({
      inputMode: "direct_goal",
      goal: "将工具调用任务完成率提升到 90% 以上",
    }));
    expect(body.config.diagnoseIntent).toBeUndefined();
    expect(body.config.nodeCommands.diagnose).toBeUndefined();
    expect(body.steps).toHaveLength(1);
    expect(body.steps[0].stepType).toBe("plan");
    expect(body.steps[0].command).toContain("/clawevolve-plan");
    expect(body.steps[0].command).toContain("--goal '将工具调用任务完成率提升到 90% 以上'");
    expect(dispatch).toHaveBeenLastCalledWith(expect.objectContaining({
      stepType: "plan",
      command: body.steps[0].command,
    }));
  });

  it("rejects a direct-goal full task without a goal", async () => {
    const response = await fetch(`${baseUrl}/api/evolve/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({
        taskType: "full", inputMode: "direct_goal", taskName: "缺少目标",
        userId: "user-1", botId: "bot-1", maxRounds: 3,
      }),
    });
    expect(response.status).toBe(400);
    expect((await response.json()).error).toBe("按目标进化必须提供非空 goal");
    expect(dispatch).not.toHaveBeenCalled();
  });

  it("creates a full-flow task with the default Subagent Judge and no API key", async () => {
    const response = await fetch(`${baseUrl}/api/evolve/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({
        taskType: "full", taskName: "Agent 全流程", userId: "user-1", botId: "bot-1",
        judgeBackend: "subagent", model: "antchat/Custom-Judge",
        diagnoseIntent: "扫描最近3天的历史 session；抽取1个 bad case；重点关注任务未完成。",
        goal: "提升任务完成率",
      }),
    });
    expect(response.status).toBe(201);
    const body = await response.json() as { steps: Array<{ command: string }> };
    expect(body.steps[0].command).toContain("--judge-backend subagent");
    expect(body.steps[0].command).toContain("--model antchat/Custom-Judge");
    expect(body.steps[0].command).not.toContain("--api-key");
  });

  it("freezes YAML command templates and renders dates without persisting the API key", async () => {
    const goal = `优先修复工具调用失败的 '高频' 根因，完成率达到 90%，不要执行 $(whoami)`;
    const nodeCommandYamls = {
      diagnose: `version: "1.0"\ncommand: /clawevolve-diagnose --api-key {{api_key}} --model GLM-5 --range {{start_date}}..{{end_date}}\n`,
      plan: `version: "1.0"\ncommand: /clawevolve-plan --strategy conservative\n`,
    };
    const response = await fetch(`${baseUrl}/api/evolve/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({
        taskType: "full", taskName: "自定义命令任务",
        userId: "user-1", botId: "bot-1", apiKey: "temporary-secret",
        model: "GLM-5.1", diagnoseIntent: "扫描指定日期的历史 session；抽取4个 bad case 和1个 good case；重点关注工具调用失败。",
        startDate: "2026-07-28", endDate: "2026-07-31", goal, nodeCommandYamls,
      }),
    });
    expect(response.status).toBe(201);
    const task = await response.json() as {
      task_id: string; config: { goal: string; nodeCommands: Record<string, string> }; steps: Array<{ stepId: string; command: string }>;
    };
    expect(task.config.goal).toBe(goal);
    expect(task.config.nodeCommands.plan).toContain("--strategy conservative");
    expect(task.steps[0].command).toContain("--range 2026-07-28..2026-07-31");
    expect(JSON.stringify(task)).not.toContain("temporary-secret");
    expect(dispatch).toHaveBeenLastCalledWith(expect.objectContaining({
      command: expect.stringContaining("--api-key temporary-secret"),
    }));

    const diagnoseResult = await callback(task.task_id, task.steps[0].stepId, {
      status: "succeeded",
      output: diagnoseOutput(),
    });
    const planStep = diagnoseResult.body.nextStep as { stepId: string };
    expect(dispatch).toHaveBeenLastCalledWith(expect.objectContaining({
      stepType: "plan",
      command: `/clawevolve-plan --strategy conservative --task-id ${task.task_id} --step-id ${planStep.stepId} --owner-id user-1 --bot-id bot-1 --clawweb-url http://localhost:3001 --goal '优先修复工具调用失败的 '"'"'高频'"'"' 根因，完成率达到 90%，不要执行 $(whoami)'`,
    }));
  });

  it("passes a non-secret Diagnose command and an env secret to the BaaS dispatcher", async () => {
    vi.spyOn(repo, "resolveEvolveBotRuntime").mockResolvedValue({
      activeEngine: "openclaw",
      botType: "personal",
      hasServiceBot: false,
      botStatus: "active",
      bindingId: 1,
      provider: "baas",
      deviceId: "DEVICE-pre-1",
      bindingStatus: "active",
      env: "pre",
    });

    const { response } = await createDiagnosis();

    expect(response.status).toBe(201);
    const dispatched = dispatch.mock.calls.at(-1)?.[0];
    expect(dispatched.command).not.toContain("--api-key");
    expect(dispatched.command).not.toContain("temporary-secret");
    expect(dispatched.secrets).toEqual({ diagnoseApiKey: "temporary-secret" });
  });

  it("returns prior successful steps and exposes their output", async () => {
    const { body } = await createDiagnosis();
    const diagnoseExecution = (body.steps as Array<{ stepId: string }>)[0];
    const success = await callback(String(body.task_id), diagnoseExecution.stepId, {
      status: "succeeded",
      output: diagnoseOutput("issue", 5),
    });
    expect(success.response.status).toBe(200);
    const next = success.body.nextStep as { stepId: string; stepType: string };
    expect(next.stepType).toBe("plan");
    expect(dispatch).toHaveBeenLastCalledWith(expect.objectContaining({
      stepId: next.stepId,
      command: `/clawevolve-plan --task-id ${String(body.task_id)} --step-id ${next.stepId} --owner-id user-1 --bot-id bot-1 --clawweb-url http://localhost:3001`,
    }));

    const inputResponse = await fetch(`${baseUrl}/api/evolve/internal/tasks/${String(body.task_id)}/steps/${next.stepId}/input`);
    expect(inputResponse.status).toBe(200);
    const input = await inputResponse.json() as {
      inputs: { diagnose: { stepId: string; output: Record<string, unknown> } };
    };
    expect(input.inputs.diagnose.stepId).toBe(diagnoseExecution.stepId);
    expect(input.inputs.diagnose.output).toEqual(diagnoseOutput("issue", 5));
  });

  it("rejects succeeded callback without an object output", async () => {
    const { body } = await createDiagnosis();
    const step = (body.steps as Array<{ stepId: string }>)[0];
    const result = await callback(String(body.task_id), step.stepId, {
      status: "succeeded",
    });
    expect(result.response.status).toBe(422);
    expect(String(result.body.error)).toContain("output");
    expect(dispatch).toHaveBeenCalledTimes(1);
  });

  it("rejects benchTemplate from Diagnose output", async () => {
    const { body } = await createDiagnosis();
    const step = (body.steps as Array<{ stepId: string }>)[0];
    const result = await callback(String(body.task_id), step.stepId, {
      status: "succeeded", output: {
        ...diagnoseOutput(),
        cases: {
          ...(diagnoseOutput().cases),
          benchTemplate: { ownerUserId: "owner-1", domainId: "domain", templateName: "template" },
        },
      },
    });
    expect(result.response.status).toBe(422);
    expect(String(result.body.error)).toContain("尚未产生 Bench");
  });

  it("rejects Bench run references from Diagnose output", async () => {
    const { body } = await createDiagnosis();
    const step = (body.steps as Array<{ stepId: string }>)[0];
    const output = diagnoseOutput() as unknown as { cases: { items: Array<Record<string, unknown>> } };
    output.cases.items[0].benchRunId = "BENCH-RUN-001";
    const result = await callback(String(body.task_id), step.stepId, { status: "succeeded", output });
    expect(result.response.status).toBe(422);
    expect(String(result.body.error)).toContain("benchRunId");
  });

  it("rejects legacy benchRef from Diagnose output", async () => {
    const { body } = await createDiagnosis();
    const step = (body.steps as Array<{ stepId: string }>)[0];
    const output = diagnoseOutput() as unknown as { cases: { items: Array<Record<string, unknown>> } };
    output.cases.items[0].benchRef = { taskId: "bench-task-1" };
    const result = await callback(String(body.task_id), step.stepId, { status: "succeeded", output });
    expect(result.response.status).toBe(422);
    expect(String(result.body.error)).toContain("benchRef");
  });

  it("handles terminal report retries idempotently", async () => {
    const { body } = await createDiagnosis();
    const step = (body.steps as Array<{ stepId: string }>)[0];
    const event = {
      status: "succeeded",
      output: diagnoseOutput("issue", 5),
    };
    expect((await callback(String(body.task_id), step.stepId, event)).response.status).toBe(200);
    const duplicate = await callback(String(body.task_id), step.stepId, event);
    expect(duplicate.response.status).toBe(200);
    expect(duplicate.body.duplicate).toBe(true);
  });

  it("allows a succeeded step to revise its summary and output without advancing again", async () => {
    const { body } = await createDiagnosis();
    const step = (body.steps as Array<{ stepId: string }>)[0];
    const first = await callback(String(body.task_id), step.stepId, {
      status: "succeeded",
      summary: "初次诊断结果",
      output: diagnoseOutput("初次结论", 5),
    });
    expect(first.response.status).toBe(200);
    expect(dispatch).toHaveBeenCalledTimes(2);

    const revised = await callback(String(body.task_id), step.stepId, {
      status: "succeeded",
      summary: "修订后的诊断结果",
      output: diagnoseOutput("修订后的结论", 6),
    });
    expect(revised.response.status).toBe(200);
    expect(revised.body).toEqual(expect.objectContaining({
      duplicate: false, revised: true, nextStep: null,
    }));
    expect(dispatch).toHaveBeenCalledTimes(2);

    const response = await fetch(`${baseUrl}/api/evolve/tasks/${String(body.task_id)}`);
    const task = await response.json() as {
      steps: Array<{ stepId: string; summary: string; output: Record<string, unknown> }>;
    };
    const persisted = task.steps.find((item) => item.stepId === step.stepId);
    expect(persisted?.summary).toBe("修订后的诊断结果");
    expect(persisted?.output).toEqual(diagnoseOutput("修订后的结论", 6));
  });

  it("accepts a minimal command report", async () => {
    const { body } = await createDiagnosis();
    const step = (body.steps as Array<{ stepId: string }>)[0];
    const result = await callback(String(body.task_id), step.stepId, {
      status: "succeeded",
      summary: "minimal diagnose report",
      output: diagnoseOutput("issue", 5),
    });
    expect(result.response.status).toBe(200);
    expect(result.body).toEqual(expect.objectContaining({
      ok: true, duplicate: false, status: "succeeded",
    }));
  });

  it("keeps bot callback transport-only", async () => {
    const { body } = await createDiagnosis();
    const step = (body.steps as Array<{ stepId: string }>)[0];
    const response = await fetch(
      `${baseUrl}/api/evolve/internal/tasks/${String(body.task_id)}/steps/${step.stepId}/bot-callback`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          run_id: "run-bot-1",
          bot_id: "bot-1:user-1",
          status: "COMPLETED",
          result: "diagnosis completed",
          error: null,
          metadata: { key: "value" },
        }),
      },
    );
    expect(response.status).toBe(200);
    const callbackBody = await response.json() as {
      transportStatus: string;
    };
    expect(callbackBody.transportStatus).toBe("COMPLETED");
    expect(dispatch).toHaveBeenCalledTimes(1);
  });

  it("advances a full task from plan output to optimize round 1", async () => {
    const response = await fetch(`${baseUrl}/api/evolve/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({
        taskType: "full", taskName: "全流程优化测试", userId: "user-1", botId: "bot-1",
        apiKey: "temporary-secret", model: "GLM-5.1",
        diagnoseIntent: "扫描最近3天的历史 session；抽取4个 bad case 和1个 good case；重点关注影响任务完成率的主要问题。", maxRounds: 3,
        goal: "优先解决诊断阶段识别出的高频根因",
      }),
    });
    const task = await response.json() as { task_id: string; steps: Array<{ stepId: string }> };
    const diagnoseStep = task.steps[0];
    const diagnoseResult = await callback(task.task_id, diagnoseStep.stepId, {
      status: "succeeded",
      output: diagnoseOutput(),
    });
    const planStep = diagnoseResult.body.nextStep as { stepId: string };
    await benchDomainRepo.create({ domainId: "TRAIN-001", name: "Train", ownerUserId: "user-1" });
    await benchDomainRepo.create({ domainId: "TEST-001", name: "Test", ownerUserId: "user-1" });
    await benchTemplateRepo.create({
      domainId: "TRAIN-001", templateName: "bench-task-1", displayName: "Case 1",
      ownerUserId: "user-1", status: "published",
    });
    await benchTemplateRepo.update("user-1", "TRAIN-001", "bench-task-1", { publishedVersion: 1 });
    const planResult = await callback(task.task_id, planStep.stepId, {
      status: "succeeded",
      output: {
        goal: {},
        spec: { version: "v0", content_type: "text", content: "初始优化规格" },
        benchCases: {
          trainCount: 1, testCount: 0,
          items: [{
            sourceCaseId: "diagnose-case-1", taskId: "bench-task-1", split: "train",
            template: { ownerUserId: "user-1", domainId: "TRAIN-001", templateName: "bench-task-1", version: 1 },
          }],
        },
        benchDomains: {
          trainBenchDomainId: "TRAIN-001",
          testBenchDomainId: "TEST-001",
        },
      },
    });
    expect(planResult.response.status).toBe(200);
    expect(planResult.body.nextStep).toEqual(expect.objectContaining({
      stepType: "optimize",
      roundNo: 1,
    }));
    expect(dispatch).toHaveBeenLastCalledWith(expect.objectContaining({
      stepType: "optimize",
      command: expect.stringMatching(
        /\/clawevolve-workflow --stage optimize .+--task-id EV-.+ --step-id STEP-.+ --round 1 --train-bench-domain-id TRAIN-001 --test-bench-domain-id TEST-001 --clawweb-url http:\/\/localhost:3001 --openclaw-execution-mode local --owner-id user-1/,
      ),
    }));
    expect(String(dispatch.mock.calls.at(-1)?.[0]?.command)).toContain("--owner-id user-1");

    const optimizeStep = planResult.body.nextStep as { stepId: string };
    // Simulate a Step created before owner-id became a required system argument.
    await db.exec(
      "UPDATE ce_steps SET command = REPLACE(command, ' --owner-id user-1', '') WHERE step_id = ?",
      [optimizeStep.stepId],
    );
    await callback(task.task_id, optimizeStep.stepId, {
      status: "failed",
      error: { code: "BENCH_FAILED", message: "historical command retry", retryable: true },
    });
    const retryResponse = await fetch(
      `${baseUrl}/api/evolve/tasks/${task.task_id}/steps/${optimizeStep.stepId}/retry`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
    );
    expect(retryResponse.status).toBe(201);
    expect(dispatch).toHaveBeenLastCalledWith(expect.objectContaining({
      stepType: "optimize",
      command: expect.stringMatching(/--owner-id user-1$/),
    }));
  });

  it("rejects an optimization without a planned diagnosis task", async () => {
    const response = await fetch(`${baseUrl}/api/evolve/optimizations`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({
        taskName: "无诊断任务的调试优化",
        userId: "user-1",
        botId: "bot-1",
        sourceDiagnosisTaskIds: [],
        maxRounds: 3,
      }),
    });
    expect(response.status).toBe(400);
    await expect(response.json()).resolves.toEqual({ error: "诊断进化必须选择一个已完成 Plan 的诊断任务" });
  });

  it("rejects workflow control parameters in a node YAML", async () => {
    const sourceTaskId = await seedPlannedDiagnosis("EV-CONTROL-SOURCE");
    const response = await fetch(`${baseUrl}/api/evolve/optimizations`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({
        taskName: "非法控制参数", userId: "user-1", botId: "bot-1",
        sourceDiagnosisTaskIds: [sourceTaskId], maxRounds: 3,
        nodeCommandYamls: {
          optimize: `version: "1.0"\ncommand: /clawevolve-workflow --stage optimize --action prepare\n`,
        },
      }),
    });
    expect(response.status).toBe(400);
    await expect(response.json()).resolves.toEqual(expect.objectContaining({
      error: expect.stringContaining("系统参数"),
    }));
  });

  it("publishes task node definitions from the server registry", async () => {
    const response = await fetch(`${baseUrl}/api/evolve/task-definitions`);
    expect(response.status).toBe(200);
    const body = await response.json() as {
      tasks: Array<{ type: string; nodes: Array<{ key: string; defaultCommand: string }> }>;
      variants: { insight_improvement: Array<{ key: string }> };
    };
    expect(body.tasks.find((item) => item.type === "bench_optimize")?.nodes).toEqual([
      expect.objectContaining({ key: "bench_plan", defaultCommand: expect.stringContaining("--stage bench-plan") }),
      expect.objectContaining({ key: "optimize", defaultCommand: expect.stringContaining("--stage optimize") }),
    ]);
    expect(body.variants.insight_improvement.map((item) => item.key)).toEqual(["plan", "optimize"]);
  });

  it("accepts 100 optimization rounds and rejects values outside 1 to 100", async () => {
    const sourceTaskId = await seedPlannedDiagnosis();
    const accepted = await fetch(`${baseUrl}/api/evolve/optimizations`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({
        taskName: "百轮优化测试", userId: "user-1", botId: "bot-1",
        sourceDiagnosisTaskIds: [sourceTaskId], maxRounds: 100,
      }),
    });
    expect(accepted.status).toBe(201);
    const acceptedBody = await accepted.json() as { config: { maxRounds: number } };
    expect(acceptedBody.config.maxRounds).toBe(100);

    for (const maxRounds of [0, 101, 1.5]) {
      const rejected = await fetch(`${baseUrl}/api/evolve/optimizations`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
        body: JSON.stringify({
          taskName: "非法轮数测试", userId: "user-1", botId: "bot-1",
          sourceDiagnosisTaskIds: [sourceTaskId], maxRounds,
        }),
      });
      expect(rejected.status).toBe(400);
      await expect(rejected.json()).resolves.toEqual({ error: "maxRounds 必须是 1 到 100 的整数" });
    }
  });

  it("continues a failed step by creating and dispatching a new step", async () => {
    const { body } = await createDiagnosis();
    const failedStep = (body.steps as Array<{ stepId: string }>)[0];
    const failed = await callback(String(body.task_id), failedStep.stepId, {
      status: "failed",
      error: { code: "TIMEOUT", message: "Bot execution timed out", retryable: true },
    });
    expect(failed.response.status).toBe(200);

    const response = await fetch(
      `${baseUrl}/api/evolve/tasks/${String(body.task_id)}/steps/${failedStep.stepId}/retry`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
        body: JSON.stringify({ apiKey: "replacement-secret" }),
      },
    );
    expect(response.status).toBe(201);
    const retried = await response.json() as { step: { stepId: string; stepType: string; status: string; command: string } };
    expect(retried.step.stepId).not.toBe(failedStep.stepId);
    expect(retried.step.stepType).toBe("diagnose");
    expect(retried.step.status).toBe("dispatched");
    expect(retried.step.command).not.toContain("replacement-secret");
    expect(dispatch).toHaveBeenLastCalledWith(expect.objectContaining({
      stepId: retried.step.stepId,
      command: expect.stringContaining("--api-key replacement-secret"),
    }));

    const taskResponse = await fetch(`${baseUrl}/api/evolve/tasks/${String(body.task_id)}`);
    const task = await taskResponse.json() as { status: string; steps: Array<{ stepId: string; status: string }> };
    expect(task.status).toBe("running");
    expect(task.steps).toEqual(expect.arrayContaining([
      expect.objectContaining({ stepId: failedStep.stepId, status: "failed" }),
      expect.objectContaining({ stepId: retried.step.stepId, status: "dispatched" }),
    ]));
  });

  it("retries a Subagent Judge Diagnose without requesting an API key", async () => {
    const created = await fetch(`${baseUrl}/api/evolve/diagnoses`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({
        taskName: "Agent 重试", userId: "user-1", botId: "bot-1",
        judgeBackend: "subagent", model: "GLM-5.1",
        diagnoseIntent: "扫描最近3天的历史 session；抽取1个 bad case；重点关注任务未完成。",
      }),
    });
    const task = await created.json() as { task_id: string; steps: Array<{ stepId: string }> };
    await callback(task.task_id, task.steps[0].stepId, {
      status: "failed", error: { code: "TIMEOUT", message: "timeout", retryable: true },
    });

    const response = await fetch(
      `${baseUrl}/api/evolve/tasks/${task.task_id}/steps/${task.steps[0].stepId}/retry`,
      { method: "POST", headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" }, body: "{}" },
    );
    expect(response.status).toBe(201);
    const retried = await response.json() as { step: { command: string } };
    expect(retried.step.command).toContain("--judge-backend subagent");
    expect(retried.step.command).not.toContain("--api-key");
  });

  it("keeps a retried task failed when dispatching the new step fails", async () => {
    const { body } = await createDiagnosis();
    const failedStep = (body.steps as Array<{ stepId: string }>)[0];
    await callback(String(body.task_id), failedStep.stepId, {
      status: "failed",
      error: { code: "TIMEOUT", message: "Bot execution timed out", retryable: true },
    });
    dispatch.mockRejectedValueOnce(new Error("retry dispatch failed"));

    const response = await fetch(
      `${baseUrl}/api/evolve/tasks/${String(body.task_id)}/steps/${failedStep.stepId}/retry`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
        body: JSON.stringify({ apiKey: "replacement-secret" }),
      },
    );
    expect(response.status).toBe(201);
    const retried = await response.json() as { step: { status: string } };
    expect(retried.step.status).toBe("failed");
    expect((await repo.findTask(String(body.task_id)))?.status).toBe("failed");
  });

  it("logically cancels a running step and idempotently ignores repeated late reports", async () => {
    const { body } = await createDiagnosis();
    const step = (body.steps as Array<{ stepId: string }>)[0];
    const cancelResponse = await fetch(
      `${baseUrl}/api/evolve/tasks/${String(body.task_id)}/steps/${step.stepId}/cancel`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason: "人工终止调试" }) },
    );
    expect(cancelResponse.status).toBe(200);
    const canceled = await cancelResponse.json() as { step: { status: string; error: { code: string; message: string } } };
    expect(canceled.step.status).toBe("canceled");
    expect(canceled.step.error).toEqual(expect.objectContaining({ code: "USER_CANCELED", message: "人工终止调试" }));
    expect(cancelExecution).toHaveBeenCalledWith(expect.objectContaining({
      taskId: String(body.task_id), stepId: step.stepId, stepType: "diagnose", sessionId: "session-1",
    }));
    expect((await repo.findTask(String(body.task_id)))?.status).toBe("canceled");

    for (let attempt = 0; attempt < 2; attempt += 1) {
      const lateReport = await callback(String(body.task_id), step.stepId, {
        status: "succeeded",
        output: diagnoseOutput(),
      });
      expect(lateReport.response.status).toBe(200);
      expect(lateReport.body).toEqual(expect.objectContaining({
        ok: true, duplicate: true, ignored: true, status: "canceled", nextStep: null,
      }));
    }
    expect(dispatch).toHaveBeenCalledTimes(1);
  });

  it("keeps cancellation authoritative when the remote stop fails and still allows retry", async () => {
    const taskId = "EV-CANCEL-REMOTE-FAILURE";
    const stepId = "STEP-CANCEL-REMOTE-FAILURE";
    await repo.createTask({
      taskId, taskType: "full", userId: "user-1", botId: "bot-1",
      taskName: "远端停止失败", remark: null,
      configJson: JSON.stringify({ dispatchMode: "run" }), createdBy: "owner-1",
    });
    await repo.createStep({
      stepId, taskId, stepType: "plan", stepNo: 1,
      command: `/clawevolve-plan --task-id ${taskId} --step-id ${stepId}`,
    });
    await repo.markDispatched(stepId, "message-1", "session-1", {
      evolve_dispatch: { provider: "baas", transport: "message", environment: "pre" },
    });
    cancelExecution.mockImplementationOnce(async () => {
      expect((await repo.findStep(stepId))?.status).toBe("canceled");
      throw new Error("Message session no longer exists");
    });

    const cancelResponse = await fetch(
      `${baseUrl}/api/evolve/tasks/${taskId}/steps/${stepId}/cancel`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "BaaS 已超时，准备重试" }),
      },
    );

    expect(cancelResponse.status).toBe(200);
    const canceled = await cancelResponse.json() as {
      step: { status: string; botResponse: Record<string, unknown> };
      cancellation: { status: string; error: string };
    };
    expect(canceled.step.status).toBe("canceled");
    expect(canceled.cancellation).toEqual(expect.objectContaining({
      status: "remote_stop_failed",
      error: "Message session no longer exists",
    }));
    expect(canceled.step.botResponse).toEqual(expect.objectContaining({
      evolve_cancel: expect.objectContaining({
        status: "remote_stop_failed",
        error: "Message session no longer exists",
      }),
    }));
    expect((await repo.findTask(taskId))?.status).toBe("canceled");

    const retryResponse = await fetch(
      `${baseUrl}/api/evolve/tasks/${taskId}/steps/${stepId}/retry`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
    );
    expect(retryResponse.status).toBe(201);
    const retried = await retryResponse.json() as { step: { status: string; stepId: string } };
    expect(retried.step.status).toBe("dispatched");
    expect(retried.step.stepId).not.toBe(stepId);
  });

  it("issues a one-day task-scoped upload URL without exposing OSS credentials", async () => {
    await repo.createTask({
      taskId: "EV-ARTIFACT", taskType: "pack", userId: "owner-1", botId: "bot-1",
      taskName: "Artifact URL", remark: null, configJson: "{}", createdBy: "owner-1",
    });
    await repo.createStep({
      stepId: "STEP-ARTIFACT", taskId: "EV-ARTIFACT", stepType: "pack", stepNo: 1,
      command: "/clawevolve-pack --mode pack",
    });
    const response = await fetch(
      `${baseUrl}/api/evolve/internal/tasks/EV-ARTIFACT/steps/STEP-ARTIFACT/artifacts/upload-url`,
      {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          kind: "snapshot-pack", size: 12, sha256: "a".repeat(64),
          contentType: "application/zip",
        }),
      },
    );
    expect(response.status).toBe(200);
    const body = await response.json() as Record<string, unknown>;
    expect(body).toEqual(expect.objectContaining({
      method: "PUT", url: "https://oss.example.test/signed", expiresInSeconds: 86_400,
      artifact: expect.objectContaining({
        ref: "oss://clawevolve-artifacts/evolution/EV-ARTIFACT/snapshots/artifact.zip",
        size: 12, sha256: "a".repeat(64), contentType: "application/zip",
      }),
    }));
    expect(JSON.stringify(body)).not.toMatch(/OSS_AK|OSS_SK|accessKey/i);
    expect(createSignedUrl).toHaveBeenCalledWith(
      "evolution/EV-ARTIFACT/snapshots/artifact.zip", "PUT", 86_400,
      {},
    );
  });

  it("rejects baseline publication from an Optimize round after Round 1", async () => {
    await repo.createTask({
      taskId: "EV-BASELINE", taskType: "optimize", userId: "owner-1", botId: "bot-1",
      taskName: "Baseline", remark: null, configJson: "{}", createdBy: "owner-1",
    });
    await repo.createStep({
      stepId: "STEP-ROUND-2", taskId: "EV-BASELINE", stepType: "optimize", stepNo: 2,
      roundNo: 2, command: "/clawevolve-workflow --stage optimize",
    });
    const response = await fetch(
      `${baseUrl}/api/evolve/internal/tasks/EV-BASELINE/steps/STEP-ROUND-2/artifacts/upload-url`,
      {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          kind: "baseline-pack", size: 12, sha256: "a".repeat(64),
          contentType: "application/zip",
        }),
      },
    );
    expect(response.status).toBe(422);
    await expect(response.json()).resolves.toEqual({ error: "Baseline Artifact 只能由第 1 轮 Optimize Step 上传" });
    expect(createSignedUrl).not.toHaveBeenCalled();
  });

  it("returns an explicit baseline source instead of inferring it from round number", async () => {
    await repo.createTask({
      taskId: "EV-PACK-LIST", taskType: "optimize", userId: "owner-1", botId: "bot-1",
      taskName: "Pack list", remark: null, configJson: "{}", createdBy: "owner-1",
    });
    await repo.createStep({
      stepId: "STEP-PACK-LIST", taskId: "EV-PACK-LIST", stepType: "optimize", stepNo: 1,
      roundNo: 1, command: "/clawevolve-workflow --stage optimize",
    });
    await repo.updateStepStatus("STEP-PACK-LIST", {
      status: "succeeded", output: {
        baselineArtifact: { status: "available", artifact: {
          kind: "baseline_pack", ref: "oss://clawevolve-artifacts/evolution/EV-PACK-LIST/baseline/artifact_v0.zip",
          size: 12, sha256: "a".repeat(64), contentType: "application/zip",
        } },
      },
    });
    await repo.registerPack({
      pack_id: "PACK-BASELINE", user_id: "owner-1", bot_id: "bot-1",
      source_task_id: "EV-PACK-LIST", source_step_id: "STEP-PACK-LIST",
      source_kind: "baseline", source_round: 0,
      artifact_ref: "oss://clawevolve-artifacts/evolution/EV-PACK-LIST/baseline/artifact_v0.zip",
      artifact_size: 12, artifact_sha256: "a".repeat(64), artifact_content_type: "application/zip",
    });
    const response = await fetch(`${baseUrl}/api/evolve/packs?botId=bot-1`, { headers: { "X-User-Id": "owner-1" } });
    expect(response.status).toBe(200);
    const body = await response.json() as { items: Array<Record<string, unknown>> };
    expect(body.items).toEqual([expect.objectContaining({
      packId: "PACK-BASELINE", taskId: "EV-PACK-LIST", sourceKind: "baseline", sourceRound: null,
    })]);
  });

  it("lists accepted and rejected Optimize rounds as reviewable versions without changing Pack APIs", async () => {
    await repo.createTask({
      taskId: "EV-VERSION-REVIEW", taskType: "full", userId: "owner-1", botId: "bot-1",
      taskName: "Version review", remark: null, configJson: "{}", createdBy: "owner-1",
    });
    await repo.createStep({
      stepId: "STEP-VERSION-R1", taskId: "EV-VERSION-REVIEW", stepType: "optimize", stepNo: 1,
      roundNo: 1, command: "/clawevolve-workflow --stage optimize",
    });
    await repo.createStep({
      stepId: "STEP-VERSION-R2", taskId: "EV-VERSION-REVIEW", stepType: "optimize", stepNo: 2,
      roundNo: 2, command: "/clawevolve-workflow --stage optimize",
    });
    await repo.createStep({
      stepId: "STEP-VERSION-R3", taskId: "EV-VERSION-REVIEW", stepType: "optimize", stepNo: 3,
      roundNo: 3, command: "/clawevolve-workflow --stage optimize",
    });
    const baselineArtifact = {
      ref: "oss://clawevolve-artifacts/evolution/EV-VERSION-REVIEW/baseline/artifact.zip",
      size: 11, sha256: "a".repeat(64), contentType: "application/zip",
    };
    const roundArtifact = {
      ref: "oss://clawevolve-artifacts/evolution/EV-VERSION-REVIEW/rounds/round-001/artifact.zip",
      size: 12, sha256: "b".repeat(64), contentType: "application/zip",
    };
    await repo.updateStepStatus("STEP-VERSION-R1", {
      status: "succeeded", output: {
        benchDecision: "passed", accepted: true, promotionStatus: "succeeded", reviewStatus: "success",
        scoreComparison: { name: "test_score", baseline: 0.6, candidate: 0.7, delta: 0.1 },
        diff: { summary: "improved", files: [{ path: "skills-local/demo/SKILL.md", change: "modified" }] },
        spec: { version: "v1", content_type: "text", content: "# Spec" },
        pack: { status: "available", artifact: roundArtifact },
      },
    });
    await repo.updateStepStatus("STEP-VERSION-R2", {
      status: "succeeded", output: {
        benchDecision: "not_improved", accepted: false, promotionStatus: "not_started", reviewStatus: "success",
        scoreComparison: { name: "test_score", baseline: 0.7, candidate: 0.5, delta: -0.2 },
        diff: { summary: "regressed", files: [{ path: "skills-local/demo/SKILL.md", change: "modified" }] },
        spec: { version: "v2", content_type: "text", content: "# Spec" },
        pack: { status: "available", artifact: {
          ref: "oss://clawevolve-artifacts/evolution/EV-VERSION-REVIEW/rounds/round-002/artifacts/artifact_v2.zip",
          size: 14, sha256: "d".repeat(64), contentType: "application/zip",
        } },
      },
    });
    const unregisteredArtifact = {
      ref: "oss://clawevolve-artifacts/evolution/EV-VERSION-REVIEW/rounds/round-003/artifact.zip",
      size: 13, sha256: "c".repeat(64), contentType: "application/zip",
    };
    await repo.updateStepStatus("STEP-VERSION-R3", {
      status: "succeeded", output: {
        benchDecision: "passed", accepted: false, promotionStatus: "failed", reviewStatus: "success",
        scoreComparison: { name: "test_score", baseline: 0.7, candidate: 0.8, delta: 0.1 },
        pack: { status: "available", artifact: unregisteredArtifact },
      },
    });
    await repo.registerPack({
      pack_id: "PACK-VERSION-INITIAL", user_id: "owner-1", bot_id: "bot-1",
      source_task_id: "EV-VERSION-REVIEW", source_step_id: "STEP-VERSION-R1",
      source_kind: "baseline", source_round: 0,
      artifact_ref: baselineArtifact.ref, artifact_size: baselineArtifact.size,
      artifact_sha256: baselineArtifact.sha256, artifact_content_type: baselineArtifact.contentType,
    });
    await repo.registerPack({
      pack_id: "PACK-VERSION-R1", user_id: "owner-1", bot_id: "bot-1",
      source_task_id: "EV-VERSION-REVIEW", source_step_id: "STEP-VERSION-R1",
      source_kind: "round", source_round: 1,
      artifact_ref: roundArtifact.ref, artifact_size: roundArtifact.size,
      artifact_sha256: roundArtifact.sha256, artifact_content_type: roundArtifact.contentType,
    });
    await repo.registerPack({
      pack_id: "PACK-VERSION-R2", user_id: "owner-1", bot_id: "bot-1",
      source_task_id: "EV-VERSION-REVIEW", source_step_id: "STEP-VERSION-R2",
      source_kind: "round", source_round: 2,
      artifact_ref: "oss://clawevolve-artifacts/evolution/EV-VERSION-REVIEW/rounds/round-002/artifacts/artifact_v2.zip",
      artifact_size: 14, artifact_sha256: "d".repeat(64), artifact_content_type: "application/zip",
    });

    const response = await fetch(`${baseUrl}/api/evolve/versions?botId=bot-1`, {
      headers: { "X-User-Id": "owner-1" },
    });
    expect(response.status).toBe(200);
    const body = await response.json() as { items: Array<Record<string, unknown>> };
    expect(body.items).toHaveLength(4);
    expect(body.items).toEqual(expect.arrayContaining([
      expect.objectContaining({ kind: "initial", acceptanceStatus: "unassessed" }),
      expect.objectContaining({ kind: "round", round: 1, acceptanceStatus: "accepted", pack: expect.objectContaining({ packId: "PACK-VERSION-R1" }) }),
      expect.objectContaining({
        kind: "round", round: 2, acceptanceStatus: "rejected", accepted: false,
        pack: expect.objectContaining({ packId: "PACK-VERSION-R2" }),
        diff: expect.objectContaining({ available: true, artifactAvailable: false }),
      }),
      expect.objectContaining({
        kind: "round", round: 3, acceptanceStatus: "promotion_failed", accepted: false,
        promotionStatus: "failed", pack: null,
        reportedPack: expect.objectContaining({ status: "available", artifact: expect.objectContaining({ ref: unregisteredArtifact.ref }) }),
      }),
    ]));

    const packResponse = await fetch(`${baseUrl}/api/evolve/packs?botId=bot-1`, {
      headers: { "X-User-Id": "owner-1" },
    });
    const packs = await packResponse.json() as { items: Array<Record<string, unknown>> };
    expect(packs.items).toHaveLength(3);
  });

  it("registers the initial Optimize Pack while the round remains running", async () => {
    await repo.createTask({
      taskId: "EV-INITIAL-PACK", taskType: "optimize", userId: "owner-1", botId: "bot-1",
      taskName: "Initial Pack", remark: null, configJson: "{}", createdBy: "owner-1",
    });
    await repo.createStep({
      stepId: "STEP-INITIAL-PACK", taskId: "EV-INITIAL-PACK", stepType: "optimize", stepNo: 1,
      roundNo: 1, command: "/clawevolve-workflow --stage optimize",
    });
    const artifact = {
      kind: "baseline_pack",
      ref: "oss://clawevolve-artifacts/evolution/EV-INITIAL-PACK/baseline/artifact_v0.zip",
      size: 12,
      sha256: "c".repeat(64),
      contentType: "application/zip",
    };

    const report = await callback("EV-INITIAL-PACK", "STEP-INITIAL-PACK", {
      status: "running",
      summary: "任务初始 Pack 已创建并登记，准备开始优化",
      output: { baselineArtifact: { status: "available", artifact } },
    });
    expect(report.response.status).toBe(200);
    expect((await repo.findStep("STEP-INITIAL-PACK"))?.status).toBe("running");

    const listResponse = await fetch(`${baseUrl}/api/evolve/packs?botId=bot-1`, {
      headers: { "X-User-Id": "owner-1" },
    });
    expect(listResponse.status).toBe(200);
    const list = await listResponse.json() as { items: Array<Record<string, unknown>> };
    expect(list.items).toEqual([
      expect.objectContaining({
        taskId: "EV-INITIAL-PACK",
        stepId: "STEP-INITIAL-PACK",
        sourceKind: "baseline",
        artifact: expect.objectContaining({ ref: artifact.ref }),
      }),
    ]);

    const detailResponse = await fetch(`${baseUrl}/api/evolve/tasks/EV-INITIAL-PACK`, {
      headers: { "X-User-Id": "owner-1" },
    });
    expect(detailResponse.status).toBe(200);
    const detail = await detailResponse.json() as { initialPack?: Record<string, unknown> };
    expect(detail.initialPack).toEqual(expect.objectContaining({
      taskId: "EV-INITIAL-PACK",
      stepId: "STEP-INITIAL-PACK",
      sourceKind: "baseline",
      status: "available",
      artifact: expect.objectContaining({ ref: artifact.ref }),
    }));

    await repo.createStep({
      stepId: "STEP-INITIAL-PACK-RETRY", taskId: "EV-INITIAL-PACK", stepType: "optimize", stepNo: 2,
      roundNo: 1, command: "/clawevolve-workflow --stage optimize",
    });
    const roundArtifact = {
      kind: "pack",
      ref: "oss://clawevolve-artifacts/evolution/EV-INITIAL-PACK/rounds/round-001/artifacts/artifact_v1.zip",
      size: 13,
      sha256: "d".repeat(64),
      contentType: "application/zip",
    };
    const retryReport = await callback("EV-INITIAL-PACK", "STEP-INITIAL-PACK-RETRY", {
      status: "succeeded",
      output: {
        roundDecision: { stop: true, reason: "test complete" },
        pack: { status: "available", artifact: roundArtifact },
        baselineArtifact: { status: "available", artifact: { ...artifact, ref: artifact.ref.replace("artifact_v0", "artifact_changed") } },
      },
    });
    expect(retryReport.response.status).toBe(200);
    expect(await repo.listPacks("owner-1", "bot-1")).toEqual(expect.arrayContaining([
      expect.objectContaining({ source_kind: "baseline", artifact_ref: artifact.ref }),
      expect.objectContaining({ source_kind: "round", source_round: 1, artifact_ref: roundArtifact.ref }),
    ]));
  });

  it("does not backfill a missing Initial Pack from a later Optimize round", async () => {
    await repo.createTask({
      taskId: "EV-MISSING-INITIAL", taskType: "full", userId: "owner-1", botId: "bot-1",
      taskName: "Missing Initial Pack", remark: null, configJson: "{}", createdBy: "owner-1",
    });
    await repo.createStep({
      stepId: "STEP-MISSING-INITIAL-R2", taskId: "EV-MISSING-INITIAL", stepType: "optimize", stepNo: 2,
      roundNo: 2, command: "/clawevolve-workflow --stage optimize",
    });
    const report = await callback("EV-MISSING-INITIAL", "STEP-MISSING-INITIAL-R2", {
      status: "succeeded",
      output: {
        roundDecision: { stop: true, reason: "test complete" },
        baselineArtifact: { status: "available", artifact: {
          kind: "pack",
          ref: "oss://clawevolve-artifacts/evolution/EV-MISSING-INITIAL/rounds/round-001/artifacts/artifact_v1.zip",
          size: 13, sha256: "e".repeat(64), contentType: "application/zip",
        } },
      },
    });
    expect(report.response.status).toBe(200);
    expect(await repo.listPacks("owner-1", "bot-1")).toHaveLength(0);
  });

  it("creates an isolated runtime cleanup on the existing BaaS runner path", async () => {
    vi.spyOn(repo, "resolveEvolveBotRuntime").mockResolvedValue({
      activeEngine: "openclaw", botType: "personal", hasServiceBot: false,
      botStatus: "active", bindingId: 1, provider: "baas", deviceId: "DEVICE-pre-1",
      bindingStatus: "active", env: "pre",
    });
    const response = await fetch(`${baseUrl}/api/evolve/runtime-cleanups`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({
        taskName: "清理历史进化记录", userId: "owner-1", botId: "bot-1", botEnv: "pre",
      }),
    });
    expect(response.status).toBe(201);
    const task = await response.json() as {
      task_id: string;
      task_type: string;
      config: Record<string, unknown>;
      steps: Array<{ stepId: string; stepType: string; command: string }>;
    };
    expect(task.task_type).toBe("runtime_cleanup");
    expect(task.config).toEqual(expect.objectContaining({ scope: "bot_history", forceCleanup: false, runtimeMaintenance: false }));
    expect(task.steps[0]).toEqual(expect.objectContaining({
      stepType: "runtime_cleanup", command: expect.stringContaining("/clawevolve-runtime-cleanup --task-id "),
    }));
    expect(dispatch).toHaveBeenLastCalledWith(expect.objectContaining({
      stepType: "runtime_cleanup", runtimeMaintenance: false,
    }));

    const report = await callback(task.task_id, task.steps[0].stepId, {
      status: "succeeded",
      summary: "进化运行环境清理完成",
      output: { status: "ok", deletedAgentCount: 1, deletedSessionCount: 2 },
    });
    expect(report.response.status).toBe(200);
    expect((await repo.findTask(task.task_id))?.status).toBe("completed");
  });

  it("blocks cleanup for an active Bot task and allows explicit force without stopping it", async () => {
    vi.spyOn(repo, "resolveEvolveBotRuntime").mockResolvedValue({
      activeEngine: "openclaw", botType: "personal", hasServiceBot: false,
      botStatus: "active", bindingId: 1, provider: "baas", deviceId: "DEVICE-pre-1",
      bindingStatus: "active", env: "pre",
    });
    await repo.createTask({
      taskId: "EV-ACTIVE", taskType: "full", userId: "owner-1", botId: "bot-1",
      taskName: "仍在运行", configJson: JSON.stringify({ botEnv: "pre" }), createdBy: "owner-1",
    });

    const create = (forceCleanup: boolean) => fetch(`${baseUrl}/api/evolve/runtime-cleanups`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({
        taskName: "清理历史进化记录", userId: "owner-1", botId: "bot-1", botEnv: "pre", forceCleanup,
      }),
    });
    const blocked = await create(false);
    expect(blocked.status).toBe(409);
    expect(await blocked.json()).toEqual(expect.objectContaining({
      code: "EVOLVE_TASKS_STILL_RUNNING",
      activeTasks: [expect.objectContaining({ taskId: "EV-ACTIVE" })],
    }));
    expect(dispatch).not.toHaveBeenCalled();

    const forced = await create(true);
    expect(forced.status).toBe(201);
    const forcedTask = await forced.json() as { config: Record<string, unknown>; steps: Array<{ command: string }> };
    expect(forcedTask.config.forceCleanup).toBe(true);
    expect(forcedTask.steps[0].command).toContain("--force-cleanup");
    expect((await repo.findTask("EV-ACTIVE"))?.status).toBe("pending");
  });

  it("registers reported Packs and exposes their source and Restore applications", async () => {
    await repo.createTask({
      taskId: "EV-PACK-REGISTRY", taskType: "pack", userId: "owner-1", botId: "bot-1",
      taskName: "Pack registry", remark: null, configJson: "{}", createdBy: "owner-1",
    });
    await repo.createStep({
      stepId: "STEP-PACK-REGISTRY", taskId: "EV-PACK-REGISTRY", stepType: "pack", stepNo: 1,
      command: "/clawevolve-pack --mode pack",
    });
    const artifact = {
      kind: "pack", ref: "oss://clawevolve-artifacts/evolution/EV-PACK-REGISTRY/snapshots/artifact.zip",
      size: 42, sha256: "b".repeat(64), contentType: "application/zip",
    };
    const report = await callback("EV-PACK-REGISTRY", "STEP-PACK-REGISTRY", {
      status: "succeeded", output: { pack: { status: "available", artifact } },
    });
    expect(report.response.status).toBe(200);

    const listResponse = await fetch(`${baseUrl}/api/evolve/packs`, { headers: { "X-User-Id": "owner-1" } });
    expect(listResponse.status).toBe(200);
    const list = await listResponse.json() as { items: Array<{ packId: string; applicationCount: number }> };
    expect(list.items).toHaveLength(1);
    expect(list.items[0]).toEqual(expect.objectContaining({ applicationCount: 0 }));

    const restoreResponse = await fetch(`${baseUrl}/api/evolve/pack-restores`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({
        taskName: "Apply Pack", userId: "owner-1", botId: "bot-1",
        packId: list.items[0].packId,
      }),
    });
    const restoreError = restoreResponse.status === 201 ? null : await restoreResponse.clone().json();
    expect(restoreResponse.status, JSON.stringify(restoreError)).toBe(201);
    const restore = await restoreResponse.json() as { task_id: string };

    const detailResponse = await fetch(`${baseUrl}/api/evolve/packs/${list.items[0].packId}`, {
      headers: { "X-User-Id": "owner-1" },
    });
    expect(detailResponse.status).toBe(200);
    const detail = await detailResponse.json() as {
      pack: { taskId: string }; sourceTask: { task_id: string };
      applications: Array<{ task_id: string; config: { packId: string } }>;
    };
    expect(detail.pack.taskId).toBe("EV-PACK-REGISTRY");
    expect(detail.sourceTask.task_id).toBe("EV-PACK-REGISTRY");
    expect(detail.applications).toEqual([
      expect.objectContaining({ task_id: restore.task_id, config: expect.objectContaining({ packId: list.items[0].packId }) }),
    ]);
    expect(await repo.countPackApplications(await repo.listPacks("owner-1"))).toEqual({ [list.items[0].packId]: 1 });
  });

  it("issues a download URL only for a registered Pack", async () => {
    await repo.createTask({
      taskId: "EV-PACK-DOWNLOAD", taskType: "optimize", userId: "owner-1", botId: "bot-1",
      taskName: "Pack download", remark: null, configJson: "{}", createdBy: "owner-1",
    });
    await repo.createStep({
      stepId: "STEP-PACK-DOWNLOAD", taskId: "EV-PACK-DOWNLOAD", stepType: "optimize", stepNo: 1,
      roundNo: 1, command: "/clawevolve-workflow --stage optimize",
    });
    const artifact = {
      kind: "pack", ref: "oss://clawevolve-artifacts/evolution/EV-PACK-DOWNLOAD/rounds/round-001/artifacts/artifact_v1.zip",
      size: 12, sha256: "a".repeat(64), contentType: "application/zip",
    };
    await repo.updateStepStatus("STEP-PACK-DOWNLOAD", {
      status: "succeeded", output: { pack: { status: "available", artifact } },
    });
    await repo.registerPack({
      pack_id: "PACK-DOWNLOAD", user_id: "owner-1", bot_id: "bot-1",
      source_task_id: "EV-PACK-DOWNLOAD", source_step_id: "STEP-PACK-DOWNLOAD",
      source_kind: "round", source_round: 1,
      artifact_ref: artifact.ref, artifact_size: artifact.size,
      artifact_sha256: artifact.sha256, artifact_content_type: artifact.contentType,
    });
    const response = await fetch(`${baseUrl}/api/evolve/tasks/EV-PACK-DOWNLOAD/steps/STEP-PACK-DOWNLOAD/pack-download-url?sourceKind=round`);
    const downloadError = response.status === 200 ? null : await response.clone().json();
    expect(response.status, JSON.stringify(downloadError)).toBe(200);
    await expect(response.json()).resolves.toEqual(expect.objectContaining({
      url: "https://oss.example.test/signed", filename: "EV-PACK-DOWNLOAD-STEP-PACK-DOWNLOAD-round-1.zip", artifact,
    }));
    expect(createSignedUrl).toHaveBeenLastCalledWith(
      "evolution/EV-PACK-DOWNLOAD/rounds/round-001/artifacts/artifact_v1.zip", "GET", 86_400, {},
      { "response-content-disposition": 'attachment; filename="EV-PACK-DOWNLOAD-STEP-PACK-DOWNLOAD-round-1.zip"' },
    );
  });

  it("allows an Optimize Diff to follow the current globally visible task policy", async () => {
    await repo.createTask({
      taskId: "EV-DIFF", taskType: "optimize", userId: "owner-1", botId: "bot-1",
      taskName: "Diff", remark: null, configJson: "{}", createdBy: "owner-1",
    });
    await repo.createStep({
      stepId: "STEP-DIFF", taskId: "EV-DIFF", stepType: "optimize", stepNo: 1,
      roundNo: 1, command: "/clawevolve-workflow --stage optimize",
    });
    const response = await fetch(`${baseUrl}/api/evolve/tasks/EV-DIFF/steps/STEP-DIFF/diff`);
    expect(response.status).toBe(422);
    await expect(response.json()).resolves.not.toEqual({ error: "Optimize Step 不存在" });
  });

  it("reads an Optimize Diff from the injected artifact store", async () => {
    const content = Buffer.from("diff --git a/a.txt b/a.txt\n+new line\n");
    const sha256 = (await import("node:crypto")).createHash("sha256").update(content).digest("hex");
    await repo.createTask({
      taskId: "EV-DIFF-OBJECT", taskType: "optimize", userId: "owner-1", botId: "bot-1",
      taskName: "Diff object", remark: null, configJson: "{}", createdBy: "owner-1",
    });
    await repo.createStep({
      stepId: "STEP-DIFF-OBJECT", taskId: "EV-DIFF-OBJECT", stepType: "optimize", stepNo: 1,
      roundNo: 1, command: "/clawevolve-workflow --stage optimize",
    });
    await repo.updateStepStatus("STEP-DIFF-OBJECT", { status: "succeeded", output: {
      diff: { artifact: {
        kind: "diff",
        ref: "oss://clawevolve-artifacts/evolution/EV-DIFF-OBJECT/rounds/round-001/diff.patch",
        size: content.byteLength,
        sha256,
        contentType: "text/x-diff; charset=utf-8",
      } },
    } });
    getObject.mockResolvedValue({ content, etag: null, contentType: "text/x-diff; charset=utf-8" });

    const response = await fetch(`${baseUrl}/api/evolve/tasks/EV-DIFF-OBJECT/steps/STEP-DIFF-OBJECT/diff`);

    expect(response.status).toBe(200);
    await expect(response.text()).resolves.toBe(content.toString("utf8"));
    expect(getObject).toHaveBeenCalledWith("evolution/EV-DIFF-OBJECT/rounds/round-001/diff.patch");
  });
});

describe("ClawEvolve task log archive side channel", () => {
  it("returns an actionable error when task log storage is unavailable", async () => {
    await seedArcaBot("owner-1", "bot-arca");
    await repo.createTask({
      taskId: "EV-LOG-STORAGE", taskType: "full", userId: "owner-1", botId: "bot-arca",
      taskName: "日志存储异常", configJson: JSON.stringify({ botEnv: "pre" }), createdBy: "owner-1",
    });
    vi.spyOn(repo, "findActiveTaskLogArchive").mockRejectedValueOnce(new Error("table missing"));

    const response = await fetch(`${baseUrl}/api/evolve/tasks/EV-LOG-STORAGE/log-archives`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" }, body: "{}",
    });

    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toEqual({
      code: "TASK_LOG_STORAGE_UNAVAILABLE",
      error: "日志归档存储不可用，请确认 ClawWeb 数据库已完成日志归档表升级",
    });
    expect(dispatchTaskLogArchive).not.toHaveBeenCalled();
  });

  it("reuses an active request and keeps Task and Step state untouched", async () => {
    await seedArcaBot("owner-1", "bot-arca");
    await repo.createTask({
      taskId: "EV-LOG-001", taskType: "full", userId: "owner-1", botId: "bot-arca",
      taskName: "日志旁路", configJson: JSON.stringify({ botEnv: "pre" }), createdBy: "owner-1",
    });

    const first = await fetch(`${baseUrl}/api/evolve/tasks/EV-LOG-001/log-archives`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" }, body: "{}",
    });
    const firstBody = await first.json() as { archive: { archiveId: string; status: string }; reused: boolean };
    const second = await fetch(`${baseUrl}/api/evolve/tasks/EV-LOG-001/log-archives`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" }, body: "{}",
    });
    const secondBody = await second.json() as typeof firstBody;

    expect(first.status).toBe(202);
    expect(firstBody.reused).toBe(false);
    expect(secondBody.reused).toBe(true);
    expect(secondBody.archive.archiveId).toBe(firstBody.archive.archiveId);
    expect(dispatchTaskLogArchive).toHaveBeenCalledTimes(1);
    expect((await repo.findTask("EV-LOG-001"))?.status).toBe("pending");
    expect(await repo.listSteps("EV-LOG-001")).toHaveLength(0);
  });

  it("registers a completed artifact and exposes the newest archive from the side-channel API", async () => {
    await seedArcaBot("owner-1", "bot-arca");
    await repo.createTask({
      taskId: "EV-LOG-002", taskType: "full", userId: "owner-1", botId: "bot-arca",
      taskName: "日志归档", configJson: JSON.stringify({ botEnv: "pre" }), createdBy: "owner-1",
    });
    const created = await fetch(`${baseUrl}/api/evolve/tasks/EV-LOG-002/log-archives`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" }, body: "{}",
    });
    const { archive } = await created.json() as { archive: { archiveId: string } };
    const ref = `oss://clawevolve-artifacts/evolution/EV-LOG-002/support/log-archives/${archive.archiveId}.tar.gz`;
    const report = await fetch(`${baseUrl}/api/evolve/internal/tasks/EV-LOG-002/log-archives/${archive.archiveId}/report`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "succeeded", artifact: {
        ref, size: 128, sha256: "a".repeat(64), contentType: "application/gzip",
      }, metadata: { entryCount: 3 } }),
    });
    expect(report.status).toBe(200);

    const detail = await fetch(`${baseUrl}/api/evolve/tasks/EV-LOG-002/log-archives`, { headers: { "X-User-Id": "owner-1" } });
    const body = await detail.json() as { items: Array<{ archiveId: string; status: string }> };
    expect(body.items[0]).toMatchObject({ archiveId: archive.archiveId, status: "succeeded" });
    const download = await fetch(`${baseUrl}/api/evolve/tasks/EV-LOG-002/log-archives/${archive.archiveId}/download-url`, {
      headers: { "X-User-Id": "owner-1" },
    });
    expect(download.status).toBe(200);
    expect(createSignedUrl).toHaveBeenLastCalledWith(
      `evolution/EV-LOG-002/support/log-archives/${archive.archiveId}.tar.gz`, "GET", 86400, {},
      expect.objectContaining({ "response-content-disposition": expect.stringContaining("EV-LOG-002") }),
    );

    const repeated = await fetch(`${baseUrl}/api/evolve/tasks/EV-LOG-002/log-archives`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" }, body: "{}",
    });
    const repeatedBody = await repeated.json() as { archive: { archiveId: string }; reused: boolean };
    expect(repeatedBody.reused).toBe(false);
    expect(repeatedBody.archive.archiveId).not.toBe(archive.archiveId);

    const history = await fetch(`${baseUrl}/api/evolve/tasks/EV-LOG-002/log-archives`, {
      headers: { "X-User-Id": "owner-1" },
    });
    const historyBody = await history.json() as { items: Array<{ archiveId: string; status: string }> };
    expect(historyBody.items.map((item) => item.archiveId)).toEqual([
      repeatedBody.archive.archiveId,
      archive.archiveId,
    ]);
    expect(historyBody.items[1]?.status).toBe("succeeded");
    expect(dispatchTaskLogArchive).toHaveBeenCalledTimes(2);
  });
});
