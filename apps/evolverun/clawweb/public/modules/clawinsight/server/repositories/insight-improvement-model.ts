import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { InsightCursorError } from "../services/insight/providers/insight-read-provider.js";
import type { ImprovementEvidenceSnapshot, ImprovementEvolveLinkView, ImprovementView, InsightGovernanceEvent } from "../services/insight/contracts.js";
import { GOVERNANCE_SOURCE_TYPES, OWNER_VISIBLE_SOURCE_TYPES, actionTypeFromSourceType, appendGovernanceEvent, isRejectedGovernanceSourceType, parseGovernanceGuidance, type ImprovementSourceType } from "../services/insight/governance-item.js";

export type ImprovementItemRow = {
  id: number;
  owner_user_id: string;
  bot_owner_user_id: string;
  bot_id: string;
  title: string;
  user_guidance: string | null;
  source_type: string;
  source_rule_id: string | null;
  evidence_count: number;
  session_count: number;
  data_start_time: string | null;
  data_end_time: string | null;
  data_as_of: string;
  batch_id: string;
  content_fingerprint: string;
  idempotency_key: string;
  status: string;
  latest_evolve_task_id?: string | null;
  latest_evolve_task_status?: string | null;
  applied_evolve_task_id: string | null;
  apply_request_id: string | null;
  applied_by: string | null;
  applied_at: number | string | null;
  version: number;
  created_by: string;
  gmt_create: number | string;
  gmt_modified: number | string;
};

export type ImprovementEvidenceRow = {
  session_id: string;
  task_index: number;
  ordinal: number;
  task_description_snapshot: string;
  failure_class_snapshot: string;
  reasoning_summary: string | null;
  payload_ref: string;
  payload_etag: string;
  payload_version_id: string | null;
};

export type ImprovementEvolveLinkRow = {
  improvement_id: number;
  evolve_task_id: string;
  request_id: string;
  created_by: string;
  task_status: string | null;
  task_name: string | null;
  gmt_create: number | string;
};

export type ImprovementCursor = {
  beforeModified: number | string;
  beforeId: number;
};

export function timestampForDb(dbType: IDatabase["dbType"], value: Date): number | string {
  if (dbType !== "mysql" && dbType !== "zdas") return Math.floor(value.getTime() / 1000);
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())} ${pad(value.getHours())}:${pad(value.getMinutes())}:${pad(value.getSeconds())}`;
}

export const sqlValues = (values: readonly string[]) => values.map((value) => `'${value}'`).join(", ");
export const GOVERNANCE_SOURCE_SQL = sqlValues(GOVERNANCE_SOURCE_TYPES);
export const OWNER_VISIBLE_SOURCE_SQL = sqlValues(OWNER_VISIBLE_SOURCE_TYPES);
export const TRUSTED_GOVERNANCE_SOURCE_SQL = sqlValues(
  GOVERNANCE_SOURCE_TYPES.filter((value) => value.startsWith("TRUSTED_RULE_")),
);
export const REVIEWED_GOVERNANCE_SOURCE_SQL = sqlValues(
  GOVERNANCE_SOURCE_TYPES.filter((value) => value.startsWith("ADMIN_RULE_")),
);
export const REJECTED_GOVERNANCE_SOURCE_SQL = sqlValues(
  GOVERNANCE_SOURCE_TYPES.filter((value) => value.startsWith("REJECTED_RULE_")),
);
// 驳回可以作用在所有 Owner 可见来源上（含 USER_SELECTED/ADMIN_SELECTED 待办项，它们不在
// GOVERNANCE_SOURCE_TYPES 里），以及管理员审批驳回产生的 REJECTED_RULE_* 来源。
export const REJECTABLE_SOURCE_SQL = sqlValues([
  ...OWNER_VISIBLE_SOURCE_TYPES,
  ...GOVERNANCE_SOURCE_TYPES.filter((value) => value.startsWith("REJECTED_RULE_")),
]);
// 归档本身不是驳回证据：必须存在真实的驳回事件（用户驳回 / 管理员驳回 / Admin审批驳回）。
export const OWNER_REJECTION_EVENT_SQL = "i.user_guidance LIKE '%[用户驳回]%'";
export const ADMIN_REJECTION_EVENT_SQL =
  "(i.user_guidance LIKE '%[管理员驳回]%' OR i.user_guidance LIKE '%[Admin驳回]%')";
export const ANY_REJECTION_EVENT_SQL = `(${OWNER_REJECTION_EVENT_SQL} OR ${ADMIN_REJECTION_EVENT_SQL})`;

export const STANDARD_VERIFICATION_WAIT_SECONDS = 2 * 24 * 60 * 60;
export const OPEN_VERIFICATION_WAIT_SECONDS = 7 * 24 * 60 * 60;

export function epochSeconds(value: unknown): number | null {
  if (value instanceof Date) {
    const time = value.getTime();
    return Number.isFinite(time) ? Math.floor(time / 1000) : null;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return null;
    return value > 10_000_000_000 ? Math.floor(value / 1000) : Math.floor(value);
  }
  const text = String(value ?? "").trim();
  if (!text) return null;
  if (/^\d+$/.test(text)) {
    const numeric = Number(text);
    return Number.isFinite(numeric)
      ? numeric > 10_000_000_000 ? Math.floor(numeric / 1000) : Math.floor(numeric)
      : null;
  }
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(text)
    ? `${text.replace(" ", "T")}+08:00`
    : text;
  const parsed = Date.parse(normalized);
  return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : null;
}

export function hasElapsed(value: unknown, seconds: number): boolean {
  const timestamp = epochSeconds(value);
  return timestamp !== null && Math.floor(Date.now() / 1000) - timestamp >= seconds;
}

export function isoTimestamp(value: unknown): string | null {
  const seconds = epochSeconds(value);
  return seconds === null ? null : new Date(seconds * 1000).toISOString();
}

export function compactDate(value: unknown): string | null {
  const timestamp = isoTimestamp(value);
  return timestamp ? timestamp.slice(0, 10).replaceAll("-", "") : null;
}

export function addDays(value: string, days: number): string {
  return new Date(new Date(value).getTime() + days * 24 * 60 * 60 * 1000).toISOString();
}

export function effectEvent(item: ImprovementView): InsightGovernanceEvent | null {
  const appliedAt = isoTimestamp(item.appliedAt);
  const handledAt = isoTimestamp(item.handledAt);
  const status = item.status.toUpperCase();
  // 事件锚点优先使用记录在事件流中的状态推进时间；历史数据没有事件时，
  // 对已离开“待修复”的项用 gmt_modified 作为最后的状态推进兜底。
  const statusTransitionAt = ["IN_PROGRESS", "RESOLVED"].includes(status)
    ? isoTimestamp(item.gmtModified)
    : null;
  const effectiveAt = handledAt ?? appliedAt ?? statusTransitionAt;
  if (!effectiveAt) return null;

  const checkedAt = isoTimestamp(item.verificationLastCheckedAt);
  const effectiveSeconds = epochSeconds(effectiveAt);
  const checkedSeconds = epochSeconds(checkedAt);
  const observationEndAt = checkedSeconds !== null && effectiveSeconds !== null && checkedSeconds >= effectiveSeconds
    ? checkedAt
    : addDays(effectiveAt, 2);

  return {
    improvementId: item.improvementId,
    ownerUserId: item.ownerUserId,
    botId: item.botId,
    title: item.title,
    sourceType: item.sourceType,
    sourceRuleId: item.sourceRuleId,
    actionType: item.actionType,
    status: item.status,
    verificationStatus: item.verificationStatus,
    effectiveAt,
    observationEndAt,
    observationDays: 2,
    appliedAt,
    handledAt,
    verificationLastCheckedAt: checkedAt,
    resolvedSource: item.resolvedSource,
    rootCauseSummary: item.rootCauseSummary,
    suggestedAction: item.suggestedAction,
  };
}

export function encodeImprovementCursor(row: ImprovementItemRow): string {
  return Buffer.from(JSON.stringify({
    beforeModified: row.gmt_modified,
    beforeId: row.id,
  } satisfies ImprovementCursor), "utf8").toString("base64url");
}

export function decodeImprovementCursor(raw: string): ImprovementCursor {
  try {
    const value = JSON.parse(Buffer.from(raw, "base64url").toString("utf8")) as Partial<ImprovementCursor>;
    if (
      (typeof value.beforeModified !== "string" && typeof value.beforeModified !== "number")
      || !Number.isInteger(value.beforeId)
      || Number(value.beforeId) <= 0
    ) {
      throw new Error("invalid cursor");
    }
    return value as ImprovementCursor;
  } catch {
    throw new InsightCursorError("改进项游标无效");
  }
}

export class ImprovementEvolveLinkConflictError extends Error {
  readonly code = "IMPROVEMENT_STATE_CONFLICT";
}

export type CreateImprovementInput = {
  ownerUserId: string;
  botOwnerUserId: string;
  botId: string;
  title: string;
  userGuidance: string | null;
  sourceType: ImprovementSourceType;
  sourceRuleId: string | null;
  dataStartTime: string | null;
  dataEndTime: string | null;
  dataAsOf: string;
  batchId: string;
  contentFingerprint: string;
  idempotencyKey: string;
  createdBy: string;
  initialStatus?: "PENDING_ADMIN" | "ACTIVE";
  assignmentReason?: string | null;
  rootCauseSummary?: string | null;
  suggestedAction?: string | null;
  evidence: ImprovementEvidenceSnapshot[];
};

export function itemView(row: ImprovementItemRow): ImprovementView {
  const guidance = parseGovernanceGuidance(row.user_guidance);
  const actionType = actionTypeFromSourceType(row.source_type);
  const status = row.status.toUpperCase();
  const adminReviewStatus: ImprovementView["adminReviewStatus"] = status === "PENDING_ADMIN"
    ? "PENDING"
    : isRejectedGovernanceSourceType(row.source_type)
      ? "REJECTED"
      : "APPROVED";
  const verificationStatus: ImprovementView["verificationStatus"] =
    ["PENDING", "STILL_PRESENT", "VERIFIED", "INSUFFICIENT_DATA"].includes(guidance.verificationStatus ?? "")
      ? guidance.verificationStatus as ImprovementView["verificationStatus"]
      : status === "RESOLVED"
        ? "VERIFIED"
        : status === "IN_PROGRESS" && guidance.handledAt
          ? "PENDING"
          : "NOT_STARTED";
  return {
    improvementId: row.id,
    ownerUserId: row.owner_user_id,
    botOwnerUserId: row.bot_owner_user_id || row.owner_user_id,
    botId: row.bot_id,
    title: row.title,
    userGuidance: row.user_guidance,
    sourceType: row.source_type,
    sourceRuleId: row.source_rule_id,
    evidenceCount: row.evidence_count,
    sessionCount: row.session_count,
    dataStartTime: row.data_start_time,
    dataEndTime: row.data_end_time,
    dataAsOf: row.data_as_of,
    batchId: row.batch_id,
    status: row.status,
    actionType,
    assignmentReason: guidance.assignmentReason,
    rootCauseSummary: guidance.rootCauseSummary,
    suggestedAction: guidance.suggestedAction,
    adminReviewStatus,
    adminReviewedBy: guidance.adminReviewedBy,
    adminReviewedAt: guidance.adminReviewedAt,
    adminReviewComment: guidance.adminReviewComment,
    rejectReasonCode: guidance.rejectReasonCode,
    rejectComment: guidance.rejectComment,
    rejectedAt: guidance.rejectedAt,
    rejectedBy: guidance.rejectedBy,
    handledAt: guidance.handledAt,
    verificationStatus,
    verificationLastCheckedAt: guidance.verificationLastCheckedAt,
    verificationNewSessionCount: guidance.verificationNewSessionCount,
    verificationLastRecurrenceAt: guidance.verificationLastRecurrenceAt,
    resolvedSource: guidance.resolvedSource
      ?? (status === "RESOLVED" && row.applied_evolve_task_id ? "EVOLVE_APPLY" : null),
    latestEvolveTaskId: row.latest_evolve_task_id ?? null,
    latestEvolveTaskStatus: row.latest_evolve_task_status ?? null,
    appliedEvolveTaskId: row.applied_evolve_task_id ?? null,
    appliedBy: row.applied_by ?? null,
    appliedAt: row.applied_at ?? null,
    version: row.version,
    createdBy: row.created_by,
    gmtCreate: row.gmt_create,
    gmtModified: row.gmt_modified,
  };
}

export function appendPendingVerification(guidance: string): string {
  return appendGovernanceEvent(guidance, {
    title: "自动验证",
    values: [["状态", "PENDING"]],
  });
}

export function evidenceView(
  row: ImprovementEvidenceRow,
): ImprovementEvidenceSnapshot {
  return {
    sessionId: row.session_id,
    taskIndex: row.task_index,
    ordinal: row.ordinal,
    taskDescription: row.task_description_snapshot,
    failureClass: row.failure_class_snapshot,
    reasoningSummary: row.reasoning_summary,
    payloadRef: row.payload_ref,
    payloadEtag: row.payload_etag,
    payloadVersionId: row.payload_version_id,
  };
}

export function evolveLinkView(
  row: ImprovementEvolveLinkRow,
): ImprovementEvolveLinkView {
  return {
    evolveTaskId: row.evolve_task_id,
    requestId: row.request_id,
    taskStatus: row.task_status,
    taskName: row.task_name,
    gmtCreate: row.gmt_create,
  };
}
