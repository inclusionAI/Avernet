import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import { SqliteDatabase, runMigrations } from "../../db.js";
import { EvolveRepository } from "../../repositories/evolve-repository.js";
import { EvolveTaskSourceRepository } from "../../repositories/evolve-task-source-repository.js";
import { BenchTemplateRepository } from "../../repositories/bench-template-repository.js";
import { InsightImprovementRepository } from "../../repositories/insight-improvement-repository.js";
import { InsightAutoRepairRepository } from "../../repositories/insight-auto-repair-repository.js";
import type { SessionEvidence } from "../../services/insight/contracts.js";
import { GovernanceRuleProvider } from "../../services/insight/governance-rule-provider.js";
import { TaskSourceService } from "../../services/evolve/task-source-service.js";
import { InsightPlanStepService } from "../../services/evolve/insight-plan-step-service.js";
import { InsightTaskService } from "../../services/evolve/insight-task-service.js";
import { createEvolveRouter } from "../evolve.js";
import { join } from "node:path";

let db: SqliteDatabase;
let repo: EvolveRepository;
let improvementRepo: InsightImprovementRepository;
let autoRepairRepo: InsightAutoRepairRepository;
let ruleProvider: GovernanceRuleProvider;
let taskSourceRepo: EvolveTaskSourceRepository;
let taskSourceService: TaskSourceService;
let benchTemplateRepo: BenchTemplateRepository;
let server: ReturnType<express.Application["listen"]> | null;
let baseUrl: string;
const dispatch = vi.fn();

const evidence: SessionEvidence = {
  schema_version: "session-evidence/v1",
  batch_id: "batch-1",
  dt: "20260811",
  user_id: "owner-1",
  bot_id: "bot-1",
  session_id: "session-1",
  session: { start_time: "2026-08-11T01:00:00Z" },
  judge_meta: { judge_version: "v3" },
  generated_at: "2026-08-11T02:00:00Z",
  messages: [
    { message_index: 0, role: "user", timestamp: 1, visibility: "visible", content: "查日志", raw: {} },
    { message_index: 1, role: "assistant", timestamp: 2, visibility: "visible", content: "工具失败", raw: { tool_call: {} } },
  ],
  tasks: [{
    task_index: 0,
    task_description: "验证工具执行环境",
    message_range: [0, 2],
    is_complete: 0,
    reasoning: "工具失败后结束",
    task_failure_class: "TOOL_FAILURE",
  }],
};

beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  await db.exec(
    `CREATE TABLE ac_entity_device_binding (
      id INTEGER PRIMARY KEY,
      device_provider TEXT,
      device_id TEXT,
      status TEXT,
      env TEXT
    )`,
  );
  await db.exec(
    `CREATE TABLE ac_bots (
      id INTEGER PRIMARY KEY,
      bot_id TEXT NOT NULL,
      owner_id TEXT,
      entity_id TEXT,
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
      (id, bot_id, owner_id, entity_id, is_delete, active_engine, bot_type, status, binding_id, env)
     VALUES (?, ?, ?, ?, 0, 'openclaw', 'personal', 'active', NULL, 'pre')`,
    [1, "target-bot-1", "specialist-1", "specialist-1"],
  );
  await db.exec(
    `INSERT INTO ac_bots
      (id, bot_id, owner_id, entity_id, is_delete, active_engine, bot_type, status, binding_id, env)
     VALUES (?, ?, ?, ?, 0, 'openclaw', 'personal', 'active', NULL, 'pre')`,
    [99, "bot-1", "owner-1", "owner-1"],
  );
  repo = new EvolveRepository(db);
  improvementRepo = new InsightImprovementRepository(db);
  autoRepairRepo = new InsightAutoRepairRepository(db);
  ruleProvider = new GovernanceRuleProvider({
    environment: "pre",
    filePath: join(process.cwd(), "server/fixtures/insight/v1/governance-rules.json"),
  });
  taskSourceRepo = new EvolveTaskSourceRepository(db);
  benchTemplateRepo = new BenchTemplateRepository(db);
  taskSourceService = new TaskSourceService(taskSourceRepo, async () => evidence);
  dispatch.mockReset();
  dispatch.mockImplementation(async (input: { command: string }) => ({
    runId: "run-1",
    sessionId: "session-1",
    platformResponse: { echoedCommand: input.command },
  }));
  const app = express();
  app.use(express.json());
  app.use("/api/evolve", createEvolveRouter(repo, {
    dispatch,
    improvementRepo,
    taskSourceService,
    autoRepairRepo,
    ruleProvider,
    benchTemplateRepo,
    artifactUrlStore: { createSignedUrl: vi.fn(async () => "https://signed.example/object") },
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

async function seedImprovement(): Promise<number> {
  const result = await improvementRepo.create({
    ownerUserId: "specialist-1",
    botOwnerUserId: "owner-1",
    botId: "bot-1",
    title: "修复工具失败后不降级",
    userGuidance: "保留可执行降级路径",
    sourceType: "USER_SELECTED",
    sourceRuleId: null,
    dataStartTime: "2026-08-10T00:00:00Z",
    dataEndTime: "2026-08-11T00:00:00Z",
    dataAsOf: "2026-08-11T02:00:00Z",
    batchId: "batch-1",
    contentFingerprint: "fingerprint-1",
    idempotencyKey: "create-improvement-1",
    createdBy: "specialist-1",
    evidence: [{
      sessionId: "session-1",
      taskIndex: 0,
      ordinal: 0,
      taskDescription: "验证工具执行环境",
      failureClass: "TOOL_FAILURE",
      reasoningSummary: "工具失败后结束",
      payloadRef: "oss://bucket/session-1.json",
      payloadEtag: "etag-1",
      payloadVersionId: "v1",
    }],
  });
  return result.item.id;
}

async function createInsightTask(improvementId: number, overrides: {
  actorUserId?: string;
  userId?: string;
  botId?: string;
  botEnv?: string;
  crossBotConfirmed?: boolean;
  idempotencyKey?: string;
  nodeCommandYamls?: Record<string, string>;
  persistAutoRepairGrant?: boolean;
} = {}) {
  const response = await fetch(`${baseUrl}/api/evolve/tasks`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-User-Id": overrides.actorUserId ?? "specialist-1",
      "Idempotency-Key": overrides.idempotencyKey ?? "insight-task-1",
    },
    body: JSON.stringify({
      taskType: "full",
      taskName: "Insight 全流程",
      userId: overrides.userId ?? "specialist-1",
      botId: overrides.botId ?? "target-bot-1",
      botEnv: overrides.botEnv,
      input: {
        type: "insight_improvement",
        improvementId,
        crossBotConfirmed: overrides.crossBotConfirmed ?? true,
        persistAutoRepairGrant: overrides.persistAutoRepairGrant,
      },
      maxRounds: 3,
      nodeCommandYamls: overrides.nodeCommandYamls,
    }),
  });
  return { response, body: await response.json() as Record<string, unknown> };
}

async function seedDirectGovernanceImprovement(): Promise<number> {
  const result = await improvementRepo.create({
    ownerUserId: "specialist-1",
    botOwnerUserId: "owner-1",
    botId: "bot-1",
    title: "修复确定性工具选择问题",
    userGuidance: "仅更新 tools.md",
    sourceType: "ADMIN_RULE_DIRECT_EVOLUTION",
    sourceRuleId: "tool.utoo-proxy.unsupported",
    dataStartTime: "2026-08-10T00:00:00Z",
    dataEndTime: "2026-08-11T00:00:00Z",
    dataAsOf: "2026-08-11T02:00:00Z",
    batchId: "batch-governance",
    contentFingerprint: "fingerprint-governance",
    idempotencyKey: "create-governance-improvement",
    createdBy: "governance-agent",
    initialStatus: "ACTIVE",
    evidence: [{
      sessionId: "session-1",
      taskIndex: 0,
      ordinal: 0,
      taskDescription: "验证工具执行环境",
      failureClass: "TOOL_FAILURE",
      reasoningSummary: "工具失败后结束",
      payloadRef: "oss://bucket/session-1.json",
      payloadEtag: "etag-1",
      payloadVersionId: "v1",
    }],
  });
  return result.item.id;
}

describe("Evolve Insight Improvement input", () => {
  it("allows an admin to execute one improvement once without creating owner authorization", async () => {
    const improvementId = await seedDirectGovernanceImprovement();
    const taskService = new InsightTaskService(
      repo,
      improvementRepo,
      taskSourceService,
      new InsightPlanStepService(repo, dispatch),
      autoRepairRepo,
      ruleProvider,
    );

    const result = await taskService.create({
      taskType: "full",
      taskName: "管理员代处理一次",
      remark: "仅修改 tools.md",
      userId: "specialist-1",
      botId: "target-bot-1",
      improvementId,
      crossBotConfirmed: true,
      maxRounds: 3,
      nodeCommandYamls: undefined,
      forceMessage: false,
      runtimeMaintenance: true,
      idempotencyKey: "admin-once-task-1",
      actorUserId: "admin-1",
      createdByOverride: "insight-admin-override",
      adminOverrideOnce: {
        operatorUserId: "admin-1",
        reason: "用户长期未处理，问题持续影响任务完成率",
        repairDirection: "仅检查并修改 tools.md",
      },
      callbackUrl: (taskId, stepId) => `http://localhost:5173/api/evolve/internal/tasks/${taskId}/steps/${stepId}/bot-callback`,
    });

    expect(result.created).toBe(true);
    expect(result.task.created_by).toBe("insight-admin-override");
    expect(JSON.parse(result.task.config_json)).toEqual(expect.objectContaining({
      adminOverride: expect.objectContaining({
        mode: "ADMIN_ONCE",
        operatorUserId: "admin-1",
        targetUserId: "specialist-1",
        persistentAuthorization: false,
      }),
      autoRepairAuthorization: expect.objectContaining({ consentMode: "ADMIN_ONCE" }),
    }));
    expect(await autoRepairRepo.list("specialist-1")).toEqual([]);

    const sourceRow = (await db.query<{ source_ref_json: string }>(
      "SELECT source_ref_json FROM ce_task_sources WHERE task_id = ?",
      [result.task.task_id],
    ))[0];
    const sourceRef = JSON.parse(sourceRow.source_ref_json) as Record<string, unknown>;
    expect(sourceRef.adminOverride).toEqual(expect.objectContaining({
      mode: "ADMIN_ONCE",
      operatorUserId: "admin-1",
      reason: "用户长期未处理，问题持续影响任务完成率",
      repairDirection: "仅检查并修改 tools.md",
    }));
    expect((await improvementRepo.getDetail("specialist-1", improvementId))?.status).toBe("IN_PROGRESS");
  });

  it("dispatches an Insight Plan directly through the selected ARCA environment", async () => {
    await db.exec(
      `INSERT INTO ac_entity_device_binding (id, device_provider, device_id, status, env)
       VALUES (7, 'arca', 'ARCA-PRE-7', 'active', 'pre')`,
    );
    await db.exec("UPDATE ac_bots SET binding_id = 7 WHERE bot_id = 'target-bot-1' AND env = 'pre'");
    const improvementId = await seedImprovement();
    const result = await createInsightTask(improvementId, { botEnv: "pre" });

    expect(result.response.status).toBe(201);
    const steps = result.body.steps as Array<{ stepId: string; stepType: string; status: string; command: string }>;
    expect(steps.map((step) => step.stepType)).toEqual(["plan"]);
    expect(steps[0].command).not.toContain("api-key");
    expect(steps[0].status).toBe("dispatched");
    expect(dispatch).toHaveBeenCalledTimes(1);
    expect(dispatch).toHaveBeenLastCalledWith(expect.objectContaining({
      stepType: "plan",
      runtime: expect.objectContaining({ provider: "arca", env: "pre" }),
    }));
  });

  it("keeps the original Diagnose to Plan path outside Task Source", async () => {
    const sourceLookup = vi.spyOn(taskSourceRepo, "findByTaskId");
    const createResponse = await fetch(`${baseUrl}/api/evolve/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({
        taskType: "full",
        taskName: "原 Diagnose 全流程",
        userId: "owner-1",
        botId: "bot-1",
        apiKey: "temporary-secret",
        model: "GLM-5.1",
        goal: "根据诊断结果提升工具执行成功率",
        diagnoseIntent: "扫描最近3天，抽取1个 bad case，关注工具执行失败。",
      }),
    });
    expect(createResponse.status).toBe(201);
    const task = await createResponse.json() as {
      task_id: string;
      steps: Array<{ stepId: string; stepType: string }>;
    };
    expect(task.steps).toHaveLength(1);
    expect(task.steps[0]?.stepType).toBe("diagnose");

    const reportResponse = await fetch(
      `${baseUrl}/api/evolve/internal/tasks/${task.task_id}/steps/${task.steps[0]?.stepId}/report`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          status: "succeeded",
          output: {
            diagnosis: { summary: "原链路问题" },
            cases: {
              total: 1,
              goodCount: 0,
              badCount: 1,
              items: [{ caseId: "diagnose-case-1", type: "bad", summary: "case 1" }],
            },
          },
        }),
      },
    );
    expect(reportResponse.status).toBe(200);
    const report = await reportResponse.json() as { nextStep: { stepId: string } };
    const planDispatch = dispatch.mock.calls.at(-1)?.[0] as {
      command?: string;
      runtime?: { env?: string };
    } | undefined;
    expect(planDispatch?.command).toContain(`/clawevolve-plan --task-id ${task.task_id} --step-id ${report.nextStep.stepId}`);
    expect(planDispatch?.command).toContain("--owner-id owner-1");
    expect(planDispatch?.command).toContain("--goal '根据诊断结果提升工具执行成功率'");
    expect(planDispatch?.command).toContain("--clawweb-url http://localhost:3001");
    expect(planDispatch?.command).not.toContain("--resolve-plan-source");
    expect(planDispatch?.command).not.toContain("--plan-source-token");
    expect(planDispatch?.runtime?.env).toBe("pre");

    const inputResponse = await fetch(
      `${baseUrl}/api/evolve/internal/tasks/${task.task_id}/steps/${report.nextStep.stepId}/input`,
    );
    expect(inputResponse.status).toBe(200);
    const input = await inputResponse.json() as {
      protocolVersion: string;
      inputs: { diagnose: { output: { diagnosis: { summary: string } } } };
    };
    expect(input.protocolVersion).toBe("1.0");
    expect(input.inputs.diagnose.output.diagnosis.summary).toBe("原链路问题");

    expect((await fetch(`${baseUrl}/api/evolve/tasks/${task.task_id}`)).status).toBe(200);
    expect(sourceLookup).not.toHaveBeenCalled();
    expect(await db.query("SELECT * FROM ce_task_sources")).toHaveLength(0);
  });

  it("creates an Insight task without source-specific credentials", async () => {
    const improvementId = await seedImprovement();
    const result = await createInsightTask(improvementId);
    expect(result.response.status).toBe(201);
    expect(result.body.steps).toEqual([
      expect.objectContaining({ stepType: "plan", command: expect.not.stringContaining("--resolve-plan-source") }),
    ]);
    expect(JSON.stringify(result.body)).not.toContain("plan-source-token");
    expect(await db.query("SELECT * FROM ce_task_sources")).toHaveLength(1);
    expect(dispatch).toHaveBeenCalledWith(expect.objectContaining({
      runtime: expect.objectContaining({ env: "pre" }),
    }));
    expect(dispatch).toHaveBeenCalledTimes(1);
  });

  it("creates a new governance task from any owner-visible Improvement status", async () => {
    const improvementId = await seedImprovement();
    const first = await createInsightTask(improvementId, {
      idempotencyKey: "insight-task-initial",
    });
    expect(first.response.status).toBe(201);

    const taskIds = new Set([String(first.body.task_id)]);
    for (const status of ["IN_PROGRESS", "RESOLVED", "ARCHIVED"] as const) {
      await db.exec(
        "UPDATE insight_improvement_item SET status = ? WHERE id = ?",
        [status, improvementId],
      );
      const created = await createInsightTask(improvementId, {
        idempotencyKey: `insight-task-from-${status.toLowerCase()}`,
      });
      expect(created.response.status).toBe(201);
      taskIds.add(String(created.body.task_id));
      expect((await improvementRepo.getDetail("specialist-1", improvementId))?.status).toBe("IN_PROGRESS");
    }

    expect(taskIds.size).toBe(4);
    expect(await db.query(
      "SELECT * FROM insight_improvement_evolve_link WHERE improvement_id = ?",
      [improvementId],
    )).toHaveLength(4);
    expect(await db.query("SELECT * FROM ce_tasks")).toHaveLength(4);
    expect(dispatch).toHaveBeenCalledTimes(4);
  });

  it("persists one scoped Owner authorization when starting a direct governance repair", async () => {
    const improvementId = await seedDirectGovernanceImprovement();
    const result = await createInsightTask(improvementId, { persistAutoRepairGrant: true });

    expect(result.response.status).toBe(201);
    const grants = await autoRepairRepo.list("specialist-1");
    expect(grants).toEqual([
      expect.objectContaining({
        ownerUserId: "specialist-1",
        botId: "target-bot-1",
        sourceRuleId: "tool.utoo-proxy.unsupported",
        ruleVersion: 1,
        allowedTargets: ["tools.md"],
        risk: "low",
        status: "ACTIVE",
        sourceImprovementId: improvementId,
      }),
    ]);
    expect(result.body.config).toEqual(expect.objectContaining({
      autoRepairAuthorization: expect.objectContaining({
        grantId: grants[0].grantId,
        mode: "OWNER_CONSENT",
        consentMode: "PERSISTENT",
      }),
    }));
  });

  it("records a one-time authorization in the existing task config without creating a grant", async () => {
    const improvementId = await seedDirectGovernanceImprovement();
    const result = await createInsightTask(improvementId, { persistAutoRepairGrant: false });

    expect(result.response.status).toBe(201);
    expect(await autoRepairRepo.list("specialist-1")).toEqual([]);
    expect(result.body.config).toEqual(expect.objectContaining({
      autoRepairAuthorization: { consentMode: "ONCE" },
    }));
  });

  it("does not allow direct governance auto-repair to target a service Bot", async () => {
    await db.exec(
      `INSERT INTO ac_bots
        (id, bot_id, owner_id, entity_id, is_delete, active_engine, bot_type, status, binding_id, env)
       VALUES (?, ?, ?, ?, 0, 'openclaw', 'service', 'active', NULL, 'pre')`,
      [3, "service-bot-1", "specialist-1", "specialist-1"],
    );
    const improvementId = await seedDirectGovernanceImprovement();
    const result = await createInsightTask(improvementId, {
      botId: "service-bot-1",
      crossBotConfirmed: true,
    });

    expect(result.response.status).toBe(403);
    expect(result.body).toEqual(expect.objectContaining({
      code: "AUTO_REPAIR_SERVICE_BOT_FORBIDDEN",
    }));
    expect(await db.query("SELECT * FROM ce_tasks")).toHaveLength(0);
  });

  it("passes the execution owner when an Insight Plan advances to Optimize", async () => {
    const improvementId = await seedImprovement();
    const created = await createInsightTask(improvementId);
    expect(created.response.status).toBe(201);
    const taskId = String(created.body.task_id);
    const planStep = (created.body.steps as Array<{ stepId: string }>)[0];

    expect((await fetch(
      `${baseUrl}/api/evolve/internal/tasks/${taskId}/steps/${planStep.stepId}/input`,
    )).status).toBe(200);
    const reportResponse = await fetch(
      `${baseUrl}/api/evolve/internal/tasks/${taskId}/steps/${planStep.stepId}/report`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          status: "succeeded",
          output: {
            goal: {},
            spec: { version: "v0", content_type: "text", content: "治理进化策略" },
            benchCases: {
              trainCount: 0,
              testCount: 0,
              items: [],
            },
            benchDomains: {
              trainBenchDomainId: "INSIGHT-TRAIN",
              testBenchDomainId: "INSIGHT-TEST",
            },
          },
        }),
      },
    );

    expect(reportResponse.status).toBe(200);
    expect(dispatch).toHaveBeenLastCalledWith(expect.objectContaining({
      stepType: "optimize",
      userId: "specialist-1",
      command: expect.stringContaining("--owner-id specialist-1"),
    }));
    const command = String(dispatch.mock.calls.at(-1)?.[0]?.command);
    expect(command).toContain("--train-bench-domain-id INSIGHT-TRAIN");
    expect(command).toContain("--test-bench-domain-id INSIGHT-TEST");
  });

  it("requires an explicit confirmation when Evidence and execution target differ", async () => {
    const improvementId = await seedImprovement();
    const result = await createInsightTask(improvementId, { crossBotConfirmed: false });

    expect(result.response.status).toBe(400);
    expect(result.body).toEqual(expect.objectContaining({ code: "CROSS_BOT_CONFIRMATION_REQUIRED" }));
    expect(await db.query("SELECT * FROM ce_tasks")).toHaveLength(0);
    expect(await db.query("SELECT * FROM ce_task_sources")).toHaveLength(0);
    expect(dispatch).not.toHaveBeenCalled();
  });

  it("rejects a target user space other than the assigned Improvement owner", async () => {
    const improvementId = await seedImprovement();
    const result = await createInsightTask(improvementId, {
      userId: "owner-1",
      botId: "bot-1",
    });

    expect(result.response.status).toBe(403);
    expect(result.body).toEqual(expect.objectContaining({ code: "TARGET_USER_FORBIDDEN" }));
    expect(await db.query("SELECT * FROM ce_tasks")).toHaveLength(0);
    expect(dispatch).not.toHaveBeenCalled();
  });

  it("rejects a Bot outside the assigned Improvement owner's accessible Bot set", async () => {
    const improvementId = await seedImprovement();
    const result = await createInsightTask(improvementId, { botId: "unowned-bot" });

    expect(result.response.status).toBe(403);
    expect(result.body).toEqual(expect.objectContaining({ code: "TARGET_BOT_FORBIDDEN" }));
    expect(await db.query("SELECT * FROM ce_tasks")).toHaveLength(0);
    expect(dispatch).not.toHaveBeenCalled();
  });

  it("does not reuse an idempotency key for a different execution target", async () => {
    await db.exec(
      `INSERT INTO ac_bots
        (id, bot_id, owner_id, entity_id, is_delete, active_engine, bot_type, status, binding_id, env)
       VALUES (?, ?, ?, ?, 0, 'openclaw', 'personal', 'active', NULL, 'pre')`,
      [2, "target-bot-2", "specialist-1", "specialist-1"],
    );
    const improvementId = await seedImprovement();
    const first = await createInsightTask(improvementId);
    expect(first.response.status).toBe(201);

    const conflicting = await createInsightTask(improvementId, { botId: "target-bot-2" });

    expect(conflicting.response.status).toBe(409);
    expect(conflicting.body).toEqual(expect.objectContaining({ code: "IDEMPOTENCY_TARGET_CONFLICT" }));
    expect(await db.query("SELECT * FROM ce_tasks")).toHaveLength(1);
    expect(dispatch).toHaveBeenCalledTimes(1);
  });

  it("freezes custom Plan and Optimize node commands for an Insight task", async () => {
    const improvementId = await seedImprovement();
    const result = await createInsightTask(improvementId, {
      nodeCommandYamls: {
        plan: `version: "1.0"\ncommand: /clawevolve-plan --strategy conservative\n`,
        optimize: `version: "1.0"\ncommand: /clawevolve-workflow --stage optimize --model antchat/Custom --suite smoke\n`,
      },
    });
    expect(result.response.status).toBe(201);
    expect(result.body.config).toEqual(expect.objectContaining({
      nodeCommands: expect.objectContaining({
        plan: expect.stringContaining("--strategy conservative"),
        optimize: expect.stringContaining("antchat/Custom"),
      }),
    }));
  });

  it("freezes one source, starts at Plan, and serves the same source across edits and retries", async () => {
    const improvementId = await seedImprovement();
    const first = await createInsightTask(improvementId);
    expect(first.response.status).toBe(201);
    const steps = first.body.steps as Array<{ stepId: string; stepType: string; command: string }>;
    expect(steps).toHaveLength(1);
    expect(steps[0]).toEqual(expect.objectContaining({ stepType: "plan" }));
    expect(steps[0].command).not.toContain("--resolve-plan-source");
    expect(steps[0].command).not.toContain("--plan-source-token");
    expect(await db.query("SELECT * FROM ce_steps WHERE step_type = 'diagnose'")).toHaveLength(0);
    expect(first.body.source).toEqual(expect.objectContaining({
      sourceType: "insight_improvement",
      sourceId: `improvement:${improvementId}`,
      status: "frozen",
      evidenceCount: 1,
    }));
    expect(dispatch).toHaveBeenCalledWith(expect.objectContaining({
      stepType: "plan",
      userId: "specialist-1",
      botId: "target-bot-1",
      command: expect.not.stringContaining("--resolve-plan-source"),
      runtime: expect.objectContaining({ env: "pre" }),
    }));
    const dispatchCommand = String(dispatch.mock.calls[0]?.[0]?.command ?? "");
    expect(dispatchCommand).not.toContain("--plan-source-token");
    expect(dispatchCommand).toContain("--owner-id specialist-1");
    const storedStep = await repo.findStep(steps[0].stepId);
    expect(storedStep?.bot_response_json).not.toContain("plan-source-token");

    await improvementRepo.update("specialist-1", improvementId, {
      title: "后续编辑不应漂移",
      expectedVersion: 2,
    });

    const inputUrl = `${baseUrl}/api/evolve/internal/tasks/${String(first.body.task_id)}/steps/${steps[0].stepId}/input`;
    const firstInputResponse = await fetch(inputUrl);
    expect(firstInputResponse.status).toBe(200);
    const firstInput = await firstInputResponse.json() as {
      protocolVersion: string;
      inputs: { planSource: { digest: string; delivery: { content: Record<string, unknown> } } };
    };
    expect(firstInput.protocolVersion).toBe("1.2");
    expect(firstInput.inputs.planSource.delivery.content).toEqual(expect.objectContaining({
      problem: { title: "修复工具失败后不降级", user_guidance: "保留可执行降级路径" },
      cases: [expect.objectContaining({ session_id: "session-1", task_index: 0 })],
      planning_hints: {
        target_context: {
          relationship: "cross_bot",
          applicability_required: true,
          source_bot: { owner_user_id: "owner-1", bot_id: "bot-1" },
          execution_target: { owner_user_id: "specialist-1", bot_id: "target-bot-1" },
        },
      },
    }));

    const secondInput = await (await fetch(inputUrl)).json() as typeof firstInput;
    expect(secondInput.inputs.planSource.digest).toBe(firstInput.inputs.planSource.digest);
    expect(secondInput.inputs.planSource.delivery.content).toEqual(firstInput.inputs.planSource.delivery.content);
    expect((await taskSourceRepo.findByTaskId(String(first.body.task_id)))?.status).toBe("ready");

    const duplicate = await createInsightTask(improvementId);
    expect(duplicate.response.status).toBe(200);
    expect(duplicate.body.task_id).toBe(first.body.task_id);
    expect(duplicate.body.idempotent).toBe(true);
    expect(dispatch).toHaveBeenCalledTimes(1);

    const failureResponse = await fetch(
      `${baseUrl}/api/evolve/internal/tasks/${String(first.body.task_id)}/steps/${steps[0].stepId}/report`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          status: "failed",
          error: {
            code: "PLAN_SOURCE_WRITE_FAILED",
            message: "目标目录只读",
            stage: "resolver",
            retryable: true,
          },
        }),
      },
    );
    expect(failureResponse.status).toBe(200);
    const failedTask = await (await fetch(
      `${baseUrl}/api/evolve/tasks/${String(first.body.task_id)}`,
    )).json() as { source: { status: string; error: { code: string; stage: string } } };
    expect(failedTask.source).toEqual(expect.objectContaining({
      status: "failed",
      error: expect.objectContaining({ code: "PLAN_SOURCE_WRITE_FAILED", stage: "resolver" }),
    }));

    const retryResponse = await fetch(
      `${baseUrl}/api/evolve/tasks/${String(first.body.task_id)}/steps/${steps[0].stepId}/retry`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
    );
    expect(retryResponse.status).toBe(201);
    const retry = await retryResponse.json() as { step: { stepId: string; command: string } };
    expect(retry.step.command).not.toContain("--plan-source-token");
    const retryDispatchCommand = String(dispatch.mock.calls[1]?.[0]?.command ?? "");
    expect(retryDispatchCommand).not.toContain("--plan-source-token");
    const retryInputUrl = `${baseUrl}/api/evolve/internal/tasks/${String(first.body.task_id)}/steps/${retry.step.stepId}/input`;
    const retryInput = await (await fetch(retryInputUrl)).json() as typeof firstInput;
    expect(retryInput.inputs.planSource.digest).toBe(firstInput.inputs.planSource.digest);
  });
});
