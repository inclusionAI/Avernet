import assert from "node:assert/strict";
import test from "node:test";

import type {
  ImprovementDetail,
  SessionEvidence,
} from "../src/server/ports/insight.js";
import type {
  CreateFrozenTaskSourceInput,
  EvolveTaskSourceRow,
  TaskSourceRepositoryPort,
} from "../src/server/ports/task-source-repository.js";
import {
  buildInsightPlanSource,
  type InsightSourceRef,
} from "../src/server/services/evolve/adapters/insight-plan-source-adapter.js";
import {
  digestJson,
  digestPlanSource,
} from "../src/server/services/evolve/plan-source-contract.js";
import {
  TaskSourceError,
  TaskSourceService,
} from "../src/server/services/evolve/task-source-service.js";

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
  dataStartTime: "2026-08-10T00:00:00Z",
  dataEndTime: "2026-08-11T00:00:00Z",
  dataAsOf: "2026-08-11T02:00:00Z",
  batchId: "batch-1",
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
    payloadRef: "artifact://evidence/session-1.json",
    payloadEtag: "etag-1",
    payloadVersionId: "v1",
  }],
};

const evidence: SessionEvidence = {
  schema_version: "session-evidence/v1",
  batch_id: "batch-1",
  dt: "20260811",
  user_id: "owner-1",
  bot_id: "bot-1",
  session_id: "session-1",
  session: { started_at: "2026-08-11T01:00:00Z" },
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

function sourceRef(overrides: Partial<InsightSourceRef> = {}): InsightSourceRef {
  return {
    sourceRefVersion: "evolve-source-ref/v1",
    sourceType: "insight_improvement",
    improvement: {
      improvementId: detail.improvementId,
      version: detail.version,
      title: detail.title,
      userGuidance: detail.userGuidance,
      ownerUserId: detail.ownerUserId,
      botOwnerUserId: detail.botOwnerUserId,
      botId: detail.botId,
      sourceType: detail.sourceType,
      sourceRuleId: detail.sourceRuleId,
      dataStartTime: detail.dataStartTime,
      dataEndTime: detail.dataEndTime,
      dataAsOf: detail.dataAsOf,
      batchId: detail.batchId,
      createdBy: detail.createdBy,
      createdAt: "2026-08-11T02:00:00Z",
      updatedAt: "2026-08-11T02:00:00Z",
    },
    evidence: detail.evidence,
    evidenceRefsDigest: "sha256:frozen-reference-list",
    frozenAt: "2026-08-11T03:00:00Z",
    ...overrides,
  };
}

class MemoryTaskSourceRepository implements TaskSourceRepositoryPort {
  readonly rows = new Map<string, EvolveTaskSourceRow>();

  async createFrozen(input: CreateFrozenTaskSourceInput): Promise<EvolveTaskSourceRow> {
    const row: EvolveTaskSourceRow = {
      id: this.rows.size + 1,
      task_id: input.taskId,
      source_type: input.sourceType,
      source_id: input.sourceId,
      source_schema_version: input.sourceSchemaVersion,
      adapter_version: input.adapterVersion,
      source_ref_json: JSON.stringify(input.sourceRef),
      source_digest: null,
      status: "frozen",
      error_code: null,
      error_message: null,
      resolved_at: null,
      gmt_create: 1,
      gmt_modified: 1,
    };
    this.rows.set(input.taskId, row);
    return row;
  }

  async findByTaskId(taskId: string): Promise<EvolveTaskSourceRow | null> {
    return this.rows.get(taskId) ?? null;
  }

  async markResolving(taskId: string): Promise<void> {
    const row = this.required(taskId);
    row.status = "resolving";
    row.error_code = null;
    row.error_message = null;
  }

  async markReady(taskId: string, input: {
    digest: string;
    sourceSchemaVersion: string;
    adapterVersion: string | null;
  }): Promise<void> {
    const row = this.required(taskId);
    row.status = "ready";
    row.source_digest = input.digest;
    row.source_schema_version = input.sourceSchemaVersion;
    row.adapter_version = input.adapterVersion;
    row.resolved_at = 2;
  }

  async markFailed(taskId: string, code: string, message: string): Promise<void> {
    const row = this.required(taskId);
    row.status = "failed";
    row.error_code = code;
    row.error_message = message;
  }

  private required(taskId: string): EvolveTaskSourceRow {
    const row = this.rows.get(taskId);
    if (!row) throw new Error(`missing row: ${taskId}`);
    return row;
  }
}

test("converts frozen Insight evidence into a deterministic Plan Source", () => {
  const first = buildInsightPlanSource(sourceRef(), [evidence]);
  const second = buildInsightPlanSource(sourceRef(), [evidence]);

  assert.equal(digestPlanSource(first), digestPlanSource(second));
  assert.equal(first.schema_version, "plan-source/v2");
  assert.equal(first.source.producer, "avernet-insight-plan-source-adapter");
  assert.equal(first.cases[0]?.session_id, "session-1");
  assert.equal(first.cases[0]?.analysis?.evolution_failure_mode, "tool_execution_failure");
  assert.deepEqual(first.analysis.root_cause_clusters, []);
});

test("keeps evidence provenance separate from a confirmed execution target", () => {
  const target = {
    ownerUserId: "specialist-1",
    botId: "target-bot-1",
    relationship: "cross_bot" as const,
    selectedBy: "specialist-1",
    selectedAt: "2026-08-11T03:00:00Z",
    crossBotConfirmed: true,
  };
  const source = buildInsightPlanSource(sourceRef({
    sourceRefVersion: "evolve-source-ref/v2",
    target,
    targetRefDigest: digestJson(target),
  }), [evidence]);

  assert.equal(source.source.bot_owner_user_id, "owner-1");
  assert.equal(source.source.bot_id, "bot-1");
  assert.deepEqual(source.planning_hints, {
    target_context: {
      relationship: "cross_bot",
      applicability_required: true,
      source_bot: { owner_user_id: "owner-1", bot_id: "bot-1" },
      execution_target: { owner_user_id: "specialist-1", bot_id: "target-bot-1" },
    },
  });
});

test("rejects evidence identity drift and unmapped payload fields", () => {
  assert.throws(
    () => buildInsightPlanSource(sourceRef(), [{ ...evidence, bot_id: "other-bot" }]),
    /bot_id/,
  );
  assert.throws(
    () => buildInsightPlanSource(sourceRef(), [{ ...evidence, future_field: { value: true } }]),
    (error: unknown) => (
      error instanceof Error
      && "code" in error
      && error.code === "PLAN_SOURCE_UNMAPPED_FIELD"
    ),
  );
});

test("accepts deployment-specific compatibility metadata through options", () => {
  const source = buildInsightPlanSource(sourceRef(), [evidence], {
    producer: "deployment-compatible-producer",
    evidenceAccess: {
      instruction: "Read the complete frozen evidence before planning.",
      endpointTemplate: "/evidence/{sessionId}/{taskIndex}",
    },
  });
  const access = (source.extensions as {
    insight: { evidence_access: { instruction: string; endpoint_template: string } };
  }).insight.evidence_access;
  assert.equal(source.source.producer, "deployment-compatible-producer");
  assert.equal(access.instruction, "Read the complete frozen evidence before planning.");
  assert.equal(access.endpoint_template, "/evidence/{sessionId}/{taskIndex}");

  assert.throws(
    () => buildInsightPlanSource(sourceRef(), [evidence], { producer: "invalid producer" }),
    /producer 不合法/,
  );
  assert.throws(
    () => buildInsightPlanSource(sourceRef(), [evidence], {
      evidenceAccess: { endpointTemplate: "https://example.test/evidence" },
    }),
    /endpoint template 不合法/,
  );
});

test("freezes, resolves, and persists Task Source state through public ports", async () => {
  const repo = new MemoryTaskSourceRepository();
  let evidenceReads = 0;
  const service = new TaskSourceService(repo, async () => {
    evidenceReads += 1;
    return evidence;
  });

  await service.freezeInsight("TASK-1", detail, {
    ownerUserId: "specialist-1",
    botId: "target-bot-1",
    selectedBy: "specialist-1",
    crossBotConfirmed: true,
  });
  const descriptor = await service.resolvePlanSource("TASK-1");
  const row = await repo.findByTaskId("TASK-1");

  assert.equal(evidenceReads, 1);
  assert.equal(descriptor.delivery.content.cases.length, 1);
  assert.equal(row?.status, "ready");
  assert.equal(row?.source_digest, descriptor.digest);
  assert.equal(row?.source_schema_version, "plan-source/v2");
});

test("rejects a drifted frozen reference before reading evidence", async () => {
  const repo = new MemoryTaskSourceRepository();
  let evidenceReads = 0;
  const service = new TaskSourceService(repo, async () => {
    evidenceReads += 1;
    return evidence;
  });
  await service.freezeInsight("TASK-DRIFT", detail);
  const row = repo.rows.get("TASK-DRIFT")!;
  const drifted = JSON.parse(row.source_ref_json) as {
    evidence: Array<{ payloadEtag: string }>;
  };
  drifted.evidence[0]!.payloadEtag = "tampered";
  row.source_ref_json = JSON.stringify(drifted);

  await assert.rejects(
    () => service.resolvePlanSource("TASK-DRIFT"),
    (error: unknown) => (
      error instanceof TaskSourceError
      && error.code === "SOURCE_SNAPSHOT_INVALID"
      && error.stage === "freeze"
      && error.retryable === false
    ),
  );
  assert.equal(evidenceReads, 0);
  assert.equal(row.status, "failed");
  assert.equal(row.error_code, "SOURCE_SNAPSHOT_INVALID");
});

test("rejects a drifted execution target before reading evidence", async () => {
  const repo = new MemoryTaskSourceRepository();
  let evidenceReads = 0;
  const service = new TaskSourceService(repo, async () => {
    evidenceReads += 1;
    return evidence;
  });
  await service.freezeInsight("TASK-TARGET-DRIFT", detail, {
    ownerUserId: "specialist-1",
    botId: "target-bot-1",
    selectedBy: "specialist-1",
    crossBotConfirmed: true,
  });
  const row = repo.rows.get("TASK-TARGET-DRIFT")!;
  const drifted = JSON.parse(row.source_ref_json) as { target: { botId: string } };
  drifted.target.botId = "tampered-target";
  row.source_ref_json = JSON.stringify(drifted);

  await assert.rejects(
    () => service.resolvePlanSource("TASK-TARGET-DRIFT"),
    (error: unknown) => error instanceof TaskSourceError && error.code === "SOURCE_SNAPSHOT_INVALID",
  );
  assert.equal(evidenceReads, 0);
  assert.equal(row.status, "failed");
});

test("maps evidence reader failures to a stable retryable error", async () => {
  const repo = new MemoryTaskSourceRepository();
  const service = new TaskSourceService(repo, async () => {
    throw new Error("artifact temporarily unavailable");
  });
  await service.freezeInsight("TASK-UNAVAILABLE", detail);

  await assert.rejects(
    () => service.resolvePlanSource("TASK-UNAVAILABLE"),
    (error: unknown) => (
      error instanceof TaskSourceError
      && error.code === "EVIDENCE_UNAVAILABLE"
      && error.retryable === true
    ),
  );
  assert.equal(repo.rows.get("TASK-UNAVAILABLE")?.status, "failed");
});
