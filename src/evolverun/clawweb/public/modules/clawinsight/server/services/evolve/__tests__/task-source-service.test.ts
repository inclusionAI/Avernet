import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Database from "better-sqlite3";
import { runMigrations, SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import { EvolveTaskSourceRepository } from "../../../repositories/evolve-task-source-repository.js";
import type { ImprovementDetail, SessionEvidence } from "../../insight/contracts.js";
import { TaskSourceService } from "../task-source-service.js";

const detail: ImprovementDetail = {
  improvementId: 123,
  ownerUserId: "specialist-1",
  botOwnerUserId: "owner-1",
  botId: "bot-1",
  title: "修复工具失败后不降级",
  userGuidance: "保留可执行降级路径",
  sourceType: "USER_SELECTED",
  sourceRuleId: null,
  evidenceCount: 1,
  sessionCount: 1,
  dataStartTime: "2026-08-10T00:00:00Z",
  dataEndTime: "2026-08-11T00:00:00Z",
  dataAsOf: "2026-08-11T02:00:00Z",
  batchId: "batch-1",
  status: "ACTIVE",
  latestEvolveTaskId: null,
  latestEvolveTaskStatus: null,
  appliedEvolveTaskId: null,
  appliedBy: null,
  appliedAt: null,
  version: 4,
  createdBy: "specialist-1",
  gmtCreate: 1_786_412_400,
  gmtModified: 1_786_412_400,
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
  evolveLinks: [],
};

const evidence: SessionEvidence = {
  schema_version: "session-evidence/v1",
  batch_id: "batch-1",
  dt: "20260811",
  user_id: "owner-1",
  bot_id: "bot-1",
  session_id: "session-1",
  session: {},
  messages: [{
    message_index: 0,
    role: "user",
    timestamp: 1,
    visibility: "visible",
    content: "查日志",
    raw: {},
  }],
  tasks: [{
    task_index: 0,
    task_description: "验证工具执行环境",
    message_range: [0, 1],
    is_complete: 0,
    reasoning: "工具失败后结束",
    task_failure_class: "TOOL_FAILURE",
  }],
  judge_meta: {},
  generated_at: "2026-08-11T02:00:00Z",
};

describe("TaskSourceService frozen SourceRef integrity", () => {
  let db: SqliteDatabase;
  let repo: EvolveTaskSourceRepository;

  beforeEach(async () => {
    db = new SqliteDatabase(new Database(":memory:"));
    await runMigrations(db, "sqlite");
    repo = new EvolveTaskSourceRepository(db);
  });

  afterEach(async () => {
    await db.close();
  });

  it("accepts the evidence reference list whose digest was frozen", async () => {
    const evidenceReader = vi.fn(async () => evidence);
    const service = new TaskSourceService(repo, evidenceReader);
    await service.freezeInsight("EV-1", detail);

    const descriptor = await service.resolvePlanSource("EV-1");

    expect(descriptor.delivery.content.cases).toHaveLength(1);
    expect(evidenceReader).toHaveBeenCalledTimes(1);
    expect((await repo.findByTaskId("EV-1"))?.status).toBe("ready");
  });

  it("rebuilds a v1 derived Source from the unchanged frozen SourceRef as v2", async () => {
    const evidenceReader = vi.fn(async () => evidence);
    const service = new TaskSourceService(repo, evidenceReader);
    await service.freezeInsight("EV-MIGRATE", detail);
    await db.exec(
      `UPDATE ce_task_sources
          SET source_schema_version = 'plan-source/v1', source_digest = ?
        WHERE task_id = ?`,
      ["sha256:" + "0".repeat(64), "EV-MIGRATE"],
    );

    const descriptor = await service.resolvePlanSource("EV-MIGRATE");
    const row = await repo.findByTaskId("EV-MIGRATE");

    expect(descriptor.schemaVersion).toBe("plan-source/v2");
    expect(row).toEqual(expect.objectContaining({
      source_schema_version: "plan-source/v2",
      source_digest: descriptor.digest,
      status: "ready",
    }));
  });

  it("freezes the execution target separately from the Evidence source", async () => {
    const evidenceReader = vi.fn(async () => evidence);
    const service = new TaskSourceService(repo, evidenceReader);
    await service.freezeInsight("EV-CROSS", detail, {
      ownerUserId: "specialist-1",
      botId: "target-bot-1",
      selectedBy: "specialist-1",
      crossBotConfirmed: true,
    });

    const row = await repo.findByTaskId("EV-CROSS");
    const frozen = JSON.parse(String(row?.source_ref_json)) as {
      sourceRefVersion: string;
      improvement: { botOwnerUserId: string; botId: string };
      target: {
        ownerUserId: string;
        botId: string;
        relationship: string;
        selectedBy: string;
        crossBotConfirmed: boolean;
      };
      targetRefDigest: string;
    };
    expect(row?.adapter_version).toBe("insight-to-plan-source/v2");
    expect(frozen.sourceRefVersion).toBe("evolve-source-ref/v2");
    expect(frozen.improvement).toEqual(expect.objectContaining({ botOwnerUserId: "owner-1", botId: "bot-1" }));
    expect(frozen.target).toEqual(expect.objectContaining({
      ownerUserId: "specialist-1",
      botId: "target-bot-1",
      relationship: "cross_bot",
      selectedBy: "specialist-1",
      crossBotConfirmed: true,
    }));
    expect(frozen.targetRefDigest).toMatch(/^sha256:/);

    const descriptor = await service.resolvePlanSource("EV-CROSS");
    expect(descriptor.delivery.content.source).toEqual(expect.objectContaining({
      bot_owner_user_id: "owner-1",
      bot_id: "bot-1",
    }));
    expect(descriptor.delivery.content.planning_hints).toEqual({
      target_context: {
        relationship: "cross_bot",
        applicability_required: true,
        source_bot: { owner_user_id: "owner-1", bot_id: "bot-1" },
        execution_target: { owner_user_id: "specialist-1", bot_id: "target-bot-1" },
      },
    });
  });

  it("keeps a Repair-specific direction in the frozen source for the Plan Agent", async () => {
    const evidenceReader = vi.fn(async () => evidence);
    const service = new TaskSourceService(repo, evidenceReader);
    await service.freezeInsight("REPAIR-1", detail, undefined, undefined, "只修改测试 Bot 的配置模板");

    const row = await repo.findByTaskId("REPAIR-1");
    expect(row).not.toBeNull();
    const sourceRef = JSON.parse(String(row?.source_ref_json)) as { repairDirection?: string };
    expect(sourceRef.repairDirection).toBe("只修改测试 Bot 的配置模板");

    const resolved = await service.resolvePlanSource("REPAIR-1");
    expect(resolved.delivery.content.problem.user_guidance)
      .toContain("本次修复方向：只修改测试 Bot 的配置模板");
    expect((resolved.delivery.content.extensions as { insight: { repair_direction?: string } }).insight.repair_direction)
      .toBe("只修改测试 Bot 的配置模板");
  });

  it("rejects a DB-drifted evidence reference before reading Evidence", async () => {
    const evidenceReader = vi.fn(async () => evidence);
    const service = new TaskSourceService(repo, evidenceReader);
    await service.freezeInsight("EV-1", detail);
    const row = await repo.findByTaskId("EV-1");
    const drifted = JSON.parse(String(row?.source_ref_json)) as {
      evidence: Array<{ payloadEtag: string }>;
    };
    drifted.evidence[0].payloadEtag = "etag-tampered-before-first-resolve";
    await db.exec(
      "UPDATE ce_task_sources SET source_ref_json = ? WHERE task_id = ?",
      [JSON.stringify(drifted), "EV-1"],
    );

    await expect(service.findView("EV-1")).rejects.toEqual(expect.objectContaining({
      code: "SOURCE_SNAPSHOT_INVALID",
      stage: "freeze",
      retryable: false,
    }));
    await expect(service.resolvePlanSource("EV-1")).rejects.toEqual(expect.objectContaining({
      code: "SOURCE_SNAPSHOT_INVALID",
      stage: "freeze",
      retryable: false,
    }));
    expect(evidenceReader).not.toHaveBeenCalled();
    expect(await repo.findByTaskId("EV-1")).toEqual(expect.objectContaining({
      status: "failed",
      error_code: "SOURCE_SNAPSHOT_INVALID",
    }));
  });

  it("rejects a DB-drifted execution target before reading Evidence", async () => {
    const evidenceReader = vi.fn(async () => evidence);
    const service = new TaskSourceService(repo, evidenceReader);
    await service.freezeInsight("EV-TARGET", detail, {
      ownerUserId: "specialist-1",
      botId: "target-bot-1",
      selectedBy: "specialist-1",
      crossBotConfirmed: true,
    });
    const row = await repo.findByTaskId("EV-TARGET");
    const drifted = JSON.parse(String(row?.source_ref_json)) as {
      target: { botId: string };
    };
    drifted.target.botId = "tampered-target";
    await db.exec(
      "UPDATE ce_task_sources SET source_ref_json = ? WHERE task_id = ?",
      [JSON.stringify(drifted), "EV-TARGET"],
    );

    await expect(service.resolvePlanSource("EV-TARGET")).rejects.toEqual(expect.objectContaining({
      code: "SOURCE_SNAPSHOT_INVALID",
      stage: "freeze",
      retryable: false,
    }));
    expect(evidenceReader).not.toHaveBeenCalled();
  });
});
