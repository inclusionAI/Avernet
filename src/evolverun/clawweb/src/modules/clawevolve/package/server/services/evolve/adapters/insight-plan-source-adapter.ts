import { createHash } from "node:crypto";
import type { ImprovementEvidenceSnapshot, SessionEvidence } from "../../insight/contracts.js";
import {
  digestJson,
  PLAN_SOURCE_SCHEMA_VERSION,
  validatePlanSource,
  type PlanSource,
} from "../plan-source-contract.js";

export const LEGACY_INSIGHT_ADAPTER_VERSION = "insight-to-plan-source/v1" as const;
export const INSIGHT_ADAPTER_VERSION = "insight-to-plan-source/v2" as const;

export type InsightExecutionTarget = {
  ownerUserId: string;
  botId: string;
  relationship: "same_bot" | "cross_bot";
  selectedBy: string;
  selectedAt: string;
  crossBotConfirmed: boolean;
};

export type InsightAdminOverride = {
  mode: "ADMIN_ONCE";
  operatorUserId: string;
  reason: string;
  repairDirection: string | null;
};

export type InsightSourceRef = {
  sourceRefVersion: "evolve-source-ref/v1" | "evolve-source-ref/v2";
  sourceType: "insight_improvement";
  improvement: {
    improvementId: number;
    version: number;
    title: string;
    userGuidance: string | null;
    ownerUserId: string;
    botOwnerUserId: string;
    botId: string;
    sourceType: string;
    sourceRuleId: string | null;
    dataStartTime: string | null;
    dataEndTime: string | null;
    dataAsOf: string;
    batchId: string;
    createdBy: string;
    createdAt: string;
    updatedAt: string;
  };
  evidence: ImprovementEvidenceSnapshot[];
  evidenceRefsDigest: string;
  /** The direction supplied when the Repair was created; unlike userGuidance this is execution-scoped. */
  repairDirection?: string | null;
  target?: InsightExecutionTarget;
  targetRefDigest?: string;
  adminOverride?: InsightAdminOverride;
  frozenAt: string;
};

export class InsightPlanSourceAdapterError extends Error {
  constructor(
    readonly code: "EVIDENCE_VERSION_MISMATCH" | "EVIDENCE_SCHEMA_UNSUPPORTED"
      | "PLAN_SOURCE_SCHEMA_INVALID" | "PLAN_SOURCE_UNMAPPED_FIELD",
    message: string,
  ) {
    super(message);
  }
}

const FAILURE_MODE: Record<string, string> = {
  CONFIG_MISSING: "runtime_config_missing",
  TOOL_FAILURE: "tool_execution_failure",
  PARAMETER_ERROR: "tool_parameter_error",
  PERMISSION_NETWORK: "permission_or_network_blocked",
  DATA_ISSUE: "workspace_or_data_missing",
  WORKFLOW_FAILURE: "workflow_planning_failure",
  OUTPUT_WRONG: "incorrect_or_unverified_answer",
};

const EVIDENCE_FIELDS = new Set([
  "schema_version", "batch_id", "dt", "user_id", "bot_id", "session_id",
  "session", "messages", "tasks", "judge_meta", "generated_at",
]);
const MESSAGE_FIELDS = new Set([
  "message_index", "role", "timestamp", "visibility", "content", "raw",
]);

function isEmpty(value: unknown): boolean {
  return value == null
    || (typeof value === "string" && value.length === 0)
    || (Array.isArray(value) && value.length === 0)
    || (typeof value === "object" && !Array.isArray(value) && Object.keys(value).length === 0);
}

function rejectUnmappedFields(
  value: Record<string, unknown>,
  allowed: Set<string>,
  path: string,
): void {
  const fields = Object.entries(value)
    .filter(([key, child]) => !allowed.has(key) && !isEmpty(child))
    .map(([key]) => key)
    .sort();
  if (fields.length) {
    throw new InsightPlanSourceAdapterError(
      "PLAN_SOURCE_UNMAPPED_FIELD",
      `${path} 存在未登记的非空字段: ${fields.join(", ")} (adapter=${INSIGHT_ADAPTER_VERSION})`,
    );
  }
}

function mismatch(path: string, expected: unknown, actual: unknown): never {
  throw new InsightPlanSourceAdapterError(
    "EVIDENCE_VERSION_MISMATCH",
    `${path} 与冻结引用不一致: expected=${String(expected)}, actual=${String(actual)}`,
  );
}

function caseId(improvementId: number, sessionId: string, taskIndex: number): string {
  const sessionHash = createHash("sha256").update(sessionId, "utf8").digest("hex").slice(0, 12);
  return `insight_${improvementId}_${sessionHash}_${taskIndex}`;
}

function buildCase(
  sourceRef: InsightSourceRef,
  snapshot: ImprovementEvidenceSnapshot,
  evidence: SessionEvidence,
): PlanSource["cases"][number] {
  const improvement = sourceRef.improvement;
  if (evidence.schema_version !== "session-evidence/v1") {
    throw new InsightPlanSourceAdapterError(
      "EVIDENCE_SCHEMA_UNSUPPORTED",
      `Evidence schema_version 不支持: ${String(evidence.schema_version)}`,
    );
  }
  rejectUnmappedFields(evidence, EVIDENCE_FIELDS, `Evidence ${snapshot.sessionId}`);
  if (evidence.user_id !== improvement.botOwnerUserId) mismatch("Evidence user_id", improvement.botOwnerUserId, evidence.user_id);
  if (evidence.bot_id !== improvement.botId) mismatch("Evidence bot_id", improvement.botId, evidence.bot_id);
  if (evidence.session_id !== snapshot.sessionId) mismatch("Evidence session_id", snapshot.sessionId, evidence.session_id);
  // MULTI_BATCH is an aggregate marker on the Improvement, not an Evidence
  // batch identifier. Individual payload integrity remains frozen by its
  // payload ref/version/etag and the session/task identity checks below.
  if (improvement.batchId !== "MULTI_BATCH" && evidence.batch_id !== improvement.batchId) {
    mismatch("Evidence batch_id", improvement.batchId, evidence.batch_id);
  }
  const sourceTask = evidence.tasks.find((item) => item.task_index === snapshot.taskIndex);
  if (!sourceTask) mismatch("Evidence task_index", snapshot.taskIndex, "missing");
  if (sourceTask.task_description !== snapshot.taskDescription) {
    mismatch("Evidence task_description", snapshot.taskDescription, sourceTask.task_description);
  }
  const taskFailureClass = String(sourceTask.task_failure_class ?? "");
  if (taskFailureClass && taskFailureClass !== snapshot.failureClass) {
    mismatch("Evidence task_failure_class", snapshot.failureClass, taskFailureClass);
  }
  const [start, end] = sourceTask.message_range;
  const messages = evidence.messages.filter((message) => (
    message.message_index >= start && message.message_index < end
  ));
  messages.forEach((message, index) => rejectUnmappedFields(
    message,
    MESSAGE_FIELDS,
    `Evidence ${snapshot.sessionId} target messages[${index}]`,
  ));
  const analysis: Record<string, unknown> = {
    failure_class: snapshot.failureClass,
    evolution_failure_mode: FAILURE_MODE[snapshot.failureClass] ?? "unknown_failure_mode",
  };
  if (snapshot.reasoningSummary) analysis.judge_summary = snapshot.reasoningSummary;
  return {
    case_id: caseId(improvement.improvementId, snapshot.sessionId, snapshot.taskIndex),
    case_type: "bad",
    ordinal: snapshot.ordinal,
    session_id: snapshot.sessionId,
    task_index: snapshot.taskIndex,
    query: snapshot.taskDescription,
    evidence: {
      schema_version: evidence.schema_version,
      batch_id: evidence.batch_id,
      dt: evidence.dt,
      generated_at: evidence.generated_at,
      message_range: [...sourceTask.message_range],
      source_task: { ...sourceTask },
      session: evidence.session,
      judge_meta: evidence.judge_meta,
      messages,
      payload_ref: snapshot.payloadRef,
      payload_etag: snapshot.payloadEtag,
      payload_version_id: snapshot.payloadVersionId,
    },
    analysis,
  };
}

export function buildInsightPlanSource(
  sourceRef: InsightSourceRef,
  evidencePayloads: SessionEvidence[],
): PlanSource {
  if (
    !["evolve-source-ref/v1", "evolve-source-ref/v2"].includes(sourceRef.sourceRefVersion)
    || sourceRef.sourceType !== "insight_improvement"
  ) {
    throw new InsightPlanSourceAdapterError("PLAN_SOURCE_SCHEMA_INVALID", "Insight SourceRef 契约不支持");
  }
  const target = sourceRef.target;
  if (sourceRef.sourceRefVersion === "evolve-source-ref/v2") {
    if (!target || !sourceRef.targetRefDigest) {
      throw new InsightPlanSourceAdapterError("PLAN_SOURCE_SCHEMA_INVALID", "SourceRef v2 缺少冻结执行目标");
    }
    if (sourceRef.targetRefDigest !== digestJson(target)) {
      throw new InsightPlanSourceAdapterError("PLAN_SOURCE_SCHEMA_INVALID", "SourceRef 执行目标 digest 不一致");
    }
    const expectedRelationship = target.ownerUserId === sourceRef.improvement.botOwnerUserId
      && target.botId === sourceRef.improvement.botId
      ? "same_bot" : "cross_bot";
    if (target.relationship !== expectedRelationship) {
      throw new InsightPlanSourceAdapterError("PLAN_SOURCE_SCHEMA_INVALID", "SourceRef 执行目标关系与来源不一致");
    }
    if (
      !target.ownerUserId || !target.botId || !target.selectedBy || !target.selectedAt
      || target.crossBotConfirmed !== (target.relationship === "cross_bot")
    ) {
      throw new InsightPlanSourceAdapterError("PLAN_SOURCE_SCHEMA_INVALID", "SourceRef 执行目标审计信息不完整或不一致");
    }
  } else if (target || sourceRef.targetRefDigest) {
    throw new InsightPlanSourceAdapterError("PLAN_SOURCE_SCHEMA_INVALID", "SourceRef v1 不支持执行目标字段");
  }
  if (sourceRef.evidence.length === 0 || sourceRef.evidence.length !== evidencePayloads.length) {
    throw new InsightPlanSourceAdapterError(
      "PLAN_SOURCE_SCHEMA_INVALID",
      `冻结 Evidence 数量不一致: refs=${sourceRef.evidence.length}, payloads=${evidencePayloads.length}`,
    );
  }
  const cases = sourceRef.evidence.map((snapshot, index) => buildCase(sourceRef, snapshot, evidencePayloads[index]));
  const byFailureClass = cases.reduce<Record<string, number>>((counts, item) => {
    const failureClass = String(item.analysis?.failure_class ?? "UNKNOWN");
    counts[failureClass] = (counts[failureClass] ?? 0) + 1;
    return counts;
  }, {});
  const improvement = sourceRef.improvement;
  const userGuidance = [
    sourceRef.repairDirection
      ? `本次修复方向：${sourceRef.repairDirection}`
      : improvement.userGuidance,
    sourceRef.adminOverride?.repairDirection && sourceRef.adminOverride.repairDirection !== sourceRef.repairDirection
      ? `管理员指定修复方向：${sourceRef.adminOverride.repairDirection}`
      : null,
  ].filter((value): value is string => Boolean(value?.trim())).join("\n\n") || null;
  const source: PlanSource = {
    schema_version: PLAN_SOURCE_SCHEMA_VERSION,
    generated_at: sourceRef.frozenAt,
    source: {
      type: "insight_improvement",
      id: `improvement:${improvement.improvementId}`,
      producer: "clawweb-insight-plan-source-adapter",
      adapter_version: target ? INSIGHT_ADAPTER_VERSION : LEGACY_INSIGHT_ADAPTER_VERSION,
      owner_user_id: improvement.ownerUserId,
      bot_owner_user_id: improvement.botOwnerUserId,
      bot_id: improvement.botId,
      version: String(improvement.version),
      frozen_at: sourceRef.frozenAt,
    },
    problem: { title: improvement.title, user_guidance: userGuidance },
    cases,
    analysis: {
      case_distribution: { total: cases.length, bad: cases.length, by_failure_class: byFailureClass },
      root_cause_clusters: [],
    },
    planning_hints: target ? {
      target_context: {
        relationship: target.relationship,
        applicability_required: target.relationship === "cross_bot",
        source_bot: {
          owner_user_id: improvement.botOwnerUserId,
          bot_id: improvement.botId,
        },
        execution_target: {
          owner_user_id: target.ownerUserId,
          bot_id: target.botId,
        },
      },
    } : {},
    extensions: {
      insight: {
        contract_version: "insight-improvement-handoff/v1",
        source_type: improvement.sourceType,
        source_rule_id: improvement.sourceRuleId,
        batch_id: improvement.batchId,
        data_as_of: improvement.dataAsOf,
        data_start_time: improvement.dataStartTime,
        data_end_time: improvement.dataEndTime,
        evidence_schema_version: "session-evidence/v1",
        evidence_refs_digest: sourceRef.evidenceRefsDigest,
        session_ids: [...new Set(sourceRef.evidence.map((item) => item.sessionId))],
        evidence_task_refs: sourceRef.evidence.map((item) => ({
          session_id: item.sessionId,
          task_index: item.taskIndex,
          ordinal: item.ordinal,
        })),
        evidence_access: {
          available: true,
          mode: "frozen_reference",
          instruction: "优先通过 ClawWeb Evidence 接口读取完整 Session 和 Task 证据，不要只根据问题标题判断。",
          endpoint_template: "/api/insight/v1/evidence-access/{ownerUserId}/{improvementId}/{sessionId}/{taskIndex}",
        },
        repair_direction: sourceRef.repairDirection
          ?? sourceRef.adminOverride?.repairDirection
          ?? improvement.userGuidance,
        audit: {
          created_by: improvement.createdBy,
          created_at: improvement.createdAt,
          updated_at: improvement.updatedAt,
        },
        ...(target ? {
          execution: {
            target_owner_user_id: target.ownerUserId,
            target_bot_id: target.botId,
            relationship: target.relationship,
            selected_by: target.selectedBy,
            selected_at: target.selectedAt,
            cross_bot_confirmed: target.crossBotConfirmed,
          },
        } : {}),
        ...(sourceRef.adminOverride ? {
          admin_override: {
            mode: sourceRef.adminOverride.mode,
            operator_user_id: sourceRef.adminOverride.operatorUserId,
            reason: sourceRef.adminOverride.reason,
            repair_direction: sourceRef.adminOverride.repairDirection,
            persistent_authorization: false,
          },
        } : {}),
      },
    },
  };
  try {
    validatePlanSource(source);
  } catch (error) {
    throw new InsightPlanSourceAdapterError(
      "PLAN_SOURCE_SCHEMA_INVALID",
      error instanceof Error ? error.message : String(error),
    );
  }
  return source;
}
