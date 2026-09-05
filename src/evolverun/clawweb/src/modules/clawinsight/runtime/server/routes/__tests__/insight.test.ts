import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import { createHash } from "node:crypto";
import { cp, mkdtemp, readFile, rm, unlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { SqliteDatabase, runMigrations } from "../../db.js";
import { InsightImprovementRepository } from "../../repositories/insight-improvement-repository.js";
import { InsightMetricDailyRepository } from "../../repositories/insight-metric-daily-repository.js";
import { InsightTaskIndexRepository } from "../../repositories/insight-task-index-repository.js";
import { FixtureInsightReadProvider } from "../../services/insight/providers/fixture-insight-read-provider.js";
import { DbInsightReadProvider } from "../../services/insight/providers/db-insight-read-provider.js";
import { FileEvidenceProvider } from "../../services/insight/providers/file-evidence-provider.js";
import { InsightService } from "../../services/insight/insight-service.js";
import type { DingTalkSender } from "../../services/insight/dingtalk-sender.js";
import { InsightAgentAuthorizer } from "../../services/insight/agent-auth.js";
import { GovernanceRuleProvider } from "../../services/insight/governance-rule-provider.js";
import { InsightAutoRepairRepository } from "../../repositories/insight-auto-repair-repository.js";
import { readAutoRepairRule } from "../../services/insight/auto-repair-policy.js";
import type { InsightTaskService } from "../../services/evolve/insight-task-service.js";
import type { RepairTaskService } from "../../services/repair/repair-runtime.js";
import { RepairError } from "../../services/repair/errors.js";
import type { RuleEvolutionService } from "../../services/insight/rule-evolution-service.js";
import { createInsightRouter } from "../insight.js";

const BOT_ID = "20260603_fp6to0gv";
const SESSION_ID = "7e82d8f2-a7f9-40ab-b0ac-7a6142ce3ca0";
const EVIDENCE_PATH = join(
  "evolution/pre/evidence/dev_local/20260603_fp6to0gv/20260726",
  `${SESSION_ID}.json`,
);

let db: SqliteDatabase;
let server: ReturnType<express.Application["listen"]>;
let baseUrl: string;
let consoleLog: ReturnType<typeof vi.spyOn>;
let consoleWarn: ReturnType<typeof vi.spyOn>;

beforeAll(async () => {
  consoleLog = vi.spyOn(console, "log").mockImplementation(() => undefined);
  consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  const fixtureRoot = join(process.cwd(), "server/fixtures/insight/v1");
  const service = new InsightService(
    new FixtureInsightReadProvider(fixtureRoot),
    new FileEvidenceProvider(fixtureRoot),
    new InsightImprovementRepository(db),
    null,
    "https://clawweb-pre.test",
  );
  const app = express();
  app.use(express.json());
  app.use("/api/insight/v1", createInsightRouter(service));
  server = await new Promise<ReturnType<express.Application["listen"]>>(
    (resolve) => {
      const instance = app.listen(0, () => resolve(instance));
    },
  );
  baseUrl = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});

afterAll(async () => {
  await new Promise<void>((resolve) => server.close(() => resolve()));
  await db.close();
  consoleLog.mockRestore();
  consoleWarn.mockRestore();
});

async function jsonRequestAt(
  targetBaseUrl: string,
  path: string,
  init?: RequestInit,
) {
  const response = await fetch(`${targetBaseUrl}${path}`, init);
  const body = (await response.json()) as Record<string, unknown>;
  return { response, body };
}

async function jsonRequest(path: string, init?: RequestInit) {
  return jsonRequestAt(baseUrl, path, init);
}

async function withIsolatedFixture(
  mutate: (fixtureRoot: string) => Promise<void>,
  run: (context: { baseUrl: string; db: SqliteDatabase }) => Promise<void>,
) {
  const sourceRoot = join(process.cwd(), "server/fixtures/insight/v1");
  const fixtureRoot = await mkdtemp(join(tmpdir(), "clawweb-insight-fixture-"));
  await cp(sourceRoot, fixtureRoot, { recursive: true });
  await mutate(fixtureRoot);

  const isolatedDb = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(isolatedDb, "sqlite");
  const service = new InsightService(
    new FixtureInsightReadProvider(fixtureRoot),
    new FileEvidenceProvider(fixtureRoot),
    new InsightImprovementRepository(isolatedDb),
  );
  const app = express();
  app.use(express.json());
  app.use("/api/insight/v1", createInsightRouter(service));
  const isolatedServer = await new Promise<
    ReturnType<express.Application["listen"]>
  >((resolve) => {
    const instance = app.listen(0, () => resolve(instance));
  });
  const isolatedBaseUrl = `http://127.0.0.1:${(isolatedServer.address() as { port: number }).port}`;

  try {
    await run({ baseUrl: isolatedBaseUrl, db: isolatedDb });
  } finally {
    await new Promise<void>((resolve) => isolatedServer.close(() => resolve()));
    await isolatedDb.close();
    await rm(fixtureRoot, { recursive: true, force: true });
  }
}

async function withDbInsightServer(
  run: (context: {
    baseUrl: string;
    db: SqliteDatabase;
    autoRepairRepo: InsightAutoRepairRepository;
    ruleProvider: GovernanceRuleProvider;
  }) => Promise<void>,
  dingTalkSender: DingTalkSender | null = null,
  options: {
    insightTaskService?: InsightTaskService | null;
    repairService?: RepairTaskService | null;
    agentAuthorizer?: InsightAgentAuthorizer | null;
    ruleProvider?: GovernanceRuleProvider;
    ruleEvolutionService?: RuleEvolutionService | null;
  } = {},
) {
  const isolatedDb = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(isolatedDb, "sqlite");
  const fixtureRoot = join(process.cwd(), "server/fixtures/insight/v1");
  const taskRepo = new InsightTaskIndexRepository(isolatedDb);
  const metricRepo = new InsightMetricDailyRepository(isolatedDb);
  const autoRepairRepo = new InsightAutoRepairRepository(isolatedDb);
  const ruleProvider = new GovernanceRuleProvider({
    environment: "pre",
    filePath: join(fixtureRoot, "governance-rules.json"),
  });
  const service = new InsightService(
    new DbInsightReadProvider(taskRepo, metricRepo),
    new FileEvidenceProvider(fixtureRoot),
    new InsightImprovementRepository(isolatedDb),
    dingTalkSender,
    "https://clawweb-pre.test",
  );
  const app = express();
  app.use(express.json());
  app.use((req, _res, next) => {
    req.isAdmin = req.header("X-User-Id") === "admin-1";
    next();
  });
  app.use(
    "/api/insight/v1",
    createInsightRouter(service, {
      metricWriter: metricRepo,
      taskWriter: taskRepo,
      agentAuthorizer: options.agentAuthorizer
        ?? new InsightAgentAuthorizer({ clients: {}, allowLocalUnsigned: true }),
      ruleProvider: options.ruleProvider ?? ruleProvider,
      autoRepairRepo,
      insightTaskService: options.insightTaskService ?? null,
      repairService: options.repairService ?? null,
      ruleEvolutionService: options.ruleEvolutionService ?? null,
    }),
  );
  const isolatedServer = await new Promise<
    ReturnType<express.Application["listen"]>
  >((resolve) => {
    const instance = app.listen(0, () => resolve(instance));
  });
  const isolatedBaseUrl = `http://127.0.0.1:${(isolatedServer.address() as { port: number }).port}`;

  try {
    await run({ baseUrl: isolatedBaseUrl, db: isolatedDb, autoRepairRepo, ruleProvider });
  } finally {
    await new Promise<void>((resolve) => isolatedServer.close(() => resolve()));
    await isolatedDb.close();
  }
}

async function realFailureFixtureItem(): Promise<Record<string, unknown>> {
  const fixtureRoot = join(process.cwd(), "server/fixtures/insight/v1");
  const page = JSON.parse(
    await readFile(join(fixtureRoot, "failure-task-page-1.json"), "utf8"),
  ) as { items: Array<Record<string, unknown>> };
  return page.items[0];
}

async function updateFailureFixture(
  fixtureRoot: string,
  update: (item: Record<string, unknown>) => void,
) {
  const path = join(fixtureRoot, "failure-task-page-1.json");
  const fixture = JSON.parse(await readFile(path, "utf8")) as {
    items: Array<Record<string, unknown>>;
  };
  update(fixture.items[0]);
  await writeFile(path, `${JSON.stringify(fixture, null, 2)}\n`, "utf8");
}

async function updateEvidenceFixture(
  fixtureRoot: string,
  update: (evidence: Record<string, unknown>) => void,
) {
  const path = join(fixtureRoot, EVIDENCE_PATH);
  const evidence = JSON.parse(await readFile(path, "utf8")) as Record<
    string,
    unknown
  >;
  update(evidence);
  const payload = Buffer.from(`${JSON.stringify(evidence)}\n`, "utf8");
  await writeFile(path, payload);
  const payloadEtag = createHash("sha256").update(payload).digest("hex");
  await updateFailureFixture(fixtureRoot, (item) => {
    item.payloadEtag = payloadEtag;
  });
}

function ownerHeaders(
  extra: Record<string, string> = {},
): Record<string, string> {
  return { "X-User-Id": "dev_local", ...extra };
}

function improvementBody(title = "补齐画眉 Token 与无浏览器环境鉴权") {
  return {
    botId: BOT_ID,
    title,
    userGuidance: "优先支持通过环境变量注入 token，避免依赖浏览器回调。",
    selectedTasks: [{ sessionId: SESSION_ID, taskIndex: 0 }],
  };
}

describe("Insight Center local contract", () => {
  it("lets an admin execute one approved improvement once without creating owner authorization", async () => {
    const create = vi.fn(async (input: Record<string, unknown>) => ({
      created: true,
      idempotent: false,
      task: {
        task_id: "EV-ADMIN-ONCE-1",
        task_name: "管理员代处理",
        status: "running",
      },
      steps: [],
      source: null,
      input,
    }));
    await withDbInsightServer(async ({ baseUrl: isolatedBaseUrl, autoRepairRepo }) => {
      const failureItem = await realFailureFixtureItem();
      const upsert = await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/failure-tasks/upsert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: [failureItem] }),
      });
      expect(upsert.response.status).toBe(201);

      const created = await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/improvements", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-User-Id": "admin-1", "Idempotency-Key": "admin-selected-once-1" },
        body: JSON.stringify({
          ...improvementBody("管理员代处理测试"),
          ownerUserId: "dev_local",
          sourceOwnerUserId: "dev_local",
        }),
      });
      expect(created.response.status).toBe(201);

      const executed = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/admin/improvements/${created.body.improvementId}/execute-once`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "admin-1", "Idempotency-Key": "admin-execute-once-1" },
          body: JSON.stringify({
            reason: "用户长期未处理，问题持续影响任务完成率",
            repairDirection: "仅检查测试 Bot 的相关配置",
          }),
        },
      );
      expect(executed.response.status).toBe(201);
      expect(executed.body).toEqual(expect.objectContaining({
        taskId: "EV-ADMIN-ONCE-1",
        executionMode: "ADMIN_ONCE",
        operatorUserId: "admin-1",
        targetUserId: "dev_local",
        targetBotId: BOT_ID,
        persistentAuthorization: false,
      }));
      expect(create).toHaveBeenCalledWith(expect.objectContaining({
        actorUserId: "admin-1",
        userId: "dev_local",
        botId: BOT_ID,
        adminOverrideOnce: {
          operatorUserId: "admin-1",
          reason: "用户长期未处理，问题持续影响任务完成率",
          repairDirection: "仅检查测试 Bot 的相关配置",
        },
      }));
      expect(await autoRepairRepo.list("dev_local")).toEqual([]);

      const forbidden = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/admin/improvements/${created.body.improvementId}/execute-once`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "dev_local", "Idempotency-Key": "admin-execute-once-2" },
          body: JSON.stringify({ reason: "不应允许" }),
        },
      );
      expect(forbidden.response.status).toBe(401);
    }, null, { insightTaskService: { create } as unknown as InsightTaskService });
  });

  it("routes an administrator's one-time Improvement action into Bot Repair", async () => {
    const createRepair = vi.fn(async (input: Record<string, unknown>) => ({
      taskId: "REPAIR-ADMIN-ONCE-1",
      taskName: "管理员代处理 · Bot 修复",
      status: "pending",
      insightSource: { improvementId: 1 },
      input,
    }));
    await withDbInsightServer(async ({ baseUrl: isolatedBaseUrl }) => {
      const failureItem = await realFailureFixtureItem();
      const upsert = await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/failure-tasks/upsert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: [failureItem] }),
      });
      expect(upsert.response.status).toBe(201);
      const created = await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/improvements", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-User-Id": "admin-1", "Idempotency-Key": "admin-repair-create-1" },
        body: JSON.stringify({
          ...improvementBody("管理员代处理 Repair 路由测试"),
          ownerUserId: "dev_local",
          sourceOwnerUserId: "dev_local",
        }),
      });
      expect(created.response.status).toBe(201);

      const executed = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/admin/improvements/${created.body.improvementId}/execute-once`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "admin-1", "Idempotency-Key": "admin-repair-execute-1" },
          body: JSON.stringify({
            reason: "用户长期未处理，改由管理员发起一次 Bot Repair",
            repairDirection: "只修改测试 Bot 的配置模板",
          }),
        },
      );
      expect(executed.response.status).toBe(202);
      expect(executed.body).toEqual(expect.objectContaining({
        taskId: "REPAIR-ADMIN-ONCE-1",
        executionMode: "ADMIN_ONCE",
        targetUserId: "dev_local",
        persistentAuthorization: false,
      }));
      expect(createRepair).toHaveBeenCalledWith(expect.objectContaining({
        actorUserId: "admin-1",
        isAdmin: true,
        body: expect.objectContaining({
          targetUserId: "dev_local",
          adminOverrideReason: "用户长期未处理，改由管理员发起一次 Bot Repair",
          repairDirection: "只修改测试 Bot 的配置模板",
          insightImprovementId: created.body.improvementId,
        }),
      }));
    }, null, { repairService: { createTask: createRepair } as unknown as RepairTaskService });
  });

  it("returns a RepairError from admin execution instead of wrapping it as HTTP 500", async () => {
    const createRepair = vi.fn(async () => {
      throw new RepairError(403, "repair_target_not_owned", "OCB 管理员 Repair 通道未授权");
    });
    await withDbInsightServer(async ({ baseUrl: isolatedBaseUrl }) => {
      const failureItem = await realFailureFixtureItem();
      const upsert = await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/failure-tasks/upsert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: [failureItem] }),
      });
      expect(upsert.response.status).toBe(201);
      const created = await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/improvements", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-User-Id": "admin-1", "Idempotency-Key": "admin-repair-error-create-1" },
        body: JSON.stringify({
          ...improvementBody("管理员代处理 Repair 错误映射测试"),
          ownerUserId: "dev_local",
          sourceOwnerUserId: "dev_local",
        }),
      });
      expect(created.response.status).toBe(201);
      const executed = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/admin/improvements/${created.body.improvementId}/execute-once`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "admin-1", "Idempotency-Key": "admin-repair-error-1" },
          body: JSON.stringify({ reason: "用户未处理，管理员代执行一次", repairDirection: "只修复测试 Bot" }),
        },
      );
      expect(executed.response.status).toBe(403);
      expect(executed.body).toEqual({
        error: "repair_target_not_owned",
        message: "OCB 管理员 Repair 通道未授权",
      });
    }, null, { repairService: { createTask: createRepair } as unknown as RepairTaskService });
  });

  it("creates the three v75 improvement tables", async () => {
    const rows = await db.query<{ name: string }>(
      `SELECT name FROM sqlite_master
       WHERE type = 'table' AND name LIKE 'insight_improvement_%'
       ORDER BY name`,
    );
    expect(rows.map((row) => row.name)).toEqual([
      "insight_improvement_evidence",
      "insight_improvement_evolve_link",
      "insight_improvement_item",
    ]);

    for (const table of [
      "insight_improvement_evidence",
      "insight_improvement_evolve_link",
    ]) {
      const columns = await db.query<{ name: string }>(
        `PRAGMA table_info(${table})`,
      );
      expect(columns.map((column) => column.name)).toContain("gmt_create");
      expect(columns.map((column) => column.name)).toContain("gmt_modified");
    }

    const itemColumns = await db.query<{ name: string }>(
      "PRAGMA table_info(insight_improvement_item)",
    );
    const itemColumnNames = itemColumns.map((column) => column.name);
    expect(itemColumnNames).toEqual(
      expect.arrayContaining([
        "bot_owner_user_id",
        "applied_evolve_task_id",
        "apply_request_id",
        "applied_by",
        "applied_at",
      ]),
    );
    for (const unnecessaryColumn of [
      "accepted_at",
      "accepted_by",
      "handling_mode",
      "last_handoff_at",
      "handoff_count",
    ]) {
      expect(itemColumnNames).not.toContain(unnecessaryColumn);
    }
  });

  it("creates the v76 DB-backed failure task table", async () => {
    const rows = await db.query<{ name: string }>(
      "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'insight_failure_task'",
    );
    expect(rows.map((row) => row.name)).toEqual(["insight_failure_task"]);
  });

  it("creates the v77 DB-backed daily metric snapshot table", async () => {
    const rows = await db.query<{ name: string }>(
      "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'insight_metric_daily'",
    );
    expect(rows.map((row) => row.name)).toEqual(["insight_metric_daily"]);
  });

  it("accepts AIStudio metric snapshots and serves overview/trend independently from failure tasks", async () => {
    await withDbInsightServer(async ({ baseUrl: dbBaseUrl }) => {
      const metricBody = {
        source: "aistudio",
        batchId: "metric-20260803",
        dataAsOf: "2026-08-04T08:00:00+08:00",
        items: [
          {
            sourceDt: "20260802",
            userId: "dev_local",
            botId: BOT_ID,
            botName: "HUAMEI Bot",
            isCron: false,
            totalTaskCount: 12,
            validTaskCount: 10,
            completeTaskCount: 7,
            capabilityTaskCount: 9,
            capabilityCompleteTaskCount: 7,
            autoCompleteTaskCount: 6,
            failureDistribution: { TOOL_FAILURE: 2, AWAITING_USER: 1 },
          },
          {
            sourceDt: "20260803",
            userId: "dev_local",
            botId: BOT_ID,
            botName: "HUAMEI Bot",
            isCron: false,
            totalTaskCount: 22,
            validTaskCount: 20,
            completeTaskCount: 16,
            capabilityTaskCount: 18,
            capabilityCompleteTaskCount: 16,
            autoCompleteTaskCount: 15,
            failureDistribution: { TOOL_FAILURE: 3, AWAITING_USER: 1 },
          },
        ],
      };
      const upsert = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/internal/metrics/daily/upsert",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(metricBody),
        },
      );
      expect(upsert.response.status).toBe(201);
      expect(upsert.body).toEqual({ success: true, data: { accepted: 2 } });

      const overview = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/overview",
        { headers: ownerHeaders() },
      );
      expect(overview.response.status).toBe(200);
      expect(overview.body.counts).toEqual(
        expect.objectContaining({ totalTaskCount: 34, completeTaskCount: 23 }),
      );
      expect(overview.body.rates).toEqual(
        expect.objectContaining({
          completionRate: 0.6765,
          autoCompletionRate: 0.7,
        }),
      );
      expect(overview.body.failureDistribution).toEqual([
        { failureClass: "TOOL_FAILURE", taskCount: 5, ratio: 0.7143 },
        { failureClass: "AWAITING_USER", taskCount: 2, ratio: 0.2857 },
      ]);

      const trend = await jsonRequestAt(dbBaseUrl, "/api/insight/v1/trend", {
        headers: ownerHeaders(),
      });
      expect(trend.response.status).toBe(200);
      expect(trend.body.points).toEqual([
        expect.objectContaining({
          date: "20260802",
          totalTaskCount: 12,
          completionRate: 0.5833,
        }),
        expect.objectContaining({
          date: "20260803",
          totalTaskCount: 22,
          completionRate: 0.7273,
        }),
      ]);

      const failures = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/failure-tasks",
        { headers: ownerHeaders() },
      );
      expect(failures.response.status).toBe(200);
      expect(failures.body.items).toEqual([]);
    });
  });

  it("returns admin error trend metrics for the whole site and keeps them out of user responses", async () => {
    await withDbInsightServer(async ({ baseUrl: dbBaseUrl, db: isolatedDb }) => {
      const sourceDt = new Date().toISOString().slice(0, 10).replaceAll("-", "");
      const createdAt = Math.floor(Date.now() / 1000);
      await isolatedDb.exec(
        `INSERT INTO insight_metric_daily
         (source_dt, owner_user_id, bot_id, bot_name, is_cron,
          total_task_count, valid_task_count, complete_task_count,
          capability_task_count, capability_complete_task_count, auto_complete_task_count,
          failure_distribution_json, batch_id, data_as_of)
         VALUES
          (?, 'owner-a', 'repair-bot', 'Repair Bot', 0, 10.4, 10, 6, 8.7, 5.2, 4, '{}', 'admin-trend', ?),
          (?, 'owner-b', 'other-bot', 'Other Bot', 0, 20.3, 20, 12, 18.2, 14, 10, '{}', 'admin-trend', ?),
          (?, 'dev_local', 'user-bot', 'User Bot', 0, 2, 2, 1, 2, 1, 1, '{}', 'admin-trend', ?)`,
        [sourceDt, new Date().toISOString(), sourceDt, new Date().toISOString(), sourceDt, new Date().toISOString()],
      );
      await isolatedDb.exec(
        `INSERT INTO insight_improvement_item
         (owner_user_id, bot_owner_user_id, bot_id, title, source_type,
          data_as_of, batch_id, content_fingerprint, idempotency_key,
          status, version, created_by, gmt_create)
         VALUES ('owner-a', 'owner-a', 'repair-bot', '近期改进项', 'USER_SELECTED',
                 ?, 'admin-trend', 'admin-trend-fingerprint', 'admin-trend-key',
                 'ACTIVE', 1, 'owner-a', ?)`,
        [new Date().toISOString(), createdAt],
      );

      const adminTrend = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/trend?ownerUserId=*",
        { headers: { "X-User-Id": "admin-1" } },
      );
      expect(adminTrend.response.status).toBe(200);
      expect(adminTrend.body.points).toEqual([
        expect.objectContaining({
          date: sourceDt,
          overallTaskCount: 33,
          repairBotCapabilityFailureTaskCount: 4,
        }),
      ]);

      const scopedAdminTrend = await jsonRequestAt(
        dbBaseUrl,
        `/api/insight/v1/trend?ownerUserId=owner-a`,
        { headers: { "X-User-Id": "admin-1" } },
      );
      expect(scopedAdminTrend.response.status).toBe(200);
      expect(scopedAdminTrend.body.points).toEqual([
        expect.objectContaining({
          date: sourceDt,
          overallTaskCount: 10,
          repairBotCapabilityFailureTaskCount: 4,
        }),
      ]);

      const userTrend = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/trend",
        { headers: ownerHeaders() },
      );
      expect(userTrend.response.status).toBe(200);
      expect(userTrend.body.points).toEqual([
        expect.objectContaining({ date: sourceDt }),
      ]);
      expect(userTrend.body.points[0]).not.toHaveProperty("overallTaskCount");
      expect(userTrend.body.points[0]).not.toHaveProperty("repairBotCapabilityFailureTaskCount");
    });
  });
  it("returns auto closure rate in the current user scope and excludes incomplete or forced verification", async () => {
    await withDbInsightServer(async ({ baseUrl: dbBaseUrl, db: isolatedDb }) => {
      await isolatedDb.exec(
        `INSERT INTO insight_metric_daily
         (source_dt, owner_user_id, bot_id, bot_name, is_cron,
          total_task_count, valid_task_count, complete_task_count,
          capability_task_count, capability_complete_task_count, auto_complete_task_count,
          failure_distribution_json, batch_id, data_as_of)
         VALUES ('20260901', 'owner-a', 'auto-bot', 'Auto Bot', 0,
                 10, 10, 8, 10, 8, 7, '{}', 'auto-closure', '2026-09-02T00:00:00Z')`,
      );
      await isolatedDb.exec(
        `INSERT INTO insight_improvement_item
         (owner_user_id, bot_owner_user_id, bot_id, title, user_guidance, source_type,
          data_as_of, batch_id, content_fingerprint, idempotency_key, status, version, created_by,
          applied_at, gmt_create, gmt_modified)
         VALUES
          ('owner-a', 'owner-a', 'auto-bot', '自动修复成功',
           '[用户已处理]
时间：2026-08-30T10:00:00Z
方式：AUTO_EVOLUTION

[自动验证]
状态：VERIFIED
检查时间：2026-09-01T10:00:00Z
关闭来源：AUTO_VERIFIED',
           'ADMIN_RULE_DIRECT_EVOLUTION', '2026-09-02T00:00:00Z', 'auto-closure', 'auto-closure-success', 'auto-closure-success',
           'RESOLVED', 1, 'governance-agent', '2026-08-30 10:00:00', '2026-08-30 10:00:00', '2026-09-01 10:00:00'),
          ('owner-a', 'owner-a', 'auto-bot', '自动修复仍有问题',
           '[用户已处理]
时间：2026-08-30T10:00:00Z
方式：AUTO_EVOLUTION

[自动验证]
状态：STILL_PRESENT
检查时间：2026-09-01T11:00:00Z',
           'ADMIN_RULE_DIRECT_EVOLUTION', '2026-09-02T00:00:00Z', 'auto-closure', 'auto-closure-failed', 'auto-closure-failed',
           'ACTIVE', 1, 'governance-agent', '2026-08-30 10:00:00', '2026-08-30 10:00:00', '2026-09-01 11:00:00'),
          ('owner-a', 'owner-a', 'auto-bot', '自动修复数据不足',
           '[用户已处理]
时间：2026-08-30T10:00:00Z
方式：AUTO_EVOLUTION

[自动验证]
状态：INSUFFICIENT_DATA
检查时间：2026-09-01T12:00:00Z',
           'ADMIN_RULE_DIRECT_EVOLUTION', '2026-09-02T00:00:00Z', 'auto-closure', 'auto-closure-insufficient', 'auto-closure-insufficient',
           'IN_PROGRESS', 1, 'governance-agent', '2026-08-30 10:00:00', '2026-08-30 10:00:00', '2026-09-01 12:00:00'),
          ('owner-a', 'owner-a', 'auto-bot', '强制验收不计入',
           '[用户已处理]
时间：2026-08-30T10:00:00Z
方式：AUTO_EVOLUTION

[强制验收]
状态：VERIFIED
检查时间：2026-09-01T13:00:00Z
关闭来源：FORCE_VERIFIED',
           'ADMIN_RULE_DIRECT_EVOLUTION', '2026-09-02T00:00:00Z', 'auto-closure', 'auto-closure-force', 'auto-closure-force',
           'RESOLVED', 1, 'governance-agent', '2026-08-30 10:00:00', '2026-08-30 10:00:00', '2026-09-01 13:00:00')`,
      );

      const response = await jsonRequestAt(
        dbBaseUrl,
        '/api/insight/v1/trend?from=20260901&to=20260901',
        { headers: { 'X-User-Id': 'owner-a' } },
      );
      expect(response.response.status).toBe(200);
      expect(response.body.points).toEqual([
        expect.objectContaining({
          date: '20260901',
          autoClosureRate: 0.5,
        }),
      ]);
      expect(response.body.points[0]).not.toHaveProperty('overallTaskCount');
      expect(response.body.points[0]).not.toHaveProperty('repairBotCapabilityFailureTaskCount');
    });
  });

  it("returns governance events from the repair boundary and keeps their observation window", async () => {
    await withDbInsightServer(async ({ baseUrl: dbBaseUrl, db: isolatedDb, }) => {
      await isolatedDb.exec(
        `INSERT INTO insight_metric_daily
         (source_dt, owner_user_id, bot_id, bot_name, is_cron,
          total_task_count, valid_task_count, complete_task_count,
          capability_task_count, capability_complete_task_count, auto_complete_task_count,
          failure_distribution_json, batch_id, data_as_of)
         VALUES
          ('20260802', 'dev_local', ?, 'HUAMEI Bot', 0, 10, 10, 7, 8, 6, 5, '{"TOOL_FAILURE":3}', 'events-1', '2026-08-04T08:00:00Z'),
          ('20260803', 'dev_local', ?, 'HUAMEI Bot', 0, 10, 10, 8, 8, 7, 6, '{"TOOL_FAILURE":2}', 'events-1', '2026-08-04T08:00:00Z'),
          ('20260804', 'dev_local', ?, 'HUAMEI Bot', 0, 10, 10, 9, 8, 8, 7, '{"TOOL_FAILURE":1}', 'events-1', '2026-08-04T08:00:00Z')`,
        [BOT_ID, BOT_ID, BOT_ID],
      );
      await isolatedDb.exec(
        `INSERT INTO insight_improvement_item
         (owner_user_id, bot_owner_user_id, bot_id, title, user_guidance, source_type,
          source_rule_id, data_as_of, batch_id, content_fingerprint, idempotency_key,
          status, version, created_by, applied_at)
         VALUES
          ('dev_local', 'dev_local', ?, '自动修复工具配置', '[自动验证]\n状态：PENDING', 'ADMIN_RULE_DIRECT_EVOLUTION',
           'rule.tool', '2026-08-04T08:00:00Z', 'events-1', 'fp-event-1', 'key-event-1',
           'IN_PROGRESS', 1, 'governance-agent', '2026-08-02 10:00:00'),
          ('dev_local', 'dev_local', ?, '手动修复网络配置', '[用户已处理]\n时间：2026-08-03T10:00:00+08:00', 'USER_SELECTED',
           NULL, '2026-08-04T08:00:00Z', 'events-1', 'fp-event-2', 'key-event-2',
           'IN_PROGRESS', 1, 'dev_local', NULL)`,
        [BOT_ID, BOT_ID],
      );

      const response = await jsonRequestAt(
        dbBaseUrl,
        `/api/insight/v1/trend?botId=${BOT_ID}&from=20260802&to=20260804`,
        { headers: ownerHeaders() },
      );
      expect(response.response.status).toBe(200);
      expect(response.body.governanceEvents).toEqual([
        expect.objectContaining({
          improvementId: 1,
          actionType: "DIRECT_EVOLUTION",
          verificationStatus: "PENDING",
          effectiveAt: "2026-08-02T02:00:00.000Z",
          observationEndAt: "2026-08-04T02:00:00.000Z",
          observationDays: 2,
        }),
        expect.objectContaining({
          improvementId: 2,
          actionType: null,
          verificationStatus: "PENDING",
          effectiveAt: "2026-08-03T02:00:00.000Z",
          observationEndAt: "2026-08-05T02:00:00.000Z",
        }),
      ]);
    });
  });

  it("uses the first post-todo status timestamp as the repair event fallback", async () => {
    await withDbInsightServer(async ({ baseUrl: dbBaseUrl, db: isolatedDb }) => {
      const transitionAt = Math.floor(Date.parse("2026-09-02T06:00:00Z") / 1000);
      await isolatedDb.exec(
        `INSERT INTO insight_improvement_item
         (owner_user_id, bot_owner_user_id, bot_id, title, source_type,
          data_as_of, batch_id, content_fingerprint, idempotency_key,
          status, version, created_by, gmt_modified)
         VALUES ('dev_local', 'dev_local', 'status-bot', '直接推进验收', 'USER_SELECTED',
                 '2026-09-02T06:00:00Z', 'status-event', 'status-fingerprint', 'status-key',
                 'IN_PROGRESS', 1, 'dev_local', ?)`,
        [transitionAt],
      );
      const response = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/trend",
        { headers: ownerHeaders() },
      );
      expect(response.response.status).toBe(200);
      expect(response.body.governanceEvents).toEqual([
        expect.objectContaining({
          title: "直接推进验收",
          effectiveAt: "2026-09-02T06:00:00.000Z",
        }),
      ]);
    });
  });
  it("allows only admins to aggregate metrics and failure tasks across owners", async () => {
    await withDbInsightServer(async ({ baseUrl: dbBaseUrl }) => {
      const upsertMetrics = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/internal/metrics/daily/upsert",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            source: "aistudio",
            batchId: "admin-global-metrics",
            dataAsOf: "2026-08-05T10:00:00+08:00",
            items: [
              {
                sourceDt: "20260802", userId: "owner-a", botId: "bot-a", botName: "Bot A", isCron: false,
                totalTaskCount: 10, validTaskCount: 10, completeTaskCount: 8,
                capabilityTaskCount: 10, capabilityCompleteTaskCount: 8, autoCompleteTaskCount: 7,
                failureDistribution: { TOOL_FAILURE: 2 },
              },
              {
                sourceDt: "20260802", userId: "owner-b", botId: "bot-b", botName: "Bot B", isCron: false,
                totalTaskCount: 20, validTaskCount: 20, completeTaskCount: 15,
                capabilityTaskCount: 18, capabilityCompleteTaskCount: 15, autoCompleteTaskCount: 14,
                failureDistribution: { MODEL_FAILURE: 5 },
              },
              {
                sourceDt: "20260803", userId: "owner-b", botId: "bot-b", botName: "Bot B", isCron: true,
                totalTaskCount: 30, validTaskCount: 30, completeTaskCount: 27,
                capabilityTaskCount: 28, capabilityCompleteTaskCount: 27, autoCompleteTaskCount: 26,
                failureDistribution: { TOOL_FAILURE: 3 },
              },
            ],
          }),
        },
      );
      expect(upsertMetrics.response.status).toBe(201);

      const sourceTask = await realFailureFixtureItem();
      const failureItems = [
        {
          ...sourceTask,
          sourceDt: "20260802",
          ownerUserId: "owner-a",
          botId: "bot-a",
          botName: "Bot A",
          sessionId: "session-a",
          isCron: false,
          payloadRef: "oss://antsys-agentclaw-prod/evolution/pre/evidence/owner-a/bot-a/20260802/session-a.json",
          batchId: "admin-global-failures",
          dataAsOf: "2026-08-05T10:00:00+08:00",
        },
        {
          ...sourceTask,
          sourceDt: "20260803",
          ownerUserId: "owner-b",
          botId: "bot-b",
          botName: "Bot B",
          sessionId: "session-b",
          isCron: true,
          payloadRef: "oss://antsys-agentclaw-prod/evolution/pre/evidence/owner-b/bot-b/20260803/session-b.json",
          batchId: "admin-global-failures",
          dataAsOf: "2026-08-05T10:00:00+08:00",
        },
      ];
      const upsertFailures = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/internal/failure-tasks/upsert",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source: "aistudio", items: failureItems }),
        },
      );
      expect(upsertFailures.response.status).toBe(201);

      const overview = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/overview?ownerUserId=*",
        { headers: { "X-User-Id": "admin-1" } },
      );
      expect(overview.response.status).toBe(200);
      expect(overview.body.counts).toEqual(
        expect.objectContaining({ totalTaskCount: 60, completeTaskCount: 50 }),
      );

      const filtered = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/overview?ownerUserId=*&botId=bot-b&from=20260802&to=20260802&isCron=false",
        { headers: { "X-User-Id": "admin-1" } },
      );
      expect(filtered.response.status).toBe(200);
      expect(filtered.body.counts).toEqual(
        expect.objectContaining({ totalTaskCount: 20, completeTaskCount: 15 }),
      );

      const trend = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/trend?ownerUserId=*",
        { headers: { "X-User-Id": "admin-1" } },
      );
      expect(trend.response.status).toBe(200);
      expect((trend.body.points as unknown[])).toHaveLength(2);

      const failures = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/failure-tasks?ownerUserId=*&botId=bot-b&isCron=true",
        { headers: { "X-User-Id": "admin-1" } },
      );
      expect(failures.response.status).toBe(200);
      expect(failures.body.items).toEqual([
        expect.objectContaining({ ownerUserId: "owner-b", botId: "bot-b", sessionId: "session-b" }),
      ]);

      const unauthorized = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/overview?ownerUserId=*",
        { headers: { "X-User-Id": "owner-a" } },
      );
      expect(unauthorized.response.status).toBe(401);
      expect(unauthorized.body.code).toBe("UNAUTHORIZED");
    });
  });

  it("allows Admins to read all users' improvement worklists without granting owner actions", async () => {
    await withDbInsightServer(async ({ baseUrl: dbBaseUrl, db: isolatedDb }) => {
      for (const [index, ownerUserId, status] of [
        [1, "owner-a", "ACTIVE"],
        [2, "owner-b", "IN_PROGRESS"],
      ] as const) {
        await isolatedDb.exec(
          `INSERT INTO insight_improvement_item
           (owner_user_id, bot_owner_user_id, bot_id, title, source_type, data_as_of,
            batch_id, content_fingerprint, idempotency_key, status, version, created_by)
           VALUES (?, ?, ?, ?, 'USER_SELECTED', '2026-08-17T00:00:00Z', ?, ?, ?, ?, 1, 'seed')`,
          [ownerUserId, ownerUserId, `bot-${index}`, `Owner ${index} improvement`, `batch-${index}`, `fp-${index}`, `key-${index}`, status],
        );
      }

      const all = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/improvements?ownerUserId=*&pageSize=20",
        { headers: { "X-User-Id": "admin-1" } },
      );
      expect(all.response.status).toBe(200);
      expect(all.body.items).toEqual(expect.arrayContaining([
        expect.objectContaining({ ownerUserId: "owner-a", status: "ACTIVE" }),
        expect.objectContaining({ ownerUserId: "owner-b", status: "IN_PROGRESS" }),
      ]));
      expect(all.body.statusCounts).toEqual(expect.objectContaining({ active: 1, inProgress: 1 }));

      const adminAll = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/admin/improvements?ownerUserId=*&includeAll=true&pageSize=20",
        { headers: { "X-User-Id": "admin-1" } },
      );
      expect(adminAll.response.status).toBe(200);
      expect(adminAll.body.items).toEqual(expect.arrayContaining([
        expect.objectContaining({ ownerUserId: "owner-a", status: "ACTIVE" }),
        expect.objectContaining({ ownerUserId: "owner-b", status: "IN_PROGRESS" }),
      ]));
      expect(adminAll.body.statusCounts).toEqual({
        active: 1,
        inProgress: 1,
        resolved: 0,
        archived: 0,
      });

      const unauthorized = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/improvements?ownerUserId=*",
        { headers: { "X-User-Id": "owner-a" } },
      );
      expect(unauthorized.response.status).toBe(401);
      expect(unauthorized.body.code).toBe("UNAUTHORIZED");
    });
  });

  it("previews and cleans failure tasks for multiple owners", async () => {
    await withDbInsightServer(async ({ baseUrl: dbBaseUrl, db: isolatedDb }) => {
      const sourceTask = await realFailureFixtureItem();
      const items = [
        {
          ...sourceTask,
          ownerUserId: "owner-a",
          botId: "bot-a",
          sessionId: "session-a-1",
          sourceDt: "20260805",
          payloadRef: "oss://antsys-agentclaw-prod/evolution/pre/evidence/owner-a/bot-a/20260805/session-a-1.json",
        },
        {
          ...sourceTask,
          ownerUserId: "owner-a",
          botId: "bot-a",
          sessionId: "session-a-2",
          sourceDt: "20260806",
          payloadRef: "oss://antsys-agentclaw-prod/evolution/pre/evidence/owner-a/bot-a/20260806/session-a-2.json",
        },
        {
          ...sourceTask,
          ownerUserId: "owner-b",
          botId: "bot-b",
          sessionId: "session-b-1",
          sourceDt: "20260805",
          payloadRef: "oss://antsys-agentclaw-prod/evolution/pre/evidence/owner-b/bot-b/20260805/session-b-1.json",
        },
      ];
      const upsert = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/internal/failure-tasks/upsert",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ items }),
        },
      );
      expect(upsert.response.status).toBe(201);

      const unauthorized = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/admin/failure-tasks/cleanup/preview",
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "owner-a" },
          body: JSON.stringify({ ownerUserIds: ["owner-a"] }),
        },
      );
      expect(unauthorized.response.status).toBe(401);

      const botPreview = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/admin/failure-tasks/cleanup/preview",
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "admin-1" },
          body: JSON.stringify({ ownerUserIds: ["owner-a"], botIds: ["bot-a"], sourceDt: "20260805" }),
        },
      );
      expect(botPreview.response.status).toBe(200);
      expect(botPreview.body.data).toEqual({
        matched: 1,
        byOwner: [{ ownerUserId: "owner-a", count: 1 }],
        botIds: ["bot-a"],
        sourceDt: "20260805",
      });

      const preview = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/admin/failure-tasks/cleanup/preview",
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "admin-1" },
          body: JSON.stringify({ ownerUserIds: ["owner-a", "owner-b"], sourceDt: "2026-08-05" }),
        },
      );
      expect(preview.response.status).toBe(200);
      expect(preview.body.data).toEqual({
        matched: 2,
        byOwner: [
          { ownerUserId: "owner-a", count: 1 },
          { ownerUserId: "owner-b", count: 1 },
        ],
        botIds: null,
        sourceDt: "20260805",
      });

      const executeByDate = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/admin/failure-tasks/cleanup/execute",
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "admin-1" },
          body: JSON.stringify({ ownerUserIds: ["owner-a", "owner-b"], sourceDt: "20260805" }),
        },
      );
      expect(executeByDate.response.status).toBe(200);
      expect(executeByDate.body.data).toEqual(expect.objectContaining({ deleted: 2, sourceDt: "20260805" }));

      const executeAllDates = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/admin/failure-tasks/cleanup/execute",
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "admin-1" },
          body: JSON.stringify({ ownerUserIds: ["owner-a"] }),
        },
      );
      expect(executeAllDates.response.status).toBe(200);
      expect(executeAllDates.body.data).toEqual(expect.objectContaining({ deleted: 1, sourceDt: null }));

      const remaining = await isolatedDb.query<{ count: number }>(
        "SELECT COUNT(*) AS count FROM insight_failure_task",
      );
      expect(Number(remaining[0].count)).toBe(0);
    });
  });

  it("paginates all failure classes for owners and admins without dropping later pages", async () => {
    await withDbInsightServer(async ({ baseUrl: dbBaseUrl }) => {
      const sourceTask = await realFailureFixtureItem();
      const failureClasses = ["TOOL_FAILURE", "AWAITING_USER", "EXEC_INTERRUPTED", "ASYNC_PENDING"];
      const items = Array.from({ length: 123 }, (_, index) => {
        const sessionId = `pagination-session-${String(index).padStart(3, "0")}`;
        return {
          ...sourceTask,
          ownerUserId: "pagination-owner",
          sessionId,
          taskIndex: 0,
          failureClass: failureClasses[index % failureClasses.length],
          isComplete: index % 3 === 0 ? 2 : 0,
          payloadRef: `oss://antsys-agentclaw-prod/evolution/pre/evidence/pagination-owner/${sourceTask.botId}/20260726/${sessionId}.json`,
          batchId: "pagination-all-failures",
          dataAsOf: "2026-08-11 20:55:14",
          sessionEndTime: index < 5 ? null : new Date(Date.UTC(2026, 7, 11, 20, 55, 14) - index * 60_000).toISOString(),
        };
      });
      for (let start = 0; start < items.length; start += 40) {
        const upsert = await jsonRequestAt(
          dbBaseUrl,
          "/api/insight/v1/internal/failure-tasks/upsert",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ source: "aistudio", items: items.slice(start, start + 40) }),
          },
        );
        expect(upsert.response.status).toBe(201);
      }

      const readAll = async (headers: Record<string, string>, ownerUserId?: string) => {
        const items: Array<{ sessionId: string; sessionEndTime: string | null }> = [];
        let cursor: string | undefined;
        do {
          const search = new URLSearchParams({ completionStates: "0,2,3", pageSize: "50" });
          if (ownerUserId) search.set("ownerUserId", ownerUserId);
          if (cursor) search.set("cursor", cursor);
          const response = await fetch(`${dbBaseUrl}/api/insight/v1/failure-tasks?${search}`, { headers });
          const text = await response.text();
          expect(response.status, text).toBe(200);
          const body = JSON.parse(text) as Record<string, unknown>;
          items.push(...body.items as Array<{ sessionId: string; sessionEndTime: string | null }>);
          cursor = body.nextCursor as string | undefined;
        } while (cursor);
        return items;
      };

      const ownerItems = await readAll({ "X-User-Id": "pagination-owner" });
      expect(new Set(ownerItems.map((item) => item.sessionId)).size).toBe(123);
      expect(ownerItems.slice(-5).every((item) => item.sessionEndTime === null)).toBe(true);
      const nonNullEndTimes = ownerItems.map((item) => item.sessionEndTime).filter((value): value is string => value !== null);
      expect(nonNullEndTimes).toEqual([...nonNullEndTimes].sort().reverse());

      const adminItems = await readAll({ "X-User-Id": "admin-1" }, "pagination-owner");
      expect(new Set(adminItems.map((item) => item.sessionId)).size).toBe(123);
    });
  });

  it("accepts AIStudio failure task writeback and serves bad cases from ClawWeb DB", async () => {
    await withDbInsightServer(
      async ({ baseUrl: dbBaseUrl, db: isolatedDb }) => {
        const item = await realFailureFixtureItem();
        const upsert = await jsonRequestAt(
          dbBaseUrl,
          "/api/insight/v1/internal/failure-tasks/upsert",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              source: "aistudio",
              batchId: "insight-test-db",
              dataAsOf: "2026-07-26T10:00:00+08:00",
              items: [item],
            }),
          },
        );
        expect(upsert.response.status).toBe(201);
        expect(upsert.body).toEqual({ success: true, data: { accepted: 1 } });

        const listed = await jsonRequestAt(
          dbBaseUrl,
          "/api/insight/v1/failure-tasks",
          { headers: ownerHeaders() },
        );
        expect(listed.response.status).toBe(200);
        const items = listed.body.items as Array<Record<string, unknown>>;
        expect(items).toHaveLength(1);
        expect(items[0]).toEqual(
          expect.objectContaining({
            sessionId: SESSION_ID,
            taskIndex: 0,
            failureClass: "TOOL_FAILURE",
          }),
        );

        const detail = await jsonRequestAt(
          dbBaseUrl,
          "/api/insight/v1/failure-tasks/" + SESSION_ID + "/tasks/0",
          { headers: ownerHeaders() },
        );
        expect(detail.response.status).toBe(200);
        expect(detail.body.timeline).toEqual(
          expect.objectContaining({ totalBlocks: 38 }),
        );

        const improvement = await jsonRequestAt(
          dbBaseUrl,
          "/api/insight/v1/improvements",
          {
            method: "POST",
            headers: ownerHeaders({
              "Content-Type": "application/json",
              "Idempotency-Key": "insight-test-db-create",
            }),
            body: JSON.stringify(improvementBody("DB 失败任务创建改进项")),
          },
        );
        expect(improvement.response.status).toBe(201);
        expect(improvement.body).toEqual(
          expect.objectContaining({ evidenceCount: 1, botId: BOT_ID }),
        );

        const retry = await jsonRequestAt(
          dbBaseUrl,
          "/api/insight/v1/internal/failure-tasks/upsert",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              source: "aistudio",
              batchId: "insight-test-db",
              items: [{ ...item, judgeReasonSummary: "更新后的 Judge 摘要" }],
            }),
          },
        );
        expect(retry.response.status).toBe(201);
        const rows = await isolatedDb.query<{ count: number; summary: string }>(
          "SELECT COUNT(*) AS count, MAX(judge_reason_summary) AS summary FROM insight_failure_task",
        );
        expect(Number(rows[0].count)).toBe(1);
        expect(rows[0].summary).toBe("更新后的 Judge 摘要");
      },
    );
  });

  it("rejects failure task writeback whose Evidence URI violates the OSS contract", async () => {
    await withDbInsightServer(
      async ({ baseUrl: dbBaseUrl, db: isolatedDb }) => {
        const item = await realFailureFixtureItem();
        const invalidPayloadRefs = [
          "evidence/dev_local/20260603_fp6to0gv/20260726/" +
            SESSION_ID +
            ".json",
          "oss://another-bucket/evolution/pre/evidence/dev_local/20260603_fp6to0gv/20260726/" +
            SESSION_ID +
            ".json",
          "oss://antsys-agentclaw-prod/evolution/dev/evidence/dev_local/20260603_fp6to0gv/20260726/" +
            SESSION_ID +
            ".json",
          "oss://antsys-agentclaw-prod/evolution/pre/evidence/another-user/20260603_fp6to0gv/20260726/" +
            SESSION_ID +
            ".json",
        ];

        for (const payloadRef of invalidPayloadRefs) {
          const result = await jsonRequestAt(
            dbBaseUrl,
            "/api/insight/v1/internal/failure-tasks/upsert",
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                source: "aistudio",
                items: [{ ...item, payloadRef }],
              }),
            },
          );
          expect(result.response.status).toBe(400);
          expect(result.body.code).toBe("INVALID_ARGUMENT");
        }

        const rows = await isolatedDb.query<{ count: number }>(
          "SELECT COUNT(*) AS count FROM insight_failure_task",
        );
        expect(Number(rows[0].count)).toBe(0);
      },
    );
  });

  it("serves overview from the real CSV fixture and falls back to dev_local on localhost", async () => {
    const { response, body } = await jsonRequest("/api/insight/v1/overview");
    expect(response.status).toBe(200);
    expect(body.contractVersion).toBe("insight-serving/v1");
    expect(body.scope).toEqual({ userId: "dev_local", botId: null });
    expect(body.counts).toEqual(
      expect.objectContaining({
        totalTaskCount: 3,
        completeTaskCount: 2,
      }),
    );
  });

  it("lists the HUAMEI and Admin Gate fixture Tasks and isolates users", async () => {
    const ownerResult = await jsonRequest("/api/insight/v1/failure-tasks", {
      headers: ownerHeaders(),
    });
    expect(ownerResult.response.status).toBe(200);
    const items = ownerResult.body.items as Array<Record<string, unknown>>;
    expect(items).toHaveLength(3);
    expect(items).toEqual(expect.arrayContaining([
      expect.objectContaining({
        sessionId: SESSION_ID,
        taskIndex: 0,
        isComplete: 0,
        failureClass: "TOOL_FAILURE",
        taskDescription: "测试新上传的HUAMEI skill功能，确认环境就绪并验证配置",
        messageRange: [0, 37],
      }),
      expect.objectContaining({
        sessionId: "admin-gate-auto-20260817",
        taskDescription: "抓取公开状态页并生成服务异常摘要",
        failureClass: "TOOL_FAILURE",
      }),
      expect.objectContaining({
        sessionId: "admin-gate-manual-20260817",
        taskDescription: "检索最近一周公开政策并汇总关键变化",
        failureClass: "PERMISSION_NETWORK",
      }),
    ]));

    const otherResult = await jsonRequest("/api/insight/v1/failure-tasks", {
      headers: { "X-User-Id": "another-user" },
    });
    expect(otherResult.response.status).toBe(200);
    expect(otherResult.body.items).toEqual([]);
  });

  it("serves complete timelines for the automatic and manual Admin Gate Bad Cases", async () => {
    for (const [sessionId, expectedTask, expectedFailure] of [
      ["admin-gate-auto-20260817", "抓取公开状态页并生成服务异常摘要", "TOOL_FAILURE"],
      ["admin-gate-manual-20260817", "检索最近一周公开政策并汇总关键变化", "PERMISSION_NETWORK"],
    ] as const) {
      const detail = await jsonRequest(
        `/api/insight/v1/failure-tasks/${sessionId}/tasks/0`,
        { headers: ownerHeaders() },
      );
      expect(detail.response.status).toBe(200);
      expect(detail.body.task).toEqual(expect.objectContaining({
        sessionId,
        taskDescription: expectedTask,
        failureClass: expectedFailure,
      }));
      expect((detail.body.timeline as { blocks: Array<{ kind: string }> }).blocks.map((block) => block.kind)).toEqual([
        "user_message",
        "assistant_message",
        "agent_execution",
        "assistant_message",
        "agent_execution",
        "assistant_message",
        "judge_result",
      ]);
    }
  });

  it("builds a 37-message Task timeline plus the LLM Judge result", async () => {
    const { response, body } = await jsonRequest(
      `/api/insight/v1/failure-tasks/${SESSION_ID}/tasks/0`,
      { headers: ownerHeaders() },
    );
    expect(response.status).toBe(200);
    const timeline = body.timeline as {
      totalBlocks: number;
      blocks: Array<Record<string, unknown>>;
    };
    expect(timeline.totalBlocks).toBe(38);
    expect(timeline.blocks[0]).toEqual(
      expect.objectContaining({
        blockId: "message:0",
        kind: "user_message",
      }),
    );
    expect(timeline.blocks.at(-1)).toEqual(
      expect.objectContaining({
        blockId: "judge:0",
        kind: "judge_result",
        title: "LLM Judge · TOOL_FAILURE",
      }),
    );
    expect(body.session).toEqual(
      expect.objectContaining({
        sessionId: SESSION_ID,
        botId: BOT_ID,
        botName: "支付宝反洗钱风险运营合规小助理",
        messageCount: 353,
      }),
    );
    expect(body.sessionTasks).toEqual([
      expect.objectContaining({
        taskIndex: 0,
        messageRange: [0, 37],
        isComplete: 0,
      }),
      expect.objectContaining({
        taskIndex: 1,
        messageRange: [37, 76],
        isComplete: 1,
      }),
      expect.objectContaining({
        taskIndex: 2,
        messageRange: [76, 353],
        isComplete: 1,
      }),
    ]);
  });

  it("opens sibling Tasks in the same Session through the failure Task evidence anchor", async () => {
    const { response, body } = await jsonRequest(
      `/api/insight/v1/failure-tasks/${SESSION_ID}/tasks/1?anchorTaskIndex=0`,
      { headers: ownerHeaders() },
    );
    expect(response.status).toBe(200);
    expect(body.task).toEqual(
      expect.objectContaining({
        sessionId: SESSION_ID,
        taskIndex: 1,
        isComplete: 1,
        failureClass: "COMPLETED",
      }),
    );
    expect(body.judge).toEqual(
      expect.objectContaining({
        task_index: 1,
        message_range: [37, 76],
      }),
    );
    const timeline = body.timeline as {
      totalBlocks: number;
      blocks: Array<Record<string, unknown>>;
    };
    expect(timeline.totalBlocks).toBe(40);
    expect(timeline.blocks[0]).toEqual(
      expect.objectContaining({ blockId: "message:37" }),
    );
    expect(timeline.blocks.at(-1)).toEqual(
      expect.objectContaining({
        blockId: "judge:1",
        kind: "judge_result",
      }),
    );

    const page = await jsonRequest(
      `/api/insight/v1/failure-tasks/${SESSION_ID}/tasks/1/timeline?anchorTaskIndex=0&pageSize=2`,
      { headers: ownerHeaders() },
    );
    expect(page.response.status).toBe(200);
    expect(page.body.items).toHaveLength(2);
    expect(page.body.nextCursor).toEqual(expect.any(String));
  });

  it("returns timeline summaries by page and expands only the requested long block", async () => {
    const page = await jsonRequest(
      `/api/insight/v1/failure-tasks/${SESSION_ID}/tasks/0/timeline?pageSize=5`,
      { headers: ownerHeaders() },
    );
    expect(page.response.status).toBe(200);
    const summaries = page.body.items as Array<Record<string, unknown>>;
    expect(summaries).toHaveLength(5);
    expect(page.body.nextCursor).toEqual(expect.any(String));
    expect(summaries[2].blockId).toBe("message:2");
    expect(summaries[2]).not.toHaveProperty("content");
    expect(summaries[2]).not.toHaveProperty("raw");
    const structuredAssistant = await jsonRequest(
      `/api/insight/v1/failure-tasks/${SESSION_ID}/tasks/0/timeline?blockId=message%3A1`,
      { headers: ownerHeaders() },
    );
    expect(structuredAssistant.response.status).toBe(200);
    expect(structuredAssistant.body.items[0]).toEqual(expect.objectContaining({
      title: "Agent 回复",
      preview: "查看原始消息字段",
    }));
    expect(typeof structuredAssistant.body.items[0].content).not.toBe("string");

    const markdownAssistant = await jsonRequest(
      `/api/insight/v1/failure-tasks/${SESSION_ID}/tasks/0/timeline?blockId=message%3A5`,
      { headers: ownerHeaders() },
    );
    expect(markdownAssistant.response.status).toBe(200);
    expect(markdownAssistant.body.items[0]).toEqual(expect.objectContaining({
      title: "Agent 回复",
      content: expect.any(String),
      preview: "查看原始消息字段",
    }));

    const detail = await jsonRequest(
      `/api/insight/v1/failure-tasks/${SESSION_ID}/tasks/0/timeline?blockId=message%3A2`,
      { headers: ownerHeaders() },
    );
    expect(detail.response.status).toBe(200);
    const blocks = detail.body.items as Array<Record<string, unknown>>;
    expect(blocks).toHaveLength(1);
    expect(blocks[0]).toEqual(
      expect.objectContaining({
        blockId: "message:2",
        title: "工具执行结果",
        expandable: true,
      }),
    );
    expect(Number(blocks[0].charCount)).toBeGreaterThan(10_000);
    expect(blocks[0]).toHaveProperty("content");
    expect(blocks[0]).toHaveProperty("raw");
  });

  it("does not allow another user to open the owner's failure Task", async () => {
    const { response, body } = await jsonRequest(
      `/api/insight/v1/failure-tasks/${SESSION_ID}/tasks/0`,
      { headers: { "X-User-Id": "another-user" } },
    );
    expect(response.status).toBe(404);
    expect(body.code).toBe("NOT_FOUND");
  });

  it("rejects stale Evidence ETag before returning failure Task detail", async () => {
    await withIsolatedFixture(
      async (fixtureRoot) =>
        updateFailureFixture(fixtureRoot, (item) => {
          item.payloadEtag = "0".repeat(64);
        }),
      async ({ baseUrl: isolatedBaseUrl }) => {
        const result = await jsonRequestAt(
          isolatedBaseUrl,
          `/api/insight/v1/failure-tasks/${SESSION_ID}/tasks/0`,
          { headers: ownerHeaders() },
        );
        expect(result.response.status).toBe(503);
        expect(result.body.code).toBe("DATA_NOT_READY");
        expect(String(result.body.message)).toContain("etag mismatch");
      },
    );
  });

  it("does not create an improvement when the Evidence payload is missing", async () => {
    await withIsolatedFixture(
      async (fixtureRoot) => {
        await unlink(join(fixtureRoot, EVIDENCE_PATH));
      },
      async ({ baseUrl: isolatedBaseUrl, db: isolatedDb }) => {
        const result = await jsonRequestAt(
          isolatedBaseUrl,
          "/api/insight/v1/improvements",
          {
            method: "POST",
            headers: ownerHeaders({
              "Content-Type": "application/json",
              "Idempotency-Key": "insight-test-missing-evidence",
            }),
            body: JSON.stringify(improvementBody()),
          },
        );
        expect(result.response.status).toBe(503);
        expect(result.body.code).toBe("DATA_NOT_READY");
        const rows = await isolatedDb.query<{ count: number }>(
          "SELECT COUNT(*) AS count FROM insight_improvement_item",
        );
        expect(Number(rows[0].count)).toBe(0);
      },
    );
  });

  it("rejects Evidence whose message_index is not continuous", async () => {
    await withIsolatedFixture(
      async (fixtureRoot) =>
        updateEvidenceFixture(fixtureRoot, (evidence) => {
          const messages = evidence.messages as Array<Record<string, unknown>>;
          messages[1].message_index = 0;
        }),
      async ({ baseUrl: isolatedBaseUrl }) => {
        const result = await jsonRequestAt(
          isolatedBaseUrl,
          `/api/insight/v1/failure-tasks/${SESSION_ID}/tasks/0`,
          { headers: ownerHeaders() },
        );
        expect(result.response.status).toBe(503);
        expect(result.body.code).toBe("DATA_NOT_READY");
        expect(String(result.body.message)).toContain(
          "messages[1].message_index",
        );
        expect(String(result.body.message)).toContain("数组位置 1");
      },
    );
  });

  it("does not create an improvement when Evidence message_range exceeds the message payload", async () => {
    await withIsolatedFixture(
      async (fixtureRoot) =>
        updateEvidenceFixture(fixtureRoot, (evidence) => {
          const tasks = evidence.tasks as Array<Record<string, unknown>>;
          tasks[0].message_range = [0, 354];
        }),
      async ({ baseUrl: isolatedBaseUrl, db: isolatedDb }) => {
        const result = await jsonRequestAt(
          isolatedBaseUrl,
          "/api/insight/v1/improvements",
          {
            method: "POST",
            headers: ownerHeaders({
              "Content-Type": "application/json",
              "Idempotency-Key": "insight-test-invalid-message-range",
            }),
            body: JSON.stringify(improvementBody()),
          },
        );
        expect(result.response.status).toBe(503);
        expect(result.body.code).toBe("DATA_NOT_READY");
        expect(String(result.body.message)).toContain(
          "不能超过 messages.length (353)",
        );
        const rows = await isolatedDb.query<{ count: number }>(
          "SELECT COUNT(*) AS count FROM insight_improvement_item",
        );
        expect(Number(rows[0].count)).toBe(0);
      },
    );
  });

  it("does not create an improvement when Task Index and Evidence completion states differ", async () => {
    await withIsolatedFixture(
      async (fixtureRoot) =>
        updateEvidenceFixture(fixtureRoot, (evidence) => {
          const tasks = evidence.tasks as Array<Record<string, unknown>>;
          tasks[0].is_complete = 2;
        }),
      async ({ baseUrl: isolatedBaseUrl, db: isolatedDb }) => {
        const result = await jsonRequestAt(
          isolatedBaseUrl,
          "/api/insight/v1/improvements",
          {
            method: "POST",
            headers: ownerHeaders({
              "Content-Type": "application/json",
              "Idempotency-Key": "insight-test-completion-mismatch",
            }),
            body: JSON.stringify(improvementBody()),
          },
        );
        expect(result.response.status).toBe(503);
        expect(result.body.code).toBe("DATA_NOT_READY");
        expect(String(result.body.message)).toContain(
          "完成状态或失败分类不一致",
        );
        const rows = await isolatedDb.query<{ count: number }>(
          "SELECT COUNT(*) AS count FROM insight_improvement_item",
        );
        expect(Number(rows[0].count)).toBe(0);
      },
    );
  });

  it("does not create an improvement when the Evidence schema version is unsupported", async () => {
    await withIsolatedFixture(
      async (fixtureRoot) =>
        updateEvidenceFixture(fixtureRoot, (evidence) => {
          evidence.schema_version = "session-evidence/v2";
        }),
      async ({ baseUrl: isolatedBaseUrl, db: isolatedDb }) => {
        const result = await jsonRequestAt(
          isolatedBaseUrl,
          "/api/insight/v1/improvements",
          {
            method: "POST",
            headers: ownerHeaders({
              "Content-Type": "application/json",
              "Idempotency-Key": "insight-test-schema-mismatch",
            }),
            body: JSON.stringify(improvementBody()),
          },
        );
        expect(result.response.status).toBe(503);
        expect(result.body.code).toBe("DATA_NOT_READY");
        expect(String(result.body.message)).toContain("schema_version");
        const rows = await isolatedDb.query<{ count: number }>(
          "SELECT COUNT(*) AS count FROM insight_improvement_item",
        );
        expect(Number(rows[0].count)).toBe(0);
      },
    );
  });

  it("creates an immutable evidence snapshot and handles retries idempotently", async () => {
    const first = await jsonRequest("/api/insight/v1/improvements", {
      method: "POST",
      headers: ownerHeaders({
        "Content-Type": "application/json",
        "Idempotency-Key": "insight-test-create-1",
      }),
      body: JSON.stringify(improvementBody()),
    });
    expect(first.response.status).toBe(201);
    expect(first.body.improvementId).toEqual(expect.any(Number));
    expect(Number(first.body.improvementId)).toBeGreaterThan(0);
    expect(first.body).toEqual(
      expect.objectContaining({
        ownerUserId: "dev_local",
        botOwnerUserId: "dev_local",
        botId: BOT_ID,
        evidenceCount: 1,
        sessionCount: 1,
        version: 1,
      }),
    );
    const evidence = first.body.evidence as Array<Record<string, unknown>>;
    expect(evidence[0]).toEqual(
      expect.objectContaining({
        sessionId: SESSION_ID,
        taskIndex: 0,
        failureClass: "TOOL_FAILURE",
      }),
    );

    const retry = await jsonRequest("/api/insight/v1/improvements", {
      method: "POST",
      headers: ownerHeaders({
        "Content-Type": "application/json",
        "Idempotency-Key": "insight-test-create-1",
      }),
      body: JSON.stringify(improvementBody()),
    });
    expect(retry.response.status).toBe(200);
    expect(retry.body.improvementId).toBe(first.body.improvementId);

    const conflict = await jsonRequest("/api/insight/v1/improvements", {
      method: "POST",
      headers: ownerHeaders({
        "Content-Type": "application/json",
        "Idempotency-Key": "insight-test-create-1",
      }),
      body: JSON.stringify(improvementBody("同一个幂等键的不同内容")),
    });
    expect(conflict.response.status).toBe(409);
    expect(conflict.body.code).toBe("CONFLICT");
  });

  it("sends only one summary notification per recipient for a batch of improvements", async () => {
    const sendImprovementNotification = vi.fn().mockResolvedValue({ processQueryKey: "single-process" });
    const sendImprovementBatchNotification = vi.fn().mockResolvedValue({ processQueryKey: "batch-process" });
    const dingTalkSender = {
      enabled: true,
      sendImprovementNotification,
      sendImprovementBatchNotification,
    } as unknown as DingTalkSender;

    await withDbInsightServer(async ({ baseUrl: dbBaseUrl }) => {
      const sourceTask = await realFailureFixtureItem();
      const upsert = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/internal/failure-tasks/upsert",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source: "aistudio", items: [sourceTask] }),
        },
      );
      expect(upsert.response.status).toBe(201);

      const created = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/improvements/batch",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-User-Id": "admin-1",
            "Idempotency-Key": "insight-admin-batch-notification",
          },
          body: JSON.stringify({
            items: [
              {
                ...improvementBody("第一条改进"),
                ownerUserId: "specialist-1",
                sourceOwnerUserId: "dev_local",
              },
              {
                ...improvementBody("第二条改进"),
                ownerUserId: "specialist-1",
                sourceOwnerUserId: "dev_local",
              },
            ],
          }),
        },
      );

      expect(created.response.status).toBe(201);
      expect(created.body.items).toHaveLength(2);
      expect(sendImprovementNotification).not.toHaveBeenCalled();
      expect(sendImprovementBatchNotification).toHaveBeenCalledTimes(1);
      expect(sendImprovementBatchNotification).toHaveBeenCalledWith(expect.objectContaining({
        recipientUserId: "specialist-1",
        improvements: expect.arrayContaining([
          expect.objectContaining({ title: "第一条改进", recipientUserId: "specialist-1" }),
          expect.objectContaining({ title: "第二条改进", recipientUserId: "specialist-1" }),
        ]),
      }));
    }, dingTalkSender);
  });

  it("separates an admin-assigned handler from the Bot Owner", async () => {
    const sendImprovementNotification = vi.fn().mockResolvedValue({ processQueryKey: "test-process" });
    const dingTalkSender = {
      enabled: true,
      sendImprovementNotification,
    } as unknown as DingTalkSender;
    await withDbInsightServer(async ({ baseUrl: dbBaseUrl }) => {
      const sourceTask = await realFailureFixtureItem();
      const upsert = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/internal/failure-tasks/upsert",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source: "aistudio", items: [sourceTask] }),
        },
      );
      expect(upsert.response.status).toBe(201);

      const created = await jsonRequestAt(
        dbBaseUrl,
        "/api/insight/v1/improvements",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-User-Id": "admin-1",
            "Idempotency-Key": "insight-admin-assignee-1",
          },
          body: JSON.stringify({
            ...improvementBody("管理员指派专项修复"),
            ownerUserId: "specialist-1",
            sourceOwnerUserId: "dev_local",
          }),
        },
      );
      expect(created.response.status).toBe(201);
      expect(created.body).toEqual(expect.objectContaining({
        ownerUserId: "specialist-1",
        botOwnerUserId: "dev_local",
        createdBy: "admin-1",
        sourceType: "ADMIN_SELECTED",
      }));
      expect(sendImprovementNotification).toHaveBeenCalledWith(expect.objectContaining({
        improvementId: Number(created.body.improvementId),
        recipientUserId: "specialist-1",
        title: "管理员指派专项修复",
        botId: BOT_ID,
        evidenceCount: 1,
      }));

      const improvementId = Number(created.body.improvementId);
      const handoff = await jsonRequestAt(
        dbBaseUrl,
        `/api/insight/v1/improvements/${improvementId}/handoff`,
        { headers: { "X-User-Id": "specialist-1" } },
      );
      expect(handoff.response.status).toBe(200);
      expect(handoff.body.improvement).toEqual(expect.objectContaining({
        ownerUserId: "specialist-1",
        botOwnerUserId: "dev_local",
      }));

      for (const userId of ["dev_local", "admin-1"]) {
        const hidden = await jsonRequestAt(
          dbBaseUrl,
          `/api/insight/v1/improvements/${improvementId}`,
          { headers: { "X-User-Id": userId } },
        );
        expect(hidden.response.status).toBe(404);
      }
    }, dingTalkSender);
  });

  it("rejects client-supplied identity and evidence mutation", async () => {
    const invalidCreate = await jsonRequest("/api/insight/v1/improvements", {
      method: "POST",
      headers: ownerHeaders({
        "Content-Type": "application/json",
        "Idempotency-Key": "insight-test-invalid-user",
      }),
      body: JSON.stringify({ ...improvementBody(), userId: "another-user" }),
    });
    expect(invalidCreate.response.status).toBe(400);
    expect(String(invalidCreate.body.message)).toContain("服务端注入");

    const created = await jsonRequest("/api/insight/v1/improvements", {
      method: "POST",
      headers: ownerHeaders({
        "Content-Type": "application/json",
        "Idempotency-Key": "insight-test-update-1",
      }),
      body: JSON.stringify(improvementBody("待更新改进项")),
    });
    const improvementId = Number(created.body.improvementId);
    const invalidPatch = await jsonRequest(
      `/api/insight/v1/improvements/${improvementId}`,
      {
        method: "PATCH",
        headers: ownerHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          title: "不应成功",
          selectedTasks: [{ sessionId: "other", taskIndex: 0 }],
        }),
      },
    );
    expect(invalidPatch.response.status).toBe(400);
    expect(String(invalidPatch.body.message)).toContain("不可修改");
  });

  it("starts an active improvement without requiring an Agent handoff", async () => {
    const created = await jsonRequest("/api/insight/v1/improvements", {
      method: "POST",
      headers: ownerHeaders({
        "Content-Type": "application/json",
        "Idempotency-Key": "insight-test-start-processing-1",
      }),
      body: JSON.stringify(improvementBody("直接开始处理")),
    });
    const improvementId = Number(created.body.improvementId);

    const started = await jsonRequest(
      `/api/insight/v1/improvements/${improvementId}`,
      {
        method: "PATCH",
        headers: ownerHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ status: "IN_PROGRESS", version: 1 }),
      },
    );
    expect(started.response.status).toBe(200);
    expect(started.body).toEqual(expect.objectContaining({ status: "IN_PROGRESS", version: 2 }));

    const repeated = await jsonRequest(
      `/api/insight/v1/improvements/${improvementId}`,
      {
        method: "PATCH",
        headers: ownerHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ status: "IN_PROGRESS", version: 2 }),
      },
    );
    expect(repeated.response.status).toBe(409);
    expect(repeated.body.code).toBe("CONFLICT");
  });

  it("archives and restores an improvement with optimistic locking", async () => {
    const created = await jsonRequest("/api/insight/v1/improvements", {
      method: "POST",
      headers: ownerHeaders({
        "Content-Type": "application/json",
        "Idempotency-Key": "insight-test-status-transition-1",
      }),
      body: JSON.stringify(improvementBody("状态流转测试")),
    });
    const improvementId = Number(created.body.improvementId);

    const archived = await jsonRequest(
      `/api/insight/v1/improvements/${improvementId}`,
      {
        method: "PATCH",
        headers: ownerHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ status: "ARCHIVED", version: 1 }),
      },
    );
    expect(archived.response.status).toBe(200);
    expect(archived.body).toEqual(
      expect.objectContaining({ status: "ARCHIVED", version: 2 }),
    );

    const restored = await jsonRequest(
      `/api/insight/v1/improvements/${improvementId}`,
      {
        method: "PATCH",
        headers: ownerHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ status: "ACTIVE", version: 2 }),
      },
    );
    expect(restored.response.status).toBe(200);
    expect(restored.body).toEqual(
      expect.objectContaining({ status: "ACTIVE", version: 3 }),
    );

    const invalid = await jsonRequest(
      `/api/insight/v1/improvements/${improvementId}`,
      {
        method: "PATCH",
        headers: ownerHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ status: "ACTIVE", version: 3 }),
      },
    );
    expect(invalid.response.status).toBe(409);
    expect(invalid.body.code).toBe("CONFLICT");
  });

  it("allows an active improvement to be marked resolved without copying to Agent", async () => {
    const created = await jsonRequest("/api/insight/v1/improvements", {
      method: "POST",
      headers: ownerHeaders({
        "Content-Type": "application/json",
        "Idempotency-Key": "insight-test-direct-resolve-1",
      }),
      body: JSON.stringify(improvementBody("用户自行修复后直接完成")),
    });
    expect(created.response.status).toBe(201);

    const resolved = await jsonRequest(
      `/api/insight/v1/improvements/${Number(created.body.improvementId)}`,
      {
        method: "PATCH",
        headers: ownerHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ status: "RESOLVED", version: 1 }),
      },
    );
    expect(resolved.response.status).toBe(200);
    expect(resolved.body).toEqual(expect.objectContaining({
      status: "RESOLVED",
      version: 2,
    }));
  });

  it("lists open improvements for proactive verification and closes active or unhandled items", async () => {
    await withIsolatedFixture(
      async () => undefined,
      async ({ baseUrl: isolatedBaseUrl, db: isolatedDb }) => {
        const create = (key: string, title: string) => jsonRequestAt(
          isolatedBaseUrl,
          "/api/insight/v1/improvements",
          {
            method: "POST",
            headers: ownerHeaders({
              "Content-Type": "application/json",
              "Idempotency-Key": key,
            }),
            body: JSON.stringify(improvementBody(title)),
          },
        );

        const active = await create("proactive-open-active", "主动验收待处理项");
        expect(active.response.status).toBe(201);
        const activeId = Number(active.body.improvementId);

        const inProgress = await create("proactive-open-progress", "主动验收处理中项");
        expect(inProgress.response.status).toBe(201);
        const inProgressId = Number(inProgress.body.improvementId);
        const sevenDaysAgo = Math.floor(Date.now() / 1000) - 8 * 24 * 60 * 60;
        await isolatedDb.exec(
          "UPDATE insight_improvement_item SET status = 'IN_PROGRESS', version = version + 1, gmt_modified = ? WHERE id = ?",
          [sevenDaysAgo, inProgressId],
        );

        const open = await jsonRequestAt(
          isolatedBaseUrl,
          "/api/insight/v1/internal/governance/verification-candidates/open?limit=100",
        );
        expect(open.response.status).toBe(200);
        expect(open.body.items).toEqual(expect.arrayContaining([
          expect.objectContaining({ improvementId: activeId, status: "ACTIVE" }),
          expect.objectContaining({ improvementId: inProgressId, status: "IN_PROGRESS", handledAt: null }),
        ]));

        const premature = await jsonRequestAt(
          isolatedBaseUrl,
          "/api/insight/v1/internal/governance/verification-results/open",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              improvementId: activeId,
              version: active.body.version,
              outcome: "DISAPPEARED",
              newSessionCount: 1,
            }),
          },
        );
        expect(premature.response.status).toBe(409);
        expect(premature.body.code).toBe("OPEN_VERIFICATION_TOO_EARLY");
        await isolatedDb.exec(
          "UPDATE insight_improvement_item SET gmt_modified = ? WHERE id = ?",
          [sevenDaysAgo, activeId],
        );

        const invalid = await jsonRequestAt(
          isolatedBaseUrl,
          "/api/insight/v1/internal/governance/verification-results/open",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              improvementId: activeId,
              version: active.body.version,
              outcome: "DISAPPEARED",
              newSessionCount: 1,
              status: "RESOLVED",
            }),
          },
        );
        expect(invalid.response.status).toBe(400);

        const activeResolved = await jsonRequestAt(
          isolatedBaseUrl,
          "/api/insight/v1/internal/governance/verification-results/open",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              improvementId: activeId,
              version: active.body.version,
              outcome: "DISAPPEARED",
              newSessionCount: 1,
            }),
          },
        );
        expect(activeResolved.response.status).toBe(200);
        expect(activeResolved.body.improvement).toEqual(expect.objectContaining({
          improvementId: activeId,
          status: "RESOLVED",
          verificationStatus: "VERIFIED",
          resolvedSource: "AUTO_VERIFIED",
        }));

        const progressResolved = await jsonRequestAt(
          isolatedBaseUrl,
          "/api/insight/v1/internal/governance/verification-results/open",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              improvementId: inProgressId,
              version: Number(inProgress.body.version) + 1,
              outcome: "DISAPPEARED",
              newSessionCount: 1,
            }),
          },
        );
        expect(progressResolved.response.status).toBe(200);
        expect(progressResolved.body.improvement).toEqual(expect.objectContaining({
          improvementId: inProgressId,
          status: "RESOLVED",
          verificationStatus: "VERIFIED",
        }));

        const normalQueue = await jsonRequestAt(
          isolatedBaseUrl,
          "/api/insight/v1/internal/governance/verification-candidates?limit=100",
        );
        expect(normalQueue.response.status).toBe(200);
        expect(normalQueue.body.items).toEqual([]);
      },
    );
  });

  it("filters improvement work views on the server and returns complete status counts", async () => {
    await withIsolatedFixture(
      async () => undefined,
      async ({ baseUrl: isolatedBaseUrl, db: isolatedDb }) => {
        const createdIds: number[] = [];
        for (const [index, title] of [
          "待处理项",
          "处理中项",
          "已处理项",
          "已废弃项",
        ].entries()) {
          const created = await jsonRequestAt(
            isolatedBaseUrl,
            "/api/insight/v1/improvements",
            {
              method: "POST",
              headers: ownerHeaders({
                "Content-Type": "application/json",
                "Idempotency-Key": `insight-status-filter-${index}`,
              }),
              body: JSON.stringify(improvementBody(title)),
            },
          );
          expect(created.response.status).toBe(201);
          createdIds.push(Number(created.body.improvementId));
        }

        for (const [index, status] of [
          "IN_PROGRESS",
          "RESOLVED",
          "ARCHIVED",
        ].entries()) {
          await isolatedDb.exec(
            `UPDATE insight_improvement_item
                SET status = ?, version = version + 1
              WHERE id = ?`,
            [status, createdIds[index + 1]],
          );
        }

        const active = await jsonRequestAt(
          isolatedBaseUrl,
          "/api/insight/v1/improvements?status=ACTIVE&pageSize=20",
          { headers: ownerHeaders() },
        );
        expect(active.response.status).toBe(200);
        expect(active.body.items).toEqual([
          expect.objectContaining({
            improvementId: createdIds[0],
            status: "ACTIVE",
          }),
        ]);
        expect(active.body.statusCounts).toEqual({
          active: 1,
          inProgress: 1,
          resolved: 1,
          archived: 1,
        });

        const processing = await jsonRequestAt(
          isolatedBaseUrl,
          "/api/insight/v1/improvements?status=IN_PROGRESS&pageSize=20",
          { headers: ownerHeaders() },
        );
        expect(processing.response.status).toBe(200);
        expect(processing.body.items).toEqual([
          expect.objectContaining({
            improvementId: createdIds[1],
            status: "IN_PROGRESS",
          }),
        ]);

        const invalid = await jsonRequestAt(
          isolatedBaseUrl,
          "/api/insight/v1/improvements?status=UNKNOWN",
          { headers: ownerHeaders() },
        );
        expect(invalid.response.status).toBe(400);
        expect(invalid.body.code).toBe("INVALID_ARGUMENT");
      },
    );
  });

  it("updates only title/guidance and exposes a safe Evolve handoff", async () => {
    const created = await jsonRequest("/api/insight/v1/improvements", {
      method: "POST",
      headers: ownerHeaders({
        "Content-Type": "application/json",
        "Idempotency-Key": "insight-test-handoff-1",
      }),
      body: JSON.stringify(improvementBody("进化室衔接测试")),
    });
    const improvementId = Number(created.body.improvementId);
    const updated = await jsonRequest(
      `/api/insight/v1/improvements/${improvementId}`,
      {
        method: "PATCH",
        headers: ownerHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          title: "进化室衔接测试（已更新）",
          userGuidance: "先解决凭据注入，再做浏览器降级。",
          version: 1,
        }),
      },
    );
    expect(updated.response.status).toBe(200);
    expect(updated.body).toEqual(
      expect.objectContaining({
        title: "进化室衔接测试（已更新）",
        version: 2,
      }),
    );

    const handoff = await jsonRequest(
      `/api/insight/v1/improvements/${improvementId}/handoff`,
      {
        headers: ownerHeaders(),
      },
    );
    expect(handoff.response.status).toBe(200);
    expect(handoff.body.contractVersion).toBe("insight-improvement-handoff/v1");
    expect(handoff.body.improvement).toEqual(expect.objectContaining({
      ownerUserId: "dev_local",
      botOwnerUserId: "dev_local",
    }));
    expect(handoff.body.evidence[0]).toEqual(
      expect.objectContaining({
        payloadRef: expect.stringMatching(/^oss:\/\//),
        payloadEtag: expect.any(String),
        payloadVersionId: null,
        evidenceAccessUrl: expect.stringMatching(/^https:\/\/clawweb-pre\.test\/api\/insight\/v1\/evidence-access\//),
      }),
    );
    expect(handoff.body.agentMarkdown).toEqual(expect.stringContaining("# ClawWeb 失败任务修复交接"));
    expect(handoff.body.agentMarkdown).toEqual(expect.stringContaining(`CLAWWEB_IMPROVEMENT_ID=${improvementId}`));
    expect(handoff.body.agentMarkdown).not.toEqual(expect.stringContaining(`CLAWWEB_IMPROVEMENT_APPLIED=${improvementId}`));
    expect(handoff.body.agentMarkdown).toEqual(expect.stringContaining("你的任务不是简单重跑失败任务"));
    expect(handoff.body.agentMarkdown).toEqual(expect.stringContaining('STATE_DIR="${OPENCLAW_STATE_DIR:-$HOME/.openclaw}"'));
    expect(handoff.body.agentMarkdown).toEqual(expect.stringContaining("Task Index（ClawWeb/Judge 切分索引）"));
    expect(handoff.body.agentMarkdown).toEqual(expect.stringContaining("Task Index、任务边界、完成状态和失败分类来自后者"));
    expect(handoff.body.agentMarkdown).toEqual(expect.stringContaining("`.jsonl` 是原始会话转录"));
    expect(handoff.body.agentMarkdown).toEqual(expect.stringContaining("sessions.json`，它只用于把 Session ID 映射到实际 `sessionFile`"));
    expect(handoff.body.agentMarkdown).not.toEqual(expect.stringContaining(".trajectory.jsonl"));
    expect(handoff.body.agentMarkdown).not.toEqual(expect.stringContaining(".trajectory-path.json"));
    expect(handoff.body.agentMarkdown).toEqual(expect.stringContaining("不要截断或手工改写"));
    expect(handoff.body.agentMarkdown).toEqual(expect.stringContaining("防火墙内部的只读直达接口"));
    expect(handoff.body.agentMarkdown).toEqual(expect.stringContaining("EVIDENCE_URL='https://clawweb-pre.test/api/insight/v1/evidence-access/"));
    expect(handoff.body.agentMarkdown).toEqual(expect.stringContaining("不需要 Cookie、登录态、Authorization Header 或额外 Token"));
    expect(handoff.body.agentMarkdown).not.toEqual(expect.stringContaining("不一致时停止修改"));
    expect(handoff.body.agentMarkdown).toEqual(expect.stringContaining("确实修改了至少一个 Workspace 文件"));
    expect(handoff.body.agentMarkdown).toEqual(expect.stringContaining("CLAWWEB_IMPROVEMENT_APPLIED"));
  });

  it("copies a self-repair handoff, advances status, and exposes direct Evidence without login", async () => {
    const created = await jsonRequest("/api/insight/v1/improvements", {
      method: "POST",
      headers: ownerHeaders({
        "Content-Type": "application/json",
        "Idempotency-Key": "insight-self-repair-handoff-1",
      }),
      body: JSON.stringify(improvementBody("Agent 自修复测试")),
    });
    const improvementId = Number(created.body.improvementId);
    const handoff = await jsonRequest(`/api/insight/v1/improvements/${improvementId}/handoff`, {
      headers: ownerHeaders(),
    });
    expect(handoff.response.status).toBe(200);
    const accessUrl = String((handoff.body.evidence as Array<Record<string, unknown>>)[0].evidenceAccessUrl);
    const evidenceResponse = await jsonRequestAt(baseUrl, new URL(accessUrl).pathname);
    expect(evidenceResponse.response.status).toBe(200);
    expect(evidenceResponse.body).toEqual(expect.objectContaining({
      session_id: SESSION_ID,
      bot_id: BOT_ID,
    }));
    const accepted = await jsonRequest(
      `/api/insight/v1/improvements/${improvementId}/self-repair-handoff`,
      {
        method: "POST",
        headers: ownerHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ version: 1 }),
      },
    );
    expect(accepted.response.status).toBe(200);
    expect(accepted.body).toEqual(expect.objectContaining({
      status: "IN_PROGRESS",
      version: 2,
      gmtModified: expect.anything(),
    }));

    const copiedAgain = await jsonRequest(
      `/api/insight/v1/improvements/${improvementId}/self-repair-handoff`,
      {
        method: "POST",
        headers: ownerHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ version: 2 }),
      },
    );
    expect(copiedAgain.response.status).toBe(200);
    expect(copiedAgain.body).toEqual(expect.objectContaining({
      status: "IN_PROGRESS",
      version: 3,
      gmtModified: expect.anything(),
    }));

    const resolved = await jsonRequest(`/api/insight/v1/improvements/${improvementId}`, {
      method: "PATCH",
      headers: ownerHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ status: "RESOLVED", version: 3 }),
    });
    expect(resolved.response.status).toBe(200);
    expect(resolved.body).toEqual(expect.objectContaining({ status: "RESOLVED", version: 4 }));
  });

  it("marks an in-progress improvement resolved only after an idempotent Apply callback", async () => {
    await withIsolatedFixture(
      async () => undefined,
      async ({ baseUrl: isolatedBaseUrl, db: isolatedDb }) => {
        const created = await jsonRequestAt(
          isolatedBaseUrl,
          "/api/insight/v1/improvements",
          {
            method: "POST",
            headers: ownerHeaders({
              "Content-Type": "application/json",
              "Idempotency-Key": "insight-apply-callback-create",
            }),
            body: JSON.stringify(improvementBody("Apply 回写状态测试")),
          },
        );
        expect(created.response.status).toBe(201);
        const improvementId = Number(created.body.improvementId);
        await isolatedDb.exec(
          `UPDATE insight_improvement_item
              SET status = 'IN_PROGRESS', version = version + 1
            WHERE id = ?`,
          [improvementId],
        );

        const callbackBody = {
          applyTaskId: "EV-APPLY-001",
          requestId: "apply-callback-001",
          appliedBy: "dev_local",
        };
        const applied = await jsonRequestAt(
          isolatedBaseUrl,
          `/api/insight/v1/internal/improvements/${improvementId}/applied`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(callbackBody),
          },
        );
        expect(applied.response.status).toBe(200);
        expect(applied.body).toEqual(
          expect.objectContaining({
            success: true,
            idempotent: false,
            improvement: expect.objectContaining({
              status: "IN_PROGRESS",
              verificationStatus: "PENDING",
              appliedEvolveTaskId: "EV-APPLY-001",
              appliedBy: "dev_local",
              appliedAt: expect.anything(),
              version: 3,
            }),
          }),
        );

        const retry = await jsonRequestAt(
          isolatedBaseUrl,
          `/api/insight/v1/internal/improvements/${improvementId}/applied`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(callbackBody),
          },
        );
        expect(retry.response.status).toBe(200);
        expect(retry.body).toEqual(
          expect.objectContaining({
            idempotent: true,
            improvement: expect.objectContaining({ version: 3 }),
          }),
        );

        const conflict = await jsonRequestAt(
          isolatedBaseUrl,
          `/api/insight/v1/internal/improvements/${improvementId}/applied`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              ...callbackBody,
              applyTaskId: "EV-APPLY-OTHER",
              requestId: "apply-callback-other",
            }),
          },
        );
        expect(conflict.response.status).toBe(409);
        expect(conflict.body.code).toBe("IMPROVEMENT_STATE_CONFLICT");

        const archived = await jsonRequestAt(
          isolatedBaseUrl,
          "/api/insight/v1/improvements",
          {
            method: "POST",
            headers: ownerHeaders({
              "Content-Type": "application/json",
              "Idempotency-Key": "insight-apply-callback-archived",
            }),
            body: JSON.stringify(improvementBody("废弃项不能被 Apply 覆盖")),
          },
        );
        const archivedId = Number(archived.body.improvementId);
        await isolatedDb.exec(
          "UPDATE insight_improvement_item SET status = 'ARCHIVED' WHERE id = ?",
          [archivedId],
        );
        const archivedCallback = await jsonRequestAt(
          isolatedBaseUrl,
          `/api/insight/v1/internal/improvements/${archivedId}/applied`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              applyTaskId: "EV-APPLY-ARCHIVED",
              requestId: "apply-callback-archived",
              appliedBy: "dev_local",
            }),
          },
        );
        expect(archivedCallback.response.status).toBe(409);
        expect(archivedCallback.body.code).toBe("IMPROVEMENT_STATE_CONFLICT");
      },
    );
  });
  it("allows unauthenticated candidate creation but forces the first Admin-gated state", async () => {
    const lockedAuthorizer = new InsightAgentAuthorizer({
      clients: {
        "governance-agent": {
          secret: "test-only-governance-secret",
          scopes: ["action.write"],
        },
      },
      allowLocalUnsigned: false,
    });
    await withDbInsightServer(async ({ baseUrl: isolatedBaseUrl, db: isolatedDb }) => {
      const failureItem = await realFailureFixtureItem();
      const upsert = await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/failure-tasks/upsert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: [failureItem] }),
      });
      expect(upsert.response.status).toBe(201);

      const action = await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/governance/actions", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": "governance-unauthenticated-candidate-1",
        },
        body: JSON.stringify({
          ...improvementBody("免鉴权候选改进项"),
          ownerUserId: "dev_local",
          sourceOwnerUserId: "dev_local",
          sourceRuleId: "tool.web-search.use-asap",
          actionType: "ASSIGN_OWNER",
          assignmentReason: "需要 Owner 确认配置",
          rootCauseSummary: "测试候选改进项只能进入 Admin Gate",
          suggestedAction: "由 Admin 决定是否派发。",
          status: "ACTIVE",
          adminReviewStatus: "APPROVED",
          sourceType: "TRUSTED_RULE_ASSIGN_OWNER",
          createdBy: "spoofed-caller",
        }),
      });
      expect(action.response.status).toBe(201);
      expect(action.body).toEqual(expect.objectContaining({
        status: "PENDING_ADMIN",
        adminReviewStatus: "PENDING",
        sourceType: "ADMIN_RULE_ASSIGN_OWNER",
        createdBy: "governance-agent",
        version: 1,
      }));
      expect(await isolatedDb.query("SELECT * FROM ce_tasks")).toHaveLength(0);

      const unauthenticatedVerification = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/internal/governance/verification-candidates",
      );
      expect(unauthenticatedVerification.response.status).toBe(200);
      expect(unauthenticatedVerification.body.items).toEqual([]);
    }, null, { agentAuthorizer: lockedAuthorizer });
  });

  it("gates governance Actions behind Admin review and auto-closes through unsigned verification", async () => {
    const lockedAuthorizer = new InsightAgentAuthorizer({
      clients: {},
      allowLocalUnsigned: false,
    });
    await withDbInsightServer(async ({ baseUrl: isolatedBaseUrl, db: isolatedDb }) => {
      const failureItem = await realFailureFixtureItem();
      const upsert = await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/failure-tasks/upsert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: [failureItem] }),
      });
      expect(upsert.response.status).toBe(201);

      const action = await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/governance/actions", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": "governance-admin-gate-1" },
        body: JSON.stringify({
          ...improvementBody("语雀调用方式需要治理"),
          ownerUserId: "dev_local",
          sourceOwnerUserId: "dev_local",
          sourceRuleId: "tool.yuque.use-skylark-mcp",
          actionType: "ASSIGN_OWNER",
          assignmentReason: "需要当前 Bot Owner 修改私有 tools.md",
          rootCauseSummary: "语雀任务持续使用不支持的调用方式",
          suggestedAction: "改用指定语雀 MCP，并检查权限。",
        }),
      });
      expect(action.response.status).toBe(201);
      expect(action.body).toEqual(expect.objectContaining({
        status: "PENDING_ADMIN",
        adminReviewStatus: "PENDING",
        sourceType: "ADMIN_RULE_ASSIGN_OWNER",
      }));
      const improvementId = Number(action.body.improvementId);

      const hidden = await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/improvements?status=ACTIVE", { headers: ownerHeaders() });
      expect(hidden.response.status).toBe(200);
      expect(hidden.body.items).toEqual([]);

      const adminQueue = await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/admin/improvements?adminReviewStatus=PENDING", { headers: { "X-User-Id": "admin-1" } });
      expect(adminQueue.response.status).toBe(200);
      expect(adminQueue.body.items).toHaveLength(1);
      expect(adminQueue.body.reviewCounts).toEqual(expect.objectContaining({ pending: 1 }));

      const approved = await jsonRequestAt(isolatedBaseUrl, `/api/insight/v1/admin/improvements/${improvementId}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-User-Id": "admin-1" },
        body: JSON.stringify({ decision: "APPROVE", version: action.body.version }),
      });
      expect(approved.response.status).toBe(200);
      expect(approved.body).toEqual(expect.objectContaining({ status: "ACTIVE", adminReviewStatus: "APPROVED" }));

      const pendingAfterApproval = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/admin/improvements?adminReviewStatus=PENDING",
        { headers: { "X-User-Id": "admin-1" } },
      );
      expect(pendingAfterApproval.response.status).toBe(200);
      expect(pendingAfterApproval.body.items).toEqual([]);
      expect(pendingAfterApproval.body.reviewCounts).toEqual(expect.objectContaining({ pending: 0, approved: 1 }));

      const unknownVerificationField = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/internal/governance/verification-results",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            improvementId,
            version: approved.body.version,
            outcome: "DISAPPEARED",
            newSessionCount: 3,
            status: "RESOLVED",
          }),
        },
      );
      expect(unknownVerificationField.response.status).toBe(400);

      const prematureVerification = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/internal/governance/verification-results",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            improvementId,
            version: approved.body.version,
            outcome: "DISAPPEARED",
            newSessionCount: 3,
          }),
        },
      );
      expect(prematureVerification.response.status).toBe(409);

      const handled = await jsonRequestAt(isolatedBaseUrl, `/api/insight/v1/improvements/${improvementId}/handled`, {
        method: "POST",
        headers: ownerHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ version: approved.body.version }),
      });
      expect(handled.response.status).toBe(200);
      expect(handled.body).toEqual(expect.objectContaining({ status: "IN_PROGRESS", verificationStatus: "PENDING" }));
      const prematureSuccess = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/internal/governance/verification-results/resolved",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ improvementId, version: handled.body.version, newSessionCount: 3 }),
        },
      );
      expect(prematureSuccess.response.status).toBe(409);
      expect(prematureSuccess.body.code).toBe("VERIFICATION_TOO_EARLY");
      await isolatedDb.exec(
        "UPDATE insight_improvement_item SET user_guidance = user_guidance || ? WHERE id = ?",
        ["\n\n[用户已处理]\n时间：2026-08-20T00:00:00+08:00", improvementId],
      );

      const resolvedWithOutcome = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/internal/governance/verification-results/resolved",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            improvementId,
            version: handled.body.version,
            outcome: "DISAPPEARED",
            newSessionCount: 3,
          }),
        },
      );
      expect(resolvedWithOutcome.response.status).toBe(400);

      const resolvedWithoutSession = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/internal/governance/verification-results/resolved",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ improvementId, version: handled.body.version, newSessionCount: 0 }),
        },
      );
      expect(resolvedWithoutSession.response.status).toBe(400);

      const verified = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/internal/governance/verification-results/resolved",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ improvementId, version: handled.body.version, newSessionCount: 3 }),
        },
      );
      expect(verified.response.status).toBe(200);
      expect(verified.body.improvement).toEqual(expect.objectContaining({
        status: "RESOLVED",
        verificationStatus: "VERIFIED",
        resolvedSource: "AUTO_VERIFIED",
      }));
    }, null, { agentAuthorizer: lockedAuthorizer });
  });

  it("exposes approved direct evolution Actions for Owner authorization", async () => {
    await withDbInsightServer(async ({ baseUrl: isolatedBaseUrl, db: isolatedDb }) => {
      const failureItem = await realFailureFixtureItem();
      await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/failure-tasks/upsert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: [failureItem] }),
      });
      const action = await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/governance/actions", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": "governance-direct-evolution-1" },
        body: JSON.stringify({
          ...improvementBody("工具选择规则可以自动修复"),
          ownerUserId: "dev_local",
          sourceOwnerUserId: "dev_local",
          sourceRuleId: "tool.utoo-proxy.unsupported",
          actionType: "DIRECT_EVOLUTION",
          assignmentReason: "规则确定且修改范围仅限 tools.md",
          rootCauseSummary: "错误使用仅支持 Mac 的 UTOO_PROXY",
          suggestedAction: "更新 tools.md 中的工具选择约束。",
        }),
      });
      const improvementId = Number(action.body.improvementId);
      const reviewed = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/admin/improvements/${improvementId}/review`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "admin-1" },
          body: JSON.stringify({ decision: "APPROVE", comment: "允许派发给用户确认授权", version: action.body.version }),
        },
      );
      expect(reviewed.response.status).toBe(200);
      expect(reviewed.body).toEqual(expect.objectContaining({
        actionType: "DIRECT_EVOLUTION",
        sourceType: "ADMIN_RULE_DIRECT_EVOLUTION",
        adminReviewStatus: "APPROVED",
        adminReviewedBy: "admin-1",
        adminReviewComment: "允许派发给用户确认授权",
        status: "ACTIVE",
        latestEvolveTaskId: null,
      }));

      const trustRejected = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/admin/improvements/${improvementId}/review`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "admin-1" },
          body: JSON.stringify({ decision: "TRUST", version: reviewed.body.version }),
        },
      );
      expect(trustRejected.response.status).toBe(400);
      expect(String(trustRejected.body.message)).toContain("APPROVE 或 REJECT");

      const ownerItems = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/improvements",
        { headers: ownerHeaders() },
      );
      expect(ownerItems.response.status).toBe(200);
      expect(ownerItems.body.items).toEqual([
        expect.objectContaining({
          improvementId,
          actionType: "DIRECT_EVOLUTION",
          status: "ACTIVE",
        }),
      ]);

      const tasks = await isolatedDb.query<{
        task_id: string;
        task_type: string;
        status: string;
        config_json: string;
      }>("SELECT task_id, task_type, status, config_json FROM ce_tasks");
      expect(tasks).toEqual([]);
    });
  });

  it("keeps Admin approval successful when automatic continuation cannot read governance rules", async () => {
    const createAuthorizedTask = vi.fn();
    const insightTaskService = { create: createAuthorizedTask } as unknown as InsightTaskService;
    const unavailableRuleProvider = {
      read: vi.fn().mockRejectedValue(new Error("You have no right to access this object because of bucket acl.")),
    } as unknown as GovernanceRuleProvider;
    await withDbInsightServer(async ({ baseUrl: isolatedBaseUrl }) => {
      const failureItem = await realFailureFixtureItem();
      await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/failure-tasks/upsert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: [failureItem] }),
      });
      const action = await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/governance/actions", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": "governance-rule-read-failure" },
        body: JSON.stringify({
          ...improvementBody("规则读取失败仍应完成审批"),
          ownerUserId: "dev_local",
          sourceOwnerUserId: "dev_local",
          sourceRuleId: "tool.utoo-proxy.unsupported",
          actionType: "DIRECT_EVOLUTION",
          rootCauseSummary: "测试审批后的可选自动续接失败。",
          suggestedAction: "审批必须成功，自动续接降级为稍后重试。",
        }),
      });

      const approved = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/admin/improvements/${Number(action.body.improvementId)}/review`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "admin-1" },
          body: JSON.stringify({ decision: "APPROVE", version: action.body.version }),
        },
      );

      expect(approved.response.status).toBe(200);
      expect(approved.body).toEqual(expect.objectContaining({
        improvementId: action.body.improvementId,
        status: "ACTIVE",
        adminReviewStatus: "APPROVED",
      }));
      expect(createAuthorizedTask).not.toHaveBeenCalled();
      expect(consoleWarn).toHaveBeenCalledWith(expect.stringContaining(
        `Insight approved auto-repair continuation skipped improvement=${Number(action.body.improvementId)}`,
      ));
    }, null, { insightTaskService, ruleProvider: unavailableRuleProvider });
  });

  it("automatically starts an approved Action after one scoped Owner grant", async () => {
    const createAuthorizedTask = vi.fn();
    const insightTaskService = { create: createAuthorizedTask } as unknown as InsightTaskService;
    const sendImprovementNotification = vi.fn().mockResolvedValue({ processQueryKey: "auto-repair-process" });
    const dingTalkSender = {
      enabled: true,
      sendImprovementNotification,
    } as unknown as DingTalkSender;
    const persistentBotId = "persistent-test-bot";
    await withDbInsightServer(async ({
      baseUrl: isolatedBaseUrl,
      db: isolatedDb,
      autoRepairRepo,
      ruleProvider,
    }) => {
      const improvementRepo = new InsightImprovementRepository(isolatedDb);
      createAuthorizedTask.mockImplementation(async (
        input: Parameters<InsightTaskService["create"]>[0],
      ) => {
        const evolveTaskId = `EV-AUTO-${String(input.improvementId)}`;
        await improvementRepo.linkEvolveTask({
          improvementId: Number(input.improvementId),
          ownerUserId: String(input.userId),
          evolveTaskId,
          requestId: input.idempotencyKey,
          createdBy: input.createdByOverride ?? "insight-auto-repair",
        });
        return { created: true } as Awaited<ReturnType<InsightTaskService["create"]>>;
      });
      const failureItem = await realFailureFixtureItem();
      await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/failure-tasks/upsert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: [failureItem] }),
      });

      const first = await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/governance/actions", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": "governance-auto-first" },
        body: JSON.stringify({
          ...improvementBody("首次确定性自动修复"),
          ownerUserId: "dev_local",
          sourceOwnerUserId: "dev_local",
          sourceRuleId: "tool.utoo-proxy.unsupported",
          actionType: "DIRECT_EVOLUTION",
          assignmentReason: "规则确定且范围固定",
          rootCauseSummary: "错误使用 UTOO_PROXY",
          suggestedAction: "更新 tools.md。",
        }),
      });
      expect(first.response.status).toBe(201);
      const approvedFirst = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/admin/improvements/${Number(first.body.improvementId)}/review`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "admin-1" },
          body: JSON.stringify({ decision: "APPROVE", version: first.body.version }),
        },
      );
      expect(approvedFirst.response.status).toBe(200);
      expect(sendImprovementNotification).toHaveBeenCalledTimes(1);

      const rule = await readAutoRepairRule(
        ruleProvider,
        "tool.utoo-proxy.unsupported",
        "DIRECT_EVOLUTION",
      );
      expect(rule).not.toBeNull();
      const grant = await autoRepairRepo.grant({
        ownerUserId: "dev_local",
        botId: persistentBotId,
        rule: rule!,
        sourceImprovementId: Number(first.body.improvementId),
        grantedBy: "dev_local",
      });

      const second = await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/governance/actions", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": "governance-auto-second" },
        body: JSON.stringify({
          ...improvementBody("后续同类问题自动推进"),
          ownerUserId: "dev_local",
          sourceOwnerUserId: "dev_local",
          sourceRuleId: "tool.utoo-proxy.unsupported",
          actionType: "DIRECT_EVOLUTION",
          assignmentReason: "命中已授权的自动修复范围",
          rootCauseSummary: "再次错误使用 UTOO_PROXY",
          suggestedAction: "沿用已授权范围更新 tools.md。",
        }),
      });
      expect(second.response.status).toBe(201);
      expect(second.body).toEqual(expect.objectContaining({
        status: "PENDING_ADMIN",
        sourceType: "ADMIN_RULE_DIRECT_EVOLUTION",
      }));
      expect(createAuthorizedTask).not.toHaveBeenCalled();

      const approvedSecond = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/admin/improvements/${Number(second.body.improvementId)}/review`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "admin-1" },
          body: JSON.stringify({ decision: "APPROVE", version: second.body.version }),
        },
      );
      expect(approvedSecond.response.status).toBe(200);
      expect(approvedSecond.body).toEqual(expect.objectContaining({
        status: "IN_PROGRESS",
        latestEvolveTaskId: `EV-AUTO-${String(second.body.improvementId)}`,
      }));
      expect(createAuthorizedTask).toHaveBeenCalledTimes(1);
      expect(createAuthorizedTask).toHaveBeenCalledWith(expect.objectContaining({
        actorUserId: null,
        authorizationGrantId: grant.grantId,
        userId: "dev_local",
        botId: persistentBotId,
        crossBotConfirmed: true,
        createdByOverride: "insight-auto-repair",
      }));
      expect(sendImprovementNotification).toHaveBeenCalledTimes(1);

      const grants = await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/auto-repair-grants", {
        headers: ownerHeaders(),
      });
      expect(grants.response.status).toBe(200);
      expect(grants.body.items).toEqual([
        expect.objectContaining({ grantId: grant.grantId, status: "ACTIVE" }),
      ]);
      const revoked = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/auto-repair-grants/${grant.grantId}`,
        {
          method: "DELETE",
          headers: ownerHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ version: grant.version }),
        },
      );
      expect(revoked.response.status).toBe(200);
      expect(revoked.body).toEqual(expect.objectContaining({ status: "REVOKED" }));

      const third = await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/governance/actions", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": "governance-auto-third" },
        body: JSON.stringify({
          ...improvementBody("撤销后不再自动执行"),
          ownerUserId: "dev_local",
          sourceOwnerUserId: "dev_local",
          sourceRuleId: "tool.utoo-proxy.unsupported",
          actionType: "DIRECT_EVOLUTION",
          assignmentReason: "Admin 已批准但 Owner 已撤销授权",
          rootCauseSummary: "第三次错误使用 UTOO_PROXY",
          suggestedAction: "等待 Owner 重新授权。",
        }),
      });
      expect(third.response.status).toBe(201);
      expect(third.body).toEqual(expect.objectContaining({ status: "PENDING_ADMIN" }));
      const approvedThird = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/admin/improvements/${Number(third.body.improvementId)}/review`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "admin-1" },
          body: JSON.stringify({ decision: "APPROVE", version: third.body.version }),
        },
      );
      expect(approvedThird.response.status).toBe(200);
      expect(createAuthorizedTask).toHaveBeenCalledTimes(1);
      expect(sendImprovementNotification).toHaveBeenCalledTimes(2);
    }, dingTalkSender, { insightTaskService });
  });

  it("keeps Admin-rejected governance Actions out of the Owner worklist", async () => {
    await withDbInsightServer(async ({ baseUrl: isolatedBaseUrl }) => {
      const failureItem = await realFailureFixtureItem();
      await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/failure-tasks/upsert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: [failureItem] }),
      });
      const action = await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/governance/actions", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": "governance-admin-reject-1" },
        body: JSON.stringify({
          ...improvementBody("预期业务失败无需派发"),
          ownerUserId: "dev_local",
          sourceOwnerUserId: "dev_local",
          sourceRuleId: "business.expected-failure",
          actionType: "ASSIGN_OWNER",
          assignmentReason: "需要 Admin 判断是否属于业务预期",
          rootCauseSummary: "预期业务失败被误识别为治理 Action",
          suggestedAction: "确认后驳回本次治理判断。",
        }),
      });
      const missingReason = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/admin/improvements/${Number(action.body.improvementId)}/review`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "admin-1" },
          body: JSON.stringify({ decision: "REJECT", version: action.body.version }),
        },
      );
      expect(missingReason.response.status).toBe(400);
      expect(String(missingReason.body.message)).toContain("必须填写理由");

      const reviewed = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/admin/improvements/${Number(action.body.improvementId)}/review`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "admin-1" },
          body: JSON.stringify({ decision: "REJECT", comment: "属于业务预期", version: action.body.version }),
        },
      );
      expect(reviewed.response.status).toBe(200);
      expect(reviewed.body).toEqual(expect.objectContaining({
        status: "ARCHIVED",
        sourceType: "REJECTED_RULE_ASSIGN_OWNER",
        adminReviewStatus: "REJECTED",
        adminReviewedBy: "admin-1",
        adminReviewComment: "属于业务预期",
        rejectComment: "属于业务预期",
      }));

      const rejectionLabels = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/internal/governance/rejections?days=30",
      );
      expect(rejectionLabels.response.status).toBe(200);
      expect(rejectionLabels.body.items).toEqual([
        expect.objectContaining({
          improvementId: Number(action.body.improvementId),
          rejectComment: "属于业务预期",
          adminReviewedBy: "admin-1",
        }),
      ]);

      const ownerItems = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/improvements",
        { headers: ownerHeaders() },
      );
      expect(ownerItems.response.status).toBe(200);
      expect(ownerItems.body.items).toEqual([]);
      expect(ownerItems.body.statusCounts).toEqual(expect.objectContaining({ archived: 0 }));
    });
  });

  it("allows an admin to reopen a rejected governance Action for a one-time repair", async () => {
    await withDbInsightServer(async ({ baseUrl: isolatedBaseUrl }) => {
      const failureItem = await realFailureFixtureItem();
      await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/failure-tasks/upsert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: [failureItem] }),
      });
      const action = await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/governance/actions", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": "governance-admin-reopen-1" },
        body: JSON.stringify({
          ...improvementBody("可恢复的治理项"),
          ownerUserId: "dev_local",
          sourceOwnerUserId: "dev_local",
          sourceRuleId: "reopen.test-rule",
          actionType: "ASSIGN_OWNER",
          assignmentReason: "需要管理员验证历史项恢复流程",
          rootCauseSummary: "历史治理项需要重新处理",
          suggestedAction: "修复后再次运行典型任务。",
        }),
      });
      expect(action.response.status).toBe(201);
      const improvementId = Number(action.body.improvementId);

      const rejected = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/admin/improvements/${improvementId}/review`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "admin-1" },
          body: JSON.stringify({ decision: "REJECT", comment: "暂不处理，先验证恢复流程", version: action.body.version }),
        },
      );
      expect(rejected.response.status).toBe(200);
      expect(rejected.body).toEqual(expect.objectContaining({
        status: "ARCHIVED",
        sourceType: "REJECTED_RULE_ASSIGN_OWNER",
        adminReviewStatus: "REJECTED",
      }));

      const reopened = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/admin/improvements/${improvementId}/reopen`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "admin-1" },
          body: JSON.stringify({ reason: "用户未响应，管理员代用户验证一次修复", version: rejected.body.version }),
        },
      );
      expect(reopened.response.status).toBe(200);
      expect(reopened.body).toEqual(expect.objectContaining({
        status: "ACTIVE",
        sourceType: "ADMIN_RULE_ASSIGN_OWNER",
        adminReviewStatus: "APPROVED",
        actionType: "ASSIGN_OWNER",
        version: rejected.body.version + 1,
      }));
      expect(String(reopened.body.userGuidance)).toContain("管理员恢复处理");

      const adminList = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/admin/improvements?status=ACTIVE&ownerUserId=dev_local",
        { headers: { "X-User-Id": "admin-1" } },
      );
      expect(adminList.response.status).toBe(200);
      expect(adminList.body.items).toEqual([
        expect.objectContaining({ improvementId, status: "ACTIVE", adminReviewStatus: "APPROVED" }),
      ]);
      expect(adminList.body.statusCounts).toEqual(expect.objectContaining({ active: 1, archived: 0 }));
    });
  });

  it("reuses rejected improvements as a recent governance suppression source", async () => {
    await withDbInsightServer(async ({ baseUrl: isolatedBaseUrl }) => {
      const failureItem = await realFailureFixtureItem();
      await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/failure-tasks/upsert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: [failureItem] }),
      });
      const action = await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/governance/actions", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": "governance-reject-1" },
        body: JSON.stringify({
          ...improvementBody("用户可驳回的治理项"),
          ownerUserId: "dev_local",
          sourceOwnerUserId: "dev_local",
          sourceRuleId: "tool.odps.use-dpagent-mcp",
          actionType: "ASSIGN_OWNER",
          assignmentReason: "需要 Owner 判断业务影响",
          rootCauseSummary: "ODPS 工具调用方式异常",
          suggestedAction: "确认是否需要调整 tools.md。",
        }),
      });
      const improvementId = Number(action.body.improvementId);
      const approved = await jsonRequestAt(isolatedBaseUrl, `/api/insight/v1/admin/improvements/${improvementId}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-User-Id": "admin-1" },
        body: JSON.stringify({ decision: "APPROVE", version: action.body.version }),
      });
      const rejected = await jsonRequestAt(isolatedBaseUrl, `/api/insight/v1/improvements/${improvementId}/reject`, {
        method: "POST",
        headers: ownerHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ reasonCode: "EXPECTED_BUSINESS_FAILURE", comment: "这是业务预期关单。", version: approved.body.version }),
      });
      expect(rejected.response.status).toBe(200);
      expect(rejected.body).toEqual(expect.objectContaining({ status: "ARCHIVED", rejectReasonCode: "EXPECTED_BUSINESS_FAILURE" }));

      const recent = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/internal/governance/rejections?days=15&botId=${BOT_ID}&sourceRuleId=tool.odps.use-dpagent-mcp`,
      );
      expect(recent.response.status).toBe(200);
      expect(recent.body.items).toHaveLength(1);
      expect(recent.body.items[0]).toEqual(expect.objectContaining({
        improvementId,
        rejectComment: "这是业务预期关单。",
      }));
    });
  });

  it("lists governance Actions for duplicate suppression and resolved-item rechecks without authentication", async () => {
    const lockedAuthorizer = new InsightAgentAuthorizer({
      clients: {
        "governance-agent": {
          secret: "test-only-governance-secret",
          scopes: ["action.write"],
        },
      },
      allowLocalUnsigned: false,
    });
    await withDbInsightServer(async ({ baseUrl: isolatedBaseUrl, db: isolatedDb }) => {
      const failureItem = await realFailureFixtureItem();
      await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/failure-tasks/upsert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: [failureItem] }),
      });

      const createAction = (key: string, title: string, sourceRuleId: string) => jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/internal/governance/actions",
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "Idempotency-Key": key },
          body: JSON.stringify({
            ...improvementBody(title),
            ownerUserId: "dev_local",
            sourceOwnerUserId: "dev_local",
            sourceRuleId,
            actionType: "ASSIGN_OWNER",
            assignmentReason: "由当前 Bot Owner 处理",
            rootCauseSummary: `${title}的规范化根因`,
            suggestedAction: "修复后进入 Agent 验收。",
          }),
        },
      );

      const pending = await createAction(
        "governance-query-pending",
        "待审批治理项",
        "query.pending",
      );
      const rejected = await createAction(
        "governance-query-rejected",
        "被驳回治理项",
        "query.rejected",
      );
      const rejectedReview = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/admin/improvements/${Number(rejected.body.improvementId)}/review`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "admin-1" },
          body: JSON.stringify({ decision: "REJECT", comment: "同类问题近期不再打扰", version: rejected.body.version }),
        },
      );
      expect(rejectedReview.response.status).toBe(200);

      const resolved = await createAction(
        "governance-query-resolved",
        "已完成治理项",
        "query.resolved",
      );
      const approved = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/admin/improvements/${Number(resolved.body.improvementId)}/review`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "admin-1" },
          body: JSON.stringify({ decision: "APPROVE", version: resolved.body.version }),
        },
      );
      const handled = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/improvements/${Number(resolved.body.improvementId)}/handled`,
        {
          method: "POST",
          headers: ownerHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ version: approved.body.version }),
        },
      );
      await isolatedDb.exec(
        "UPDATE insight_improvement_item SET user_guidance = user_guidance || ? WHERE id = ?",
        ["\n\n[用户已处理]\n时间：2026-08-20T00:00:00+08:00", Number(resolved.body.improvementId)],
      );
      const verification = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/internal/governance/verification-results",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            improvementId: Number(resolved.body.improvementId),
            version: handled.body.version,
            outcome: "DISAPPEARED",
            newSessionCount: 2,
          }),
        },
      );
      expect(verification.response.status).toBe(200);

      const all = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/internal/governance/actions?ownerUserId=dev_local&limit=10",
      );
      expect(all.response.status).toBe(200);
      expect(all.body.total).toBe(3);
      expect(all.body.items).toEqual(expect.arrayContaining([
        expect.objectContaining({ improvementId: pending.body.improvementId, status: "PENDING_ADMIN" }),
        expect.objectContaining({ improvementId: rejected.body.improvementId, adminReviewStatus: "REJECTED" }),
        expect.objectContaining({
          improvementId: resolved.body.improvementId,
          status: "RESOLVED",
          verificationStatus: "VERIFIED",
          resolvedSource: "AUTO_VERIFIED",
        }),
      ]));

      const since = encodeURIComponent(new Date(Date.now() - 60_000).toISOString());
      const rejectedItems = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/internal/governance/actions?ownerUserId=dev_local&botId=${BOT_ID}&adminReviewStatus=REJECTED&sourceRuleId=query.rejected&since=2026-08-01T00:00:00+08:00&limit=10`,
      );
      expect(rejectedItems.response.status).toBe(200);
      expect(rejectedItems.body.total).toBe(1);
      expect(rejectedItems.body.items).toEqual([
        expect.objectContaining({
          improvementId: rejected.body.improvementId,
          sourceRuleId: "query.rejected",
          adminReviewReason: "同类问题近期不再打扰",
        }),
      ]);

      const resolvedItems = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/internal/governance/actions?status=RESOLVED,VERIFIED,AUTO_VERIFIED&since=${since}&limit=50`,
      );
      expect(resolvedItems.response.status).toBe(200);
      expect(resolvedItems.body.total).toBe(1);
      expect(resolvedItems.body.items).toEqual([
        expect.objectContaining({
          improvementId: resolved.body.improvementId,
          status: "RESOLVED",
          resolvedSource: "AUTO_VERIFIED",
          resolvedAt: expect.any(String),
        }),
      ]);

      const recurrence = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/internal/governance/verification-results",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            improvementId: Number(resolved.body.improvementId),
            version: (verification.body.improvement as Record<string, unknown>).version,
            outcome: "STILL_PRESENT",
            newSessionCount: 1,
            lastRecurrenceAt: "2026-08-19T12:00:00+08:00",
          }),
        },
      );
      expect(recurrence.response.status).toBe(200);
      expect(recurrence.body.improvement).toEqual(expect.objectContaining({
        improvementId: resolved.body.improvementId,
        status: "ACTIVE",
        verificationStatus: "STILL_PRESENT",
        resolvedSource: null,
      }));

      const projected = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/internal/governance/actions?ownerUserId=dev_local&fields=improvementId,title,status&limit=1&offset=1",
      );
      expect(projected.response.status).toBe(200);
      expect(projected.body.total).toBe(3);
      expect(projected.body.items).toHaveLength(1);
      expect(Object.keys((projected.body.items as Array<Record<string, unknown>>)[0])).toEqual([
        "improvementId",
        "title",
        "status",
      ]);

      const empty = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/internal/governance/actions?sourceRuleId=not-found",
      );
      expect(empty.response.status).toBe(200);
      expect(empty.body).toEqual({ total: 0, items: [] });

      const invalid = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/internal/governance/actions?status=FINISHED&fields=secret",
      );
      expect(invalid.response.status).toBe(400);
    }, null, { agentAuthorizer: lockedAuthorizer });
  });

  it("exposes rule evolution proposals only to Admin", async () => {
    const proposal = {
      proposalId: 7,
      sourceRuleId: "tool.utoo-proxy.unsupported",
      fromRuleVersion: 1,
      proposedRuleVersion: 2,
      status: "PENDING",
      version: 1,
    };
    const list = vi.fn().mockResolvedValue([proposal]);
    const review = vi.fn().mockResolvedValue({ ...proposal, status: "APPROVED", version: 2 });
    const ruleEvolutionService = {
      list,
      review,
      maybeCreateFromVerification: vi.fn(),
    } as unknown as RuleEvolutionService;

    await withDbInsightServer(async ({ baseUrl: isolatedBaseUrl }) => {
      const forbidden = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/admin/governance/rule-evolution",
        { headers: { "X-User-Id": "dev_local" } },
      );
      expect(forbidden.response.status).toBe(401);

      const listed = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/admin/governance/rule-evolution?status=PENDING",
        { headers: { "X-User-Id": "admin-1" } },
      );
      expect(listed.response.status).toBe(200);
      expect(listed.body.items).toEqual([proposal]);
      expect(list).toHaveBeenCalledWith("PENDING");

      const approved = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/admin/governance/rule-evolution/7/review",
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "admin-1" },
          body: JSON.stringify({ decision: "APPROVE", version: 1 }),
        },
      );
      expect(approved.response.status).toBe(200);
      expect(approved.body).toEqual(expect.objectContaining({ status: "APPROVED" }));
      expect(review).toHaveBeenCalledWith("admin-1", 7, { decision: "APPROVE", version: 1 });
    }, null, { ruleEvolutionService });
  });

  it("marks an approved DIRECT_EVOLUTION Action handled after Evolve applies the fix", async () => {
    const lockedAuthorizer = new InsightAgentAuthorizer({ clients: {}, allowLocalUnsigned: false });
    await withDbInsightServer(async ({ baseUrl: isolatedBaseUrl, db: isolatedDb }) => {
      const failureItem = await realFailureFixtureItem();
      await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/failure-tasks/upsert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: [failureItem] }),
      });

      const createAction = (key: string, actionType: "DIRECT_EVOLUTION" | "ASSIGN_OWNER") => jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/internal/governance/actions",
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "Idempotency-Key": key },
          body: JSON.stringify({
            ...improvementBody(`${actionType} mark handled`),
            ownerUserId: "dev_local",
            sourceOwnerUserId: "dev_local",
            sourceRuleId: `mark-handled.${actionType.toLowerCase()}`,
            actionType,
            rootCauseSummary: "验证 Evolve 完成后的 handledAt 回写。",
            suggestedAction: "应用修复后进入自动验收。",
          }),
        },
      );

      const direct = await createAction("mark-handled-direct", "DIRECT_EVOLUTION");
      const directId = Number(direct.body.improvementId);
      const premature = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/internal/governance/actions/${directId}/mark-handled`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ handledAt: "2026-08-19T15:30:00+08:00" }),
        },
      );
      expect(premature.response.status).toBe(409);

      const approvedDirect = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/admin/improvements/${directId}/review`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "admin-1" },
          body: JSON.stringify({ decision: "APPROVE", version: direct.body.version }),
        },
      );
      expect(approvedDirect.response.status).toBe(200);
      await isolatedDb.exec(
        "UPDATE insight_improvement_item SET status = 'IN_PROGRESS' WHERE id = ?",
        [directId],
      );

      const invalidTime = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/internal/governance/actions/${directId}/mark-handled`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ handledAt: "2026-08-19 15:30:00" }),
        },
      );
      expect(invalidTime.response.status).toBe(400);

      const handled = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/internal/governance/actions/${directId}/mark-handled`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            handledAt: "2026-08-19T15:30:00+08:00",
            appliedEvolveTaskId: "EVOLVE-MARK-HANDLED-1",
          }),
        },
      );
      expect(handled.response.status).toBe(200);
      expect(handled.body).toEqual(expect.objectContaining({
        improvementId: directId,
        status: "IN_PROGRESS",
        actionType: "DIRECT_EVOLUTION",
        handledAt: "2026-08-19T07:30:00.000Z",
        appliedEvolveTaskId: "EVOLVE-MARK-HANDLED-1",
        verificationStatus: "PENDING",
      }));

      const candidates = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/internal/governance/verification-candidates?limit=100",
      );
      expect(candidates.response.status).toBe(200);
      expect(candidates.body.items).toEqual(expect.arrayContaining([
        expect.objectContaining({ improvementId: directId, handledAt: "2026-08-19T07:30:00.000Z" }),
      ]));

      const manual = await createAction("mark-handled-manual", "ASSIGN_OWNER");
      const manualId = Number(manual.body.improvementId);
      await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/admin/improvements/${manualId}/review`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "admin-1" },
          body: JSON.stringify({ decision: "APPROVE", version: manual.body.version }),
        },
      );
      await isolatedDb.exec(
        "UPDATE insight_improvement_item SET status = 'IN_PROGRESS' WHERE id = ?",
        [manualId],
      );
      const manualHandled = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/internal/governance/actions/${manualId}/mark-handled`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ handledAt: "2026-08-19T15:35:00+08:00" }),
        },
      );
      expect(manualHandled.response.status).toBe(409);
    }, null, { agentAuthorizer: lockedAuthorizer });
  });

  it("allows STILL_PRESENT to fall back from DIRECT_EVOLUTION to ASSIGN_OWNER", async () => {
    await withDbInsightServer(async ({ baseUrl: isolatedBaseUrl, db: isolatedDb }) => {
      const failureItem = await realFailureFixtureItem();
      await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/failure-tasks/upsert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: [failureItem] }),
      });

      const prepareDirect = async (key: string) => {
        const action = await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/governance/actions", {
          method: "POST",
          headers: { "Content-Type": "application/json", "Idempotency-Key": key },
          body: JSON.stringify({
            ...improvementBody(key),
            ownerUserId: "dev_local",
            sourceOwnerUserId: "dev_local",
            sourceRuleId: `fallback.${key}`,
            actionType: "DIRECT_EVOLUTION",
            rootCauseSummary: "自动修复后问题仍然复现。",
            suggestedAction: "必要时回退为手动修复。",
          }),
        });
        const improvementId = Number(action.body.improvementId);
        await jsonRequestAt(
          isolatedBaseUrl,
          `/api/insight/v1/admin/improvements/${improvementId}/review`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-User-Id": "admin-1" },
            body: JSON.stringify({ decision: "APPROVE", version: action.body.version }),
          },
        );
        await isolatedDb.exec(
          "UPDATE insight_improvement_item SET status = 'IN_PROGRESS' WHERE id = ?",
          [improvementId],
        );
        const handled = await jsonRequestAt(
          isolatedBaseUrl,
          `/api/insight/v1/internal/governance/actions/${improvementId}/mark-handled`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              handledAt: "2026-08-19T16:00:00+08:00",
              appliedEvolveTaskId: `EV-${key}`,
            }),
          },
        );
        expect(handled.response.status).toBe(200);
        return handled.body;
      };

      const automatic = await prepareDirect("fallback-to-manual");
      const invalidOverride = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/internal/governance/verification-results",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            improvementId: automatic.improvementId,
            version: automatic.version,
            outcome: "STILL_PRESENT",
            newSessionCount: 2,
            overrideActionType: "DIRECT_EVOLUTION",
          }),
        },
      );
      expect(invalidOverride.response.status).toBe(400);

      const fallback = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/internal/governance/verification-results",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            improvementId: automatic.improvementId,
            version: automatic.version,
            outcome: "STILL_PRESENT",
            newSessionCount: 2,
            lastRecurrenceAt: "2026-08-19T16:20:00+08:00",
            overrideActionType: "ASSIGN_OWNER",
          }),
        },
      );
      expect(fallback.response.status).toBe(200);
      expect(fallback.body.improvement).toEqual(expect.objectContaining({
        improvementId: automatic.improvementId,
        status: "ACTIVE",
        actionType: "ASSIGN_OWNER",
        sourceType: "ADMIN_RULE_ASSIGN_OWNER",
        verificationStatus: "STILL_PRESENT",
      }));
      const afterFallbackCandidates = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/internal/governance/verification-candidates?limit=100",
      );
      expect(afterFallbackCandidates.response.status).toBe(200);
      expect(afterFallbackCandidates.body.items).not.toEqual(expect.arrayContaining([
        expect.objectContaining({ improvementId: automatic.improvementId }),
      ]));

      const manualStarted = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/improvements/${automatic.improvementId}`,
        {
          method: "PATCH",
          headers: ownerHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ status: "IN_PROGRESS", version: fallback.body.improvement.version }),
        },
      );
      expect(manualStarted.response.status).toBe(200);
      const manualCompleted = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/improvements/${automatic.improvementId}/handled`,
        {
          method: "POST",
          headers: ownerHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ version: manualStarted.body.version }),
        },
      );
      expect(manualCompleted.response.status).toBe(200);
      expect(manualCompleted.body).toEqual(expect.objectContaining({
        status: "IN_PROGRESS",
        actionType: "ASSIGN_OWNER",
        verificationStatus: "PENDING",
      }));

      const unchanged = await prepareDirect("fallback-compatible");
      const stillAutomatic = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/internal/governance/verification-results",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            improvementId: unchanged.improvementId,
            version: unchanged.version,
            outcome: "STILL_PRESENT",
            newSessionCount: 1,
          }),
        },
      );
      expect(stillAutomatic.response.status).toBe(200);
      expect(stillAutomatic.body.improvement).toEqual(expect.objectContaining({
        actionType: "DIRECT_EVOLUTION",
        sourceType: "ADMIN_RULE_DIRECT_EVOLUTION",
      }));
    });
  });

  it("allows an admin to force an in-progress improvement through verification", async () => {
    await withDbInsightServer(async ({ baseUrl: isolatedBaseUrl, db: isolatedDb }) => {
      const failureItem = await realFailureFixtureItem();
      const upsert = await jsonRequestAt(isolatedBaseUrl, "/api/insight/v1/internal/failure-tasks/upsert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: [failureItem] }),
      });
      expect(upsert.response.status).toBe(201);
      const created = await jsonRequestAt(
        isolatedBaseUrl,
        "/api/insight/v1/improvements",
        {
          method: "POST",
          headers: ownerHeaders({
            "Content-Type": "application/json",
            "Idempotency-Key": "force-verification-create",
          }),
          body: JSON.stringify(improvementBody("强制验收测试")),
        },
      );
      expect(created.response.status).toBe(201);
      const improvementId = Number(created.body.improvementId);
      await isolatedDb.exec(
        `UPDATE insight_improvement_item
            SET status = 'IN_PROGRESS',
                user_guidance = user_guidance || ?
          WHERE id = ?`,
        ["\n\n[用户已处理]\n时间：2026-08-30T00:00:00.000Z", improvementId],
      );
      const detail = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/admin/improvements/${improvementId}`,
        { headers: { "X-User-Id": "admin-1" } },
      );
      expect(detail.response.status).toBe(200);

      const forced = await jsonRequestAt(
        isolatedBaseUrl,
        `/api/insight/v1/admin/improvements/${improvementId}/force-resolved`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-User-Id": "admin-1" },
          body: JSON.stringify({
            version: detail.body.version,
            newSessionCount: 1,
            reason: "预发联调，验证强制验收状态机",
          }),
        },
      );
      expect(forced.response.status).toBe(200);
      expect(forced.body.improvement).toEqual(expect.objectContaining({
        improvementId,
        status: "RESOLVED",
        verificationStatus: "VERIFIED",
        resolvedSource: "FORCE_VERIFIED",
      }));
    });
  });

});
