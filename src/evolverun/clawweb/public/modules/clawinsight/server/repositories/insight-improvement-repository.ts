import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { InsightCursorError } from "../services/insight/providers/insight-read-provider.js";
import type {
  ImprovementDetail,
  ImprovementEvidenceSnapshot,
  ImprovementEvolveLinkView,
  ImprovementView,
  InsightGovernanceEvent,
  InsightQueryScope,
} from "../services/insight/contracts.js";
import {
  GOVERNANCE_SOURCE_TYPES,
  OWNER_VISIBLE_SOURCE_TYPES,
  actionTypeFromSourceType,
  assignOwnerGovernanceSourceType,
  appendGovernanceEvent,
  buildGovernanceGuidance,
  isGovernanceSourceType,
  isRejectedGovernanceSourceType,
  parseGovernanceGuidance,
  rejectedGovernanceSourceType,
  restoredGovernanceSourceType,
  type ImprovementSourceType,
} from "../services/insight/governance-item.js";

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

type ImprovementEvidenceRow = {
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

type ImprovementCursor = {
  beforeModified: number | string;
  beforeId: number;
};

export function timestampForDb(dbType: IDatabase["dbType"], value: Date): number | string {
  if (dbType !== "mysql" && dbType !== "zdas") return Math.floor(value.getTime() / 1000);
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())} ${pad(value.getHours())}:${pad(value.getMinutes())}:${pad(value.getSeconds())}`;
}

const sqlValues = (values: readonly string[]) => values.map((value) => `'${value}'`).join(", ");
const GOVERNANCE_SOURCE_SQL = sqlValues(GOVERNANCE_SOURCE_TYPES);
const OWNER_VISIBLE_SOURCE_SQL = sqlValues(OWNER_VISIBLE_SOURCE_TYPES);
const TRUSTED_GOVERNANCE_SOURCE_SQL = sqlValues(
  GOVERNANCE_SOURCE_TYPES.filter((value) => value.startsWith("TRUSTED_RULE_")),
);
const REVIEWED_GOVERNANCE_SOURCE_SQL = sqlValues(
  GOVERNANCE_SOURCE_TYPES.filter((value) => value.startsWith("ADMIN_RULE_")),
);
const REJECTED_GOVERNANCE_SOURCE_SQL = sqlValues(
  GOVERNANCE_SOURCE_TYPES.filter((value) => value.startsWith("REJECTED_RULE_")),
);

export const STANDARD_VERIFICATION_WAIT_SECONDS = 2 * 24 * 60 * 60;
export const OPEN_VERIFICATION_WAIT_SECONDS = 7 * 24 * 60 * 60;

function epochSeconds(value: unknown): number | null {
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

function hasElapsed(value: unknown, seconds: number): boolean {
  const timestamp = epochSeconds(value);
  return timestamp !== null && Math.floor(Date.now() / 1000) - timestamp >= seconds;
}

function isoTimestamp(value: unknown): string | null {
  const seconds = epochSeconds(value);
  return seconds === null ? null : new Date(seconds * 1000).toISOString();
}

function compactDate(value: unknown): string | null {
  const timestamp = isoTimestamp(value);
  return timestamp ? timestamp.slice(0, 10).replaceAll("-", "") : null;
}

function addDays(value: string, days: number): string {
  return new Date(new Date(value).getTime() + days * 24 * 60 * 60 * 1000).toISOString();
}

function effectEvent(item: ImprovementView): InsightGovernanceEvent | null {
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

function encodeImprovementCursor(row: ImprovementItemRow): string {
  return Buffer.from(JSON.stringify({
    beforeModified: row.gmt_modified,
    beforeId: row.id,
  } satisfies ImprovementCursor), "utf8").toString("base64url");
}

function decodeImprovementCursor(raw: string): ImprovementCursor {
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

function itemView(row: ImprovementItemRow): ImprovementView {
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

function appendPendingVerification(guidance: string): string {
  return appendGovernanceEvent(guidance, {
    title: "自动验证",
    values: [["状态", "PENDING"]],
  });
}

function evidenceView(
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

function evolveLinkView(
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

export class InsightImprovementRepository {
  constructor(private readonly db: IDatabase) {}

  async findByIdempotency(
    ownerUserId: string,
    idempotencyKey: string,
  ): Promise<ImprovementItemRow | null> {
    return (
      (
        await this.db.query<ImprovementItemRow>(
          "SELECT * FROM insight_improvement_item WHERE owner_user_id = ? AND idempotency_key = ? LIMIT 1",
          [ownerUserId, idempotencyKey],
        )
      )[0] ?? null
    );
  }

  async create(
    input: CreateImprovementInput,
  ): Promise<{ created: boolean; item: ImprovementItemRow }> {
    return this.db.transaction(async (tx) => {
      const existing = (
        await tx.query<ImprovementItemRow>(
          "SELECT * FROM insight_improvement_item WHERE owner_user_id = ? AND idempotency_key = ? LIMIT 1",
          [input.ownerUserId, input.idempotencyKey],
        )
      )[0];
      if (existing) return { created: false, item: existing };

      const now = tx.dialect.now();
      const initialStatus = input.initialStatus ?? "ACTIVE";
      const storedGuidance = isGovernanceSourceType(input.sourceType)
        ? buildGovernanceGuidance(input)
        : input.userGuidance;
      const insertResult = await tx.exec(
        `INSERT INTO insight_improvement_item
         (owner_user_id, bot_owner_user_id, bot_id, title, user_guidance, source_type, source_rule_id,
          evidence_count, session_count, data_start_time, data_end_time, data_as_of, batch_id,
          content_fingerprint, idempotency_key, status, version, created_by, gmt_create, gmt_modified)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)`,
        [
          input.ownerUserId,
          input.botOwnerUserId,
          input.botId,
          input.title,
          storedGuidance,
          input.sourceType,
          input.sourceRuleId,
          input.evidence.length,
          new Set(input.evidence.map((item) => item.sessionId)).size,
          input.dataStartTime,
          input.dataEndTime,
          input.dataAsOf,
          input.batchId,
          input.contentFingerprint,
          input.idempotencyKey,
          initialStatus,
          input.createdBy,
          now,
          now,
        ],
      );
      let created = insertResult.insertId
        ? (
            await tx.query<ImprovementItemRow>(
              "SELECT * FROM insight_improvement_item WHERE id = ? LIMIT 1",
              [insertResult.insertId],
            )
          )[0]
        : undefined;
      if (!created) {
        created = (
          await tx.query<ImprovementItemRow>(
            "SELECT * FROM insight_improvement_item WHERE owner_user_id = ? AND idempotency_key = ? LIMIT 1",
            [input.ownerUserId, input.idempotencyKey],
          )
        )[0];
      }
      if (!created) throw new Error("创建改进项失败");
      for (const evidence of input.evidence) {
        await tx.exec(
          `INSERT INTO insight_improvement_evidence
           (improvement_id, session_id, task_index, ordinal, task_description_snapshot,
            failure_class_snapshot, reasoning_summary, payload_ref, payload_etag,
            payload_version_id, gmt_create)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
          [
            created.id,
            evidence.sessionId,
            evidence.taskIndex,
            evidence.ordinal,
            evidence.taskDescription,
            evidence.failureClass,
            evidence.reasoningSummary,
            evidence.payloadRef,
            evidence.payloadEtag,
            evidence.payloadVersionId,
            now,
          ],
        );
      }
      return { created: true, item: created };
    });
  }

  async list(
    ownerUserId: string,
    input: {
      botId?: string;
      status?: string;
      cursor?: string;
      pageSize: number;
    },
  ): Promise<{
    items: ImprovementView[];
    nextCursor: string | null;
    statusCounts: {
      active: number;
      inProgress: number;
      resolved: number;
      archived: number;
    };
  }> {
    const conditions = [
      "i.status <> 'PENDING_ADMIN'",
      `i.source_type IN (${OWNER_VISIBLE_SOURCE_SQL})`,
    ];
    const params: unknown[] = [];
    if (ownerUserId !== "*") {
      conditions.unshift("i.owner_user_id = ?");
      params.push(ownerUserId);
    }
    if (input.botId) {
      conditions.push("i.bot_id = ?");
      params.push(input.botId);
    }
    if (input.status) {
      conditions.push("i.status = ?");
      params.push(input.status);
    }
    if (input.cursor) {
      const cursor = decodeImprovementCursor(input.cursor);
      conditions.push("(i.gmt_modified < ? OR (i.gmt_modified = ? AND i.id < ?))");
      params.push(cursor.beforeModified, cursor.beforeModified, cursor.beforeId);
    }
    params.push(input.pageSize + 1);
    const rows = await this.db.query<ImprovementItemRow>(
      `SELECT i.*,
              (SELECT l.evolve_task_id
                 FROM insight_improvement_evolve_link l
                WHERE l.improvement_id = i.id
                ORDER BY l.id DESC LIMIT 1) AS latest_evolve_task_id,
              (SELECT t.status
                 FROM insight_improvement_evolve_link l
                 LEFT JOIN ce_tasks t ON t.task_id = l.evolve_task_id
                WHERE l.improvement_id = i.id
                ORDER BY l.id DESC LIMIT 1) AS latest_evolve_task_status
         FROM insight_improvement_item i
       WHERE ${conditions.join(" AND ")}
       ORDER BY i.gmt_modified DESC, i.id DESC
       LIMIT ?`,
      params,
    );
    const countConditions = [
      "status <> 'PENDING_ADMIN'",
      `source_type IN (${OWNER_VISIBLE_SOURCE_SQL})`,
    ];
    const countParams: unknown[] = [];
    if (ownerUserId !== "*") {
      countConditions.unshift("owner_user_id = ?");
      countParams.push(ownerUserId);
    }
    if (input.botId) {
      countConditions.push("bot_id = ?");
      countParams.push(input.botId);
    }
    const countRows = await this.db.query<{ status: string; item_count: number | string }>(
      `SELECT status, COUNT(*) AS item_count
         FROM insight_improvement_item
        WHERE ${countConditions.join(" AND ")}
        GROUP BY status`,
      countParams,
    );
    const statusCounts = {
      active: 0,
      inProgress: 0,
      resolved: 0,
      archived: 0,
    };
    for (const row of countRows) {
      const count = Number(row.item_count) || 0;
      switch (row.status.toUpperCase()) {
        case "IN_PROGRESS": statusCounts.inProgress = count; break;
        case "RESOLVED": statusCounts.resolved = count; break;
        case "ARCHIVED": statusCounts.archived = count; break;
        case "ACTIVE": statusCounts.active += count; break;
      }
    }
    const hasMore = rows.length > input.pageSize;
    const visible = rows.slice(0, input.pageSize);
    return {
      items: visible.map(itemView),
      nextCursor:
        hasMore && visible.length
          ? encodeImprovementCursor(visible[visible.length - 1])
          : null,
      statusCounts,
    };
  }

  async findItem(
    ownerUserId: string,
    improvementId: number,
  ): Promise<ImprovementItemRow | null> {
    return (
      (
        await this.db.query<ImprovementItemRow>(
          `SELECT i.*,
                  (SELECT l.evolve_task_id FROM insight_improvement_evolve_link l
                    WHERE l.improvement_id = i.id ORDER BY l.id DESC LIMIT 1) AS latest_evolve_task_id,
                  (SELECT t.status FROM insight_improvement_evolve_link l
                    LEFT JOIN ce_tasks t ON t.task_id = l.evolve_task_id
                    WHERE l.improvement_id = i.id ORDER BY l.id DESC LIMIT 1) AS latest_evolve_task_status
             FROM insight_improvement_item i
            WHERE i.owner_user_id = ? AND i.id = ?
              AND i.status <> 'PENDING_ADMIN'
              AND i.source_type IN (${OWNER_VISIBLE_SOURCE_SQL}) LIMIT 1`,
          [ownerUserId, improvementId],
        )
      )[0] ?? null
    );
  }

  async getDetail(
    ownerUserId: string,
    improvementId: number,
  ): Promise<ImprovementDetail | null> {
    const item = await this.findItem(ownerUserId, improvementId);
    if (!item) return null;
    return this.loadDetail(item);
  }

  async findItemById(improvementId: number): Promise<ImprovementItemRow | null> {
    return (
      (
        await this.db.query<ImprovementItemRow>(
          `SELECT i.*,
                  (SELECT l.evolve_task_id FROM insight_improvement_evolve_link l
                    WHERE l.improvement_id = i.id ORDER BY l.id DESC LIMIT 1) AS latest_evolve_task_id,
                  (SELECT t.status FROM insight_improvement_evolve_link l
                    LEFT JOIN ce_tasks t ON t.task_id = l.evolve_task_id
                    WHERE l.improvement_id = i.id ORDER BY l.id DESC LIMIT 1) AS latest_evolve_task_status
             FROM insight_improvement_item i
            WHERE i.id = ? LIMIT 1`,
          [improvementId],
        )
      )[0] ?? null
    );
  }

  async getDetailById(improvementId: number): Promise<ImprovementDetail | null> {
    const item = await this.findItemById(improvementId);
    return item ? this.loadDetail(item) : null;
  }

  private async loadDetail(item: ImprovementItemRow): Promise<ImprovementDetail> {
    const improvementId = item.id;
    const [evidence, links] = await Promise.all([
      this.db.query<ImprovementEvidenceRow>(
        `SELECT session_id, task_index, ordinal, task_description_snapshot, failure_class_snapshot,
                reasoning_summary, payload_ref, payload_etag, payload_version_id
         FROM insight_improvement_evidence
         WHERE improvement_id = ? ORDER BY ordinal`,
        [improvementId],
      ),
      this.db.query<ImprovementEvolveLinkRow>(
        `SELECT l.evolve_task_id, l.request_id, l.gmt_create,
                t.status AS task_status, t.task_name
         FROM insight_improvement_evolve_link l
         LEFT JOIN ce_tasks t ON t.task_id = l.evolve_task_id
         WHERE l.improvement_id = ? ORDER BY l.id DESC`,
        [improvementId],
      ),
    ]);
    return {
      ...itemView(item),
      evidence: evidence.map(evidenceView),
      evolveLinks: links.map(evolveLinkView),
    };
  }

  async listAdmin(input: {
    ownerUserId?: string;
    botId?: string;
    status?: string;
    adminReviewStatus?: string;
    includeAll?: boolean;
    cursor?: string;
    pageSize: number;
  }): Promise<{
    items: ImprovementView[];
    nextCursor: string | null;
    statusCounts: {
      active: number;
      inProgress: number;
      resolved: number;
      archived: number;
    };
    reviewCounts: { pending: number; approved: number; rejected: number };
  }> {
    const includeAll = input.includeAll === true;
    const baseScopeConditions = includeAll ? ["1 = 1"] : [`i.source_type IN (${GOVERNANCE_SOURCE_SQL})`];
    const scopeParams: unknown[] = [];
    if (input.ownerUserId && input.ownerUserId !== "*") {
      baseScopeConditions.push("i.owner_user_id = ?");
      scopeParams.push(input.ownerUserId);
    }
    if (input.botId) {
      baseScopeConditions.push("i.bot_id = ?");
      scopeParams.push(input.botId);
    }
    const conditions = [...baseScopeConditions];
    const params = [...scopeParams];
    if (input.status) {
      conditions.push("i.status = ?");
      params.push(input.status);
    }
    switch (includeAll ? undefined : input.adminReviewStatus) {
      case "PENDING":
        conditions.push("i.status = 'PENDING_ADMIN'");
        break;
      case "REJECTED":
        conditions.push(`i.source_type IN (${REJECTED_GOVERNANCE_SOURCE_SQL})`);
        break;
      case "APPROVED":
        conditions.push(`i.source_type IN (${REVIEWED_GOVERNANCE_SOURCE_SQL}, ${TRUSTED_GOVERNANCE_SOURCE_SQL})`);
        conditions.push("i.status <> 'PENDING_ADMIN'");
        break;
    }
    if (input.cursor) {
      const cursor = decodeImprovementCursor(input.cursor);
      conditions.push("(i.gmt_modified < ? OR (i.gmt_modified = ? AND i.id < ?))");
      params.push(cursor.beforeModified, cursor.beforeModified, cursor.beforeId);
    }
    params.push(input.pageSize + 1);
    const rows = await this.db.query<ImprovementItemRow>(
      `SELECT i.*,
              (SELECT l.evolve_task_id FROM insight_improvement_evolve_link l
                WHERE l.improvement_id = i.id ORDER BY l.id DESC LIMIT 1) AS latest_evolve_task_id,
              (SELECT t.status FROM insight_improvement_evolve_link l
                LEFT JOIN ce_tasks t ON t.task_id = l.evolve_task_id
                WHERE l.improvement_id = i.id ORDER BY l.id DESC LIMIT 1) AS latest_evolve_task_status
         FROM insight_improvement_item i
        WHERE ${conditions.join(" AND ")}
        ORDER BY i.gmt_modified DESC, i.id DESC LIMIT ?`,
      params,
    );
    const countRows = await this.db.query<{ source_type: string; status: string; item_count: number | string }>(
      `SELECT source_type, status, COUNT(*) AS item_count
         FROM insight_improvement_item i
        WHERE ${baseScopeConditions.join(" AND ")}
        GROUP BY source_type, status`,
      scopeParams,
    );
    const statusCounts = {
      active: 0,
      inProgress: 0,
      resolved: 0,
      archived: 0,
    };
    const reviewCounts = { pending: 0, approved: 0, rejected: 0 };
    for (const row of countRows) {
      const count = Number(row.item_count) || 0;
      switch (row.status.toUpperCase()) {
        case "ACTIVE": statusCounts.active += count; break;
        case "IN_PROGRESS": statusCounts.inProgress += count; break;
        case "RESOLVED": statusCounts.resolved += count; break;
        case "ARCHIVED": statusCounts.archived += count; break;
      }
      if (row.status.toUpperCase() === "PENDING_ADMIN") reviewCounts.pending += count;
      else if (isRejectedGovernanceSourceType(row.source_type)) reviewCounts.rejected += count;
      else if (!includeAll || isGovernanceSourceType(row.source_type)) reviewCounts.approved += count;
    }
    const hasMore = rows.length > input.pageSize;
    const visible = rows.slice(0, input.pageSize);
    return {
      items: visible.map(itemView),
      nextCursor: hasMore && visible.length ? encodeImprovementCursor(visible[visible.length - 1]) : null,
      statusCounts,
      reviewCounts,
    };
  }

  async reviewAdminAction(input: {
    improvementId: number;
    expectedVersion: number;
    decision: "APPROVE" | "REJECT";
    reviewedBy: string;
    comment?: string | null;
  }): Promise<ImprovementView | null | "VERSION_CONFLICT" | "STATE_CONFLICT"> {
    return this.db.transaction(async (tx) => {
      const existing = (
        await tx.query<ImprovementItemRow>(
          "SELECT * FROM insight_improvement_item WHERE id = ? LIMIT 1",
          [input.improvementId],
        )
      )[0];
      if (!existing) return null;
      if (existing.version !== input.expectedVersion) return "VERSION_CONFLICT";
      if (existing.status.toUpperCase() !== "PENDING_ADMIN" || !isGovernanceSourceType(existing.source_type)) {
        return "STATE_CONFLICT";
      }

      const actionType = actionTypeFromSourceType(existing.source_type);
      if (!actionType) return "STATE_CONFLICT";
      const now = tx.dialect.now();
      const approved = input.decision === "APPROVE";
      const status = approved ? "ACTIVE" : "ARCHIVED";
      const sourceType = input.decision === "REJECT"
        ? rejectedGovernanceSourceType(existing.source_type) ?? existing.source_type
        : existing.source_type;
      let guidance = appendGovernanceEvent(existing.user_guidance, {
        title: "Admin审核",
        values: [
          ["决定", input.decision],
          ["审核人", input.reviewedBy],
          ["说明", input.comment],
          ["审核时间", now],
        ],
      });
      if (input.decision === "REJECT") {
        guidance = appendGovernanceEvent(guidance, {
          title: "Admin驳回",
          values: [
            ["原因", "ADMIN_REJECTED"],
            ["说明", input.comment],
            ["时间", now],
          ],
        });
      }
      const result = await tx.exec(
        `UPDATE insight_improvement_item
            SET source_type = ?, user_guidance = ?, status = ?,
                version = version + 1, gmt_modified = ?
          WHERE id = ? AND version = ? AND status = 'PENDING_ADMIN'`,
        [sourceType, guidance, status, now, input.improvementId, input.expectedVersion],
      );
      if (result.affectedRows !== 1) return "VERSION_CONFLICT";

      const updated = (
        await tx.query<ImprovementItemRow>(
          `SELECT i.*,
                  (SELECT l.evolve_task_id FROM insight_improvement_evolve_link l
                    WHERE l.improvement_id = i.id ORDER BY l.id DESC LIMIT 1) AS latest_evolve_task_id,
                  (SELECT t.status FROM insight_improvement_evolve_link l
                    LEFT JOIN ce_tasks t ON t.task_id = l.evolve_task_id
                    WHERE l.improvement_id = i.id ORDER BY l.id DESC LIMIT 1) AS latest_evolve_task_status
             FROM insight_improvement_item i
            WHERE i.id = ? LIMIT 1`,
          [input.improvementId],
        )
      )[0];
      return updated ? itemView(updated) : null;
    });
  }

  async markHandled(
    ownerUserId: string,
    improvementId: number,
    expectedVersion: number,
  ): Promise<ImprovementView | null | "VERSION_CONFLICT" | "STATE_CONFLICT"> {
    const existing = await this.findItem(ownerUserId, improvementId);
    if (!existing) return null;
    if (existing.version !== expectedVersion) return "VERSION_CONFLICT";
    if (!["ACTIVE", "IN_PROGRESS"].includes(existing.status.toUpperCase())) return "STATE_CONFLICT";
    const now = this.db.dialect.now();
    const guidance = isGovernanceSourceType(existing.source_type)
      ? appendPendingVerification(appendGovernanceEvent(existing.user_guidance, {
          title: "用户已处理",
          values: [["时间", now]],
        }))
      : existing.user_guidance;
    const result = await this.db.exec(
      `UPDATE insight_improvement_item
          SET status = 'IN_PROGRESS', user_guidance = ?, version = version + 1, gmt_modified = ?
        WHERE owner_user_id = ? AND id = ? AND version = ?
          AND source_type IN (${OWNER_VISIBLE_SOURCE_SQL})
          AND status IN ('ACTIVE', 'IN_PROGRESS')`,
      [guidance, now, ownerUserId, improvementId, expectedVersion],
    );
    if (result.affectedRows !== 1) return "VERSION_CONFLICT";
    const updated = await this.findItem(ownerUserId, improvementId);
    return updated ? itemView(updated) : null;
  }

  async markAdminHandled(input: {
    improvementId: number;
    expectedVersion: number;
    operatedBy: string;
  }): Promise<ImprovementView | null | "VERSION_CONFLICT" | "STATE_CONFLICT"> {
    const existing = await this.findItemById(input.improvementId);
    if (!existing) return null;
    if (existing.version !== input.expectedVersion) return "VERSION_CONFLICT";
    if (!OWNER_VISIBLE_SOURCE_TYPES.includes(existing.source_type as typeof OWNER_VISIBLE_SOURCE_TYPES[number])) {
      return "STATE_CONFLICT";
    }
    if (!["ACTIVE", "IN_PROGRESS"].includes(existing.status.toUpperCase())) return "STATE_CONFLICT";
    const now = this.db.dialect.now();
    const guidance = appendPendingVerification(appendGovernanceEvent(existing.user_guidance, {
      title: "管理员已处理",
      values: [
        ["操作人", input.operatedBy],
        ["时间", now],
        ["方式", "ADMIN_HANDOFF"],
      ],
    }));
    const result = await this.db.exec(
      `UPDATE insight_improvement_item
          SET status = 'IN_PROGRESS', user_guidance = ?, version = version + 1, gmt_modified = ?
        WHERE id = ? AND version = ? AND status IN ('ACTIVE', 'IN_PROGRESS')
          AND source_type IN (${OWNER_VISIBLE_SOURCE_SQL})`,
      [guidance, now, input.improvementId, input.expectedVersion],
    );
    if (result.affectedRows !== 1) return "VERSION_CONFLICT";
    const updated = await this.findItemById(input.improvementId);
    return updated ? itemView(updated) : null;
  }

  async reject(input: {
    ownerUserId: string;
    improvementId: number;
    expectedVersion: number;
    reasonCode: string;
    comment?: string | null;
  }): Promise<ImprovementView | null | "VERSION_CONFLICT" | "STATE_CONFLICT"> {
    const existing = await this.findItem(input.ownerUserId, input.improvementId);
    if (!existing) return null;
    if (existing.version !== input.expectedVersion) return "VERSION_CONFLICT";
    if (!["ACTIVE", "IN_PROGRESS"].includes(existing.status.toUpperCase())) return "STATE_CONFLICT";
    const now = this.db.dialect.now();
    const guidance = appendGovernanceEvent(existing.user_guidance, {
      title: "用户驳回",
      values: [
        ["原因", input.reasonCode],
        ["说明", input.comment],
        ["时间", now],
      ],
    });
    const result = await this.db.exec(
      `UPDATE insight_improvement_item
          SET status = 'ARCHIVED', user_guidance = ?, version = version + 1, gmt_modified = ?
        WHERE owner_user_id = ? AND id = ? AND version = ?
          AND source_type IN (${OWNER_VISIBLE_SOURCE_SQL})
          AND status IN ('ACTIVE', 'IN_PROGRESS')`,
      [guidance, now, input.ownerUserId, input.improvementId, input.expectedVersion],
    );
    if (result.affectedRows !== 1) return "VERSION_CONFLICT";
    const updated = await this.findItemById(input.improvementId);
    return updated ? itemView(updated) : null;
  }

  async rejectAdmin(input: {
    improvementId: number;
    expectedVersion: number;
    reasonCode: string;
    comment?: string | null;
    operatedBy: string;
  }): Promise<ImprovementView | null | "VERSION_CONFLICT" | "STATE_CONFLICT"> {
    const existing = await this.findItemById(input.improvementId);
    if (!existing) return null;
    if (existing.version !== input.expectedVersion) return "VERSION_CONFLICT";
    if (!OWNER_VISIBLE_SOURCE_TYPES.includes(existing.source_type as typeof OWNER_VISIBLE_SOURCE_TYPES[number])) {
      return "STATE_CONFLICT";
    }
    if (!["ACTIVE", "IN_PROGRESS"].includes(existing.status.toUpperCase())) return "STATE_CONFLICT";
    const now = this.db.dialect.now();
    const guidance = appendGovernanceEvent(existing.user_guidance, {
      title: "管理员驳回",
      values: [
        ["原因", input.reasonCode],
        ["说明", input.comment],
        ["操作人", input.operatedBy],
        ["时间", now],
      ],
    });
    const result = await this.db.exec(
      `UPDATE insight_improvement_item
          SET status = 'ARCHIVED', user_guidance = ?, version = version + 1, gmt_modified = ?
        WHERE id = ? AND version = ? AND status IN ('ACTIVE', 'IN_PROGRESS')
          AND source_type IN (${OWNER_VISIBLE_SOURCE_SQL})`,
      [guidance, now, input.improvementId, input.expectedVersion],
    );
    if (result.affectedRows !== 1) return "VERSION_CONFLICT";
    const updated = await this.findItemById(input.improvementId);
    return updated ? itemView(updated) : null;
  }

  async reopenAdmin(input: {
    improvementId: number;
    expectedVersion: number;
    reason?: string | null;
    operatedBy: string;
  }): Promise<ImprovementView | null | "VERSION_CONFLICT" | "STATE_CONFLICT"> {
    const existing = await this.findItemById(input.improvementId);
    if (!existing) return null;
    if (existing.version !== input.expectedVersion) return "VERSION_CONFLICT";
    if (existing.status.toUpperCase() !== "ARCHIVED") return "STATE_CONFLICT";
    const restoredSourceType = restoredGovernanceSourceType(existing.source_type)
      ?? (OWNER_VISIBLE_SOURCE_TYPES.includes(existing.source_type as typeof OWNER_VISIBLE_SOURCE_TYPES[number])
        ? existing.source_type as ImprovementSourceType
        : null);
    if (!restoredSourceType) return "STATE_CONFLICT";

    const now = this.db.dialect.now();
    const guidance = appendGovernanceEvent(existing.user_guidance, {
      title: "管理员恢复处理",
      values: [
        ["操作人", input.operatedBy],
        ["原因", input.reason],
        ["时间", now],
      ],
    });
    const result = await this.db.exec(
        `UPDATE insight_improvement_item
          SET source_type = ?, status = 'ACTIVE', user_guidance = ?,
              version = version + 1, gmt_modified = ?
        WHERE id = ? AND version = ? AND status = 'ARCHIVED'`,
      [restoredSourceType, guidance, now, input.improvementId, input.expectedVersion],
    );
    if (result.affectedRows !== 1) return "VERSION_CONFLICT";
    const updated = await this.findItemById(input.improvementId);
    return updated ? itemView(updated) : null;
  }

  async listRecentRejections(input: {
    days: number;
    ownerUserId?: string;
    botId?: string;
    sourceRuleId?: string;
    limit: number;
  }): Promise<ImprovementView[]> {
    const threshold = new Date(Date.now() - input.days * 24 * 60 * 60 * 1000);
    const conditions = [
      "status = 'ARCHIVED'",
      `source_type IN (${GOVERNANCE_SOURCE_SQL})`,
      "gmt_create >= ?",
    ];
    const params: unknown[] = [timestampForDb(this.db.dbType, threshold)];
    if (input.ownerUserId) { conditions.push("owner_user_id = ?"); params.push(input.ownerUserId); }
    if (input.botId) { conditions.push("bot_id = ?"); params.push(input.botId); }
    if (input.sourceRuleId) { conditions.push("source_rule_id = ?"); params.push(input.sourceRuleId); }
    params.push(input.limit);
    const rows = await this.db.query<ImprovementItemRow>(
      `SELECT * FROM insight_improvement_item
        WHERE ${conditions.join(" AND ")}
        ORDER BY gmt_create DESC, id DESC LIMIT ?`,
      params,
    );
    return rows.map(itemView);
  }

  async markGovernanceActionHandled(input: {
    improvementId: number;
    handledAt: Date;
    appliedEvolveTaskId?: string | null;
  }): Promise<ImprovementView | null | "VERSION_CONFLICT" | "STATE_CONFLICT"> {
    const existing = await this.findItemById(input.improvementId);
    if (!existing) return null;
    const existingView = itemView(existing);
    const directEvolution = existing.source_type === "ADMIN_RULE_DIRECT_EVOLUTION"
      || existing.source_type === "TRUSTED_RULE_DIRECT_EVOLUTION";
    if (
      existing.status.toUpperCase() !== "IN_PROGRESS"
      || existingView.adminReviewStatus !== "APPROVED"
      || !directEvolution
      || existingView.handledAt
    ) {
      return "STATE_CONFLICT";
    }

    const now = this.db.dialect.now();
    const handledAtIso = input.handledAt.toISOString();
    const guidance = appendPendingVerification(appendGovernanceEvent(existing.user_guidance, {
      title: "用户已处理",
      values: [
        ["时间", handledAtIso],
        ["方式", "AUTO_EVOLUTION"],
        ["Evolve任务", input.appliedEvolveTaskId],
      ],
    }));
    const result = await this.db.exec(
      `UPDATE insight_improvement_item
          SET user_guidance = ?, applied_evolve_task_id = COALESCE(?, applied_evolve_task_id),
              applied_by = ?, applied_at = ?, version = version + 1, gmt_modified = ?
        WHERE id = ? AND version = ? AND status = 'IN_PROGRESS'
          AND source_type IN ('ADMIN_RULE_DIRECT_EVOLUTION', 'TRUSTED_RULE_DIRECT_EVOLUTION')`,
      [
        guidance,
        input.appliedEvolveTaskId ?? null,
        "claw-evolve",
        timestampForDb(this.db.dbType, input.handledAt),
        now,
        input.improvementId,
        existing.version,
      ],
    );
    if (result.affectedRows !== 1) return "VERSION_CONFLICT";
    const updated = await this.findItemById(input.improvementId);
    return updated ? itemView(updated) : null;
  }

  async listGovernanceActions(input: {
    ownerUserId?: string;
    botId?: string;
    statuses?: string[];
    adminReviewStatuses?: string[];
    sourceRuleId?: string;
    since?: Date;
    limit: number;
    offset: number;
  }): Promise<{ total: number; items: ImprovementView[] }> {
    const conditions = [`i.source_type IN (${GOVERNANCE_SOURCE_SQL})`];
    const params: unknown[] = [];
    if (input.ownerUserId) {
      conditions.push("i.owner_user_id = ?");
      params.push(input.ownerUserId);
    }
    if (input.botId) {
      conditions.push("i.bot_id = ?");
      params.push(input.botId);
    }
    if (input.sourceRuleId) {
      conditions.push("i.source_rule_id = ?");
      params.push(input.sourceRuleId);
    }
    if (input.since) {
      conditions.push("i.gmt_modified >= ?");
      params.push(timestampForDb(this.db.dbType, input.since));
    }
    if (input.statuses?.length) {
      const statusConditions: string[] = [];
      for (const status of input.statuses) {
        if (status === "VERIFIED") {
          statusConditions.push("i.status = 'RESOLVED'");
        } else if (status === "AUTO_VERIFIED") {
          statusConditions.push("(i.status = 'RESOLVED' AND i.user_guidance LIKE ?)");
          params.push("%关闭来源：AUTO_VERIFIED%");
        } else {
          statusConditions.push("i.status = ?");
          params.push(status);
        }
      }
      conditions.push(`(${statusConditions.join(" OR ")})`);
    }
    if (input.adminReviewStatuses?.length) {
      const reviewConditions = input.adminReviewStatuses.map((status) => {
        if (status === "PENDING") return "i.status = 'PENDING_ADMIN'";
        if (status === "REJECTED") return `i.source_type IN (${REJECTED_GOVERNANCE_SOURCE_SQL})`;
        return `(i.source_type IN (${REVIEWED_GOVERNANCE_SOURCE_SQL}, ${TRUSTED_GOVERNANCE_SOURCE_SQL}) AND i.status <> 'PENDING_ADMIN')`;
      });
      conditions.push(`(${reviewConditions.join(" OR ")})`);
    }

    const where = conditions.join(" AND ");
    const countRow = (await this.db.query<{ item_count: number | string }>(
      `SELECT COUNT(*) AS item_count FROM insight_improvement_item i WHERE ${where}`,
      params,
    ))[0];
    const rows = await this.db.query<ImprovementItemRow>(
      `SELECT i.*,
              (SELECT l.evolve_task_id FROM insight_improvement_evolve_link l
                WHERE l.improvement_id = i.id ORDER BY l.id DESC LIMIT 1) AS latest_evolve_task_id,
              (SELECT t.status FROM insight_improvement_evolve_link l
                LEFT JOIN ce_tasks t ON t.task_id = l.evolve_task_id
                WHERE l.improvement_id = i.id ORDER BY l.id DESC LIMIT 1) AS latest_evolve_task_status
         FROM insight_improvement_item i
        WHERE ${where}
        ORDER BY i.gmt_modified DESC, i.id DESC
        LIMIT ? OFFSET ?`,
      [...params, input.limit, input.offset],
    );
    return {
      total: Number(countRow?.item_count ?? 0) || 0,
      items: rows.map(itemView),
    };
  }

  async listVerificationCandidates(limit: number): Promise<ImprovementView[]> {
    const rows = await this.db.query<ImprovementItemRow>(
      `SELECT * FROM insight_improvement_item
        WHERE status = 'IN_PROGRESS'
        ORDER BY gmt_modified ASC, id ASC LIMIT ?`,
      [Math.max(limit * 5, limit)],
    );
    return rows.map(itemView).filter((item) => Boolean(item.handledAt)).slice(0, limit);
  }

  async listOpenVerificationCandidates(limit: number): Promise<ImprovementView[]> {
    const rows = await this.db.query<ImprovementItemRow>(
      `SELECT i.*,
              (SELECT l.evolve_task_id FROM insight_improvement_evolve_link l
                WHERE l.improvement_id = i.id ORDER BY l.id DESC LIMIT 1) AS latest_evolve_task_id,
              (SELECT t.status FROM insight_improvement_evolve_link l
                LEFT JOIN ce_tasks t ON t.task_id = l.evolve_task_id
                WHERE l.improvement_id = i.id ORDER BY l.id DESC LIMIT 1) AS latest_evolve_task_status
         FROM insight_improvement_item i
        WHERE i.status IN ('ACTIVE', 'IN_PROGRESS')
          AND i.source_type IN (${OWNER_VISIBLE_SOURCE_SQL})
        ORDER BY i.gmt_modified ASC, i.id ASC LIMIT ?`,
      [Math.max(limit * 10, limit)],
    );
    return rows
      .map(itemView)
      // An IN_PROGRESS item with handledAt belongs to the normal queue. ACTIVE
      // items are intentionally included, including items reopened after recurrence.
      .filter((item) => item.status.toUpperCase() === "ACTIVE" || !item.handledAt)
      .slice(0, limit);
  }

  async getRuleEvolutionStats(sourceRuleId: string): Promise<{
    successCount: number;
    ownerCount: number;
    botCount: number;
    lastVerifiedAt: number | string | null;
  }> {
    const row = (await this.db.query<{
      success_count: number;
      owner_count: number;
      bot_count: number;
      last_verified_at: number | string | null;
    }>(
      `SELECT COUNT(*) AS success_count,
              COUNT(DISTINCT owner_user_id) AS owner_count,
              COUNT(DISTINCT bot_id) AS bot_count,
              MAX(gmt_modified) AS last_verified_at
         FROM insight_improvement_item
        WHERE source_rule_id = ?
          AND status = 'RESOLVED'
          AND source_type IN ('ADMIN_RULE_DIRECT_EVOLUTION', 'TRUSTED_RULE_DIRECT_EVOLUTION')
          AND user_guidance LIKE '%[自动验证]%'
          AND user_guidance LIKE '%状态：VERIFIED%'`,
      [sourceRuleId],
    ))[0];
    return {
      successCount: Number(row?.success_count ?? 0),
      ownerCount: Number(row?.owner_count ?? 0),
      botCount: Number(row?.bot_count ?? 0),
      lastVerifiedAt: row?.last_verified_at ?? null,
    };
  }

  async getAutoClosureRateByDate(scope: InsightQueryScope): Promise<Record<string, number | null>> {
    // 只统计真正执行过自动修复、且已经完成一次有结论的 Agent 验收的改进项。
    // 同一改进项多次重试只取当前最新一次验收结果，避免重复放大。
    const conditions = [
      "i.applied_at IS NOT NULL",
      "i.source_type IN ('ADMIN_RULE_DIRECT_EVOLUTION', 'TRUSTED_RULE_DIRECT_EVOLUTION')",
      "i.user_guidance IS NOT NULL",
    ];
    const params: unknown[] = [];
    if (scope.userId !== "*") {
      conditions.push("i.owner_user_id = ?");
      params.push(scope.userId);
    }
    if (scope.botId) {
      conditions.push("i.bot_id = ?");
      params.push(scope.botId);
    }

    const rows = await this.db.query<ImprovementItemRow>(
      `SELECT i.*
         FROM insight_improvement_item i
        WHERE ${conditions.join(" AND ")}
        ORDER BY i.id ASC`,
      params,
    );
    const byDate = new Map<string, { observed: number; closed: number }>();
    const from = scope.from?.replaceAll("-", "").slice(0, 8);
    const to = scope.to?.replaceAll("-", "").slice(0, 8);

    for (const row of rows) {
      const item = itemView(row);
      if (item.actionType !== "DIRECT_EVOLUTION" || !item.appliedAt || !item.verificationLastCheckedAt) continue;

      // INSUFFICIENT_DATA 不代表验收完成；强制验收也不计入自动闭环。
      const hasOutcome = item.verificationStatus === "STILL_PRESENT"
        || (item.verificationStatus === "VERIFIED" && item.resolvedSource === "AUTO_VERIFIED");
      if (!hasOutcome) continue;
      const date = compactDate(item.verificationLastCheckedAt);
      if (!date || (from && date < from) || (to && date > to)) continue;

      const stats = byDate.get(date) ?? { observed: 0, closed: 0 };
      stats.observed += 1;
      if (item.verificationStatus === "VERIFIED" && item.resolvedSource === "AUTO_VERIFIED") {
        stats.closed += 1;
      }
      byDate.set(date, stats);
    }

    return Object.fromEntries(
      [...byDate.entries()].map(([date, stats]) => [
        date, stats.observed > 0 ? Number((stats.closed / stats.observed).toFixed(4)) : null,
      ]),
    );
  }

  async listEffectEvents(scope: InsightQueryScope): Promise<InsightGovernanceEvent[]> {
    const conditions = [
      `i.source_type IN (${OWNER_VISIBLE_SOURCE_SQL})`,
      "i.status <> 'PENDING_ADMIN'",
      "(i.status IN ('IN_PROGRESS', 'RESOLVED') OR i.applied_at IS NOT NULL OR i.user_guidance LIKE ?)",
    ];
    const params: unknown[] = ["%[用户已处理]%"];
    if (scope.userId !== "*") {
      conditions.push("i.owner_user_id = ?");
      params.push(scope.userId);
    }
    if (scope.botId) {
      conditions.push("i.bot_id = ?");
      params.push(scope.botId);
    }

    const rows = await this.db.query<ImprovementItemRow>(
      `SELECT i.*,
              (SELECT l.evolve_task_id
                 FROM insight_improvement_evolve_link l
                WHERE l.improvement_id = i.id
                ORDER BY l.id DESC LIMIT 1) AS latest_evolve_task_id,
              (SELECT t.status
                 FROM insight_improvement_evolve_link l
                 LEFT JOIN ce_tasks t ON t.task_id = l.evolve_task_id
                WHERE l.improvement_id = i.id
                ORDER BY l.id DESC LIMIT 1) AS latest_evolve_task_status
         FROM insight_improvement_item i
        WHERE ${conditions.join(" AND ")}
        ORDER BY COALESCE(i.applied_at, i.gmt_modified) ASC, i.id ASC
        LIMIT 500`,
      params,
    );

    const from = scope.from?.replaceAll("-", "").slice(0, 8);
    const to = scope.to?.replaceAll("-", "").slice(0, 8);
    return rows
      .map(itemView)
      .map(effectEvent)
      .filter((event): event is InsightGovernanceEvent => Boolean(event))
      .filter((event) => {
        const date = compactDate(event.effectiveAt);
        return Boolean(date) && (!from || date! >= from) && (!to || date! <= to);
      })
      .sort((left, right) => (epochSeconds(left.effectiveAt) ?? 0) - (epochSeconds(right.effectiveAt) ?? 0)
        || left.improvementId - right.improvementId);
  }

  async recordVerification(input: {
    improvementId: number;
    expectedVersion: number;
    outcome: "DISAPPEARED" | "STILL_PRESENT" | "INSUFFICIENT_DATA";
    newSessionCount: number;
    lastRecurrenceAt?: string | null;
    overrideActionType?: "ASSIGN_OWNER" | null;
  }): Promise<ImprovementView | null | "VERSION_CONFLICT" | "STATE_CONFLICT" | "TOO_EARLY"> {
    const existing = await this.findItemById(input.improvementId);
    if (!existing) return null;
    if (existing.version !== input.expectedVersion) return "VERSION_CONFLICT";
    const existingView = itemView(existing);
    const currentStatus = existing.status.toUpperCase();
    const pendingVerification = currentStatus === "IN_PROGRESS" && Boolean(existingView.handledAt);
    const resolvedRecurrence = currentStatus === "RESOLVED"
      && isGovernanceSourceType(existing.source_type)
      && input.outcome === "STILL_PRESENT";
    if (!pendingVerification && !resolvedRecurrence) return "STATE_CONFLICT";
    if (pendingVerification && input.outcome === "DISAPPEARED"
      && !hasElapsed(existingView.handledAt, STANDARD_VERIFICATION_WAIT_SECONDS)) {
      return "TOO_EARLY";
    }
    const overriddenSourceType = input.overrideActionType === "ASSIGN_OWNER"
      ? assignOwnerGovernanceSourceType(existing.source_type)
      : existing.source_type;
    if (input.overrideActionType && !overriddenSourceType) return "STATE_CONFLICT";
    const now = this.db.dialect.now();
    const verificationStatus = input.outcome === "DISAPPEARED"
      ? "VERIFIED"
      : input.outcome === "STILL_PRESENT"
        ? "STILL_PRESENT"
        : "INSUFFICIENT_DATA";
    const nextStatus = input.overrideActionType === "ASSIGN_OWNER"
      ? "ACTIVE"
      : resolvedRecurrence
        ? "ACTIVE"
        : input.outcome === "DISAPPEARED"
          ? "RESOLVED"
          : existing.status;
    const guidance = appendGovernanceEvent(existing.user_guidance, {
      title: "自动验证",
      values: [
        ["状态", verificationStatus],
        ["检查时间", now],
        ["新Session数", input.newSessionCount],
        ["最后再现", input.lastRecurrenceAt],
        ["修复方式", input.overrideActionType],
        ["关闭来源", input.outcome === "DISAPPEARED" ? "AUTO_VERIFIED" : null],
      ],
    });
    const result = await this.db.exec(
      `UPDATE insight_improvement_item
          SET status = ?, source_type = ?, user_guidance = ?, version = version + 1, gmt_modified = ?
        WHERE id = ? AND version = ? AND status = ?`,
      [
        nextStatus,
        overriddenSourceType,
        guidance,
        now,
        input.improvementId,
        input.expectedVersion,
        existing.status,
      ],
    );
    if (result.affectedRows !== 1) return "VERSION_CONFLICT";
    const updated = await this.findItemById(input.improvementId);
    return updated ? itemView(updated) : null;
  }

  async forceResolveVerification(input: {
    improvementId: number;
    expectedVersion: number;
    newSessionCount: number;
    reason: string;
    resolvedSource: "FORCE_VERIFIED" | "TEST_FORCE_VERIFIED";
    operatedBy: string;
  }): Promise<ImprovementView | null | "VERSION_CONFLICT" | "STATE_CONFLICT"> {
    const existing = await this.findItemById(input.improvementId);
    if (!existing) return null;
    if (existing.version !== input.expectedVersion) return "VERSION_CONFLICT";
    const existingView = itemView(existing);
    const currentStatus = existing.status.toUpperCase();
    const hasRepairBoundary = Boolean(existingView.handledAt || existing.applied_at);
    if (currentStatus !== "IN_PROGRESS" || !hasRepairBoundary) return "STATE_CONFLICT";

    const now = this.db.dialect.now();
    const guidance = appendGovernanceEvent(existing.user_guidance, {
      title: "强制验收",
      values: [
        ["状态", "VERIFIED"],
        ["检查时间", now],
        ["新Session数", input.newSessionCount],
        ["原因", input.reason],
        ["操作人", input.operatedBy],
        ["关闭来源", input.resolvedSource],
      ],
    });
    const result = await this.db.exec(
      `UPDATE insight_improvement_item
          SET status = 'RESOLVED', user_guidance = ?, version = version + 1, gmt_modified = ?
        WHERE id = ? AND version = ? AND status = 'IN_PROGRESS'`,
      [guidance, now, input.improvementId, input.expectedVersion],
    );
    if (result.affectedRows !== 1) return "VERSION_CONFLICT";
    const updated = await this.findItemById(input.improvementId);
    return updated ? itemView(updated) : null;
  }

  async recordOpenVerification(input: {
    improvementId: number;
    expectedVersion: number;
    outcome: "DISAPPEARED" | "STILL_PRESENT" | "INSUFFICIENT_DATA";
    newSessionCount: number;
    lastRecurrenceAt?: string | null;
    overrideActionType?: "ASSIGN_OWNER" | null;
  }): Promise<ImprovementView | null | "VERSION_CONFLICT" | "STATE_CONFLICT" | "TOO_EARLY"> {
    const existing = await this.findItemById(input.improvementId);
    if (!existing) return null;
    if (existing.version !== input.expectedVersion) return "VERSION_CONFLICT";
    if (!OWNER_VISIBLE_SOURCE_TYPES.includes(existing.source_type as typeof OWNER_VISIBLE_SOURCE_TYPES[number])) {
      return "STATE_CONFLICT";
    }
    const existingView = itemView(existing);
    const currentStatus = existing.status.toUpperCase();
    if (![
      "ACTIVE",
      "IN_PROGRESS",
    ].includes(currentStatus) || (currentStatus === "IN_PROGRESS" && existingView.handledAt)) {
      return "STATE_CONFLICT";
    }
    if (input.outcome === "DISAPPEARED"
      && !hasElapsed(existing.gmt_modified, OPEN_VERIFICATION_WAIT_SECONDS)) {
      return "TOO_EARLY";
    }
    const overriddenSourceType = input.overrideActionType === "ASSIGN_OWNER"
      ? assignOwnerGovernanceSourceType(existing.source_type)
      : existing.source_type;
    if (input.overrideActionType && !overriddenSourceType) return "STATE_CONFLICT";
    const now = this.db.dialect.now();
    const verificationStatus = input.outcome === "DISAPPEARED"
      ? "VERIFIED"
      : input.outcome === "STILL_PRESENT"
        ? "STILL_PRESENT"
        : "INSUFFICIENT_DATA";
    const nextStatus = input.overrideActionType === "ASSIGN_OWNER"
      ? "ACTIVE"
      : input.outcome === "DISAPPEARED"
        ? "RESOLVED"
        : existing.status;
    const guidance = appendGovernanceEvent(existing.user_guidance, {
      title: "自动验证",
      values: [
        ["状态", verificationStatus],
        ["检查时间", now],
        ["新Session数", input.newSessionCount],
        ["最后再现", input.lastRecurrenceAt],
        ["修复方式", input.overrideActionType],
        ["验收入口", "OPEN_ITEM"],
        ["关闭来源", input.outcome === "DISAPPEARED" ? "AUTO_VERIFIED" : null],
      ],
    });
    const result = await this.db.exec(
      `UPDATE insight_improvement_item
          SET status = ?, source_type = ?, user_guidance = ?, version = version + 1, gmt_modified = ?
        WHERE id = ? AND version = ? AND status = ?`,
      [
        nextStatus,
        overriddenSourceType,
        guidance,
        now,
        input.improvementId,
        input.expectedVersion,
        existing.status,
      ],
    );
    if (result.affectedRows !== 1) return "VERSION_CONFLICT";
    const updated = await this.findItemById(input.improvementId);
    return updated ? itemView(updated) : null;
  }

  async update(
    ownerUserId: string,
    improvementId: number,
    input: {
      title?: string;
      userGuidance?: string | null;
      status?: "ACTIVE" | "IN_PROGRESS" | "RESOLVED" | "ARCHIVED";
      expectedVersion?: number;
    },
  ): Promise<ImprovementView | null | "VERSION_CONFLICT"> {
    const existing = await this.findItem(ownerUserId, improvementId);
    if (!existing) return null;
    if (input.expectedVersion !== undefined && existing.version !== input.expectedVersion) {
      return "VERSION_CONFLICT";
    }
    const title = input.title ?? existing.title;
    const userGuidance = input.userGuidance === undefined
      ? existing.user_guidance
      : isGovernanceSourceType(existing.source_type)
        ? appendGovernanceEvent(existing.user_guidance, {
            title: "用户更新",
            values: [["说明", input.userGuidance]],
          })
        : input.userGuidance;
    const status = input.status ?? existing.status;
    const now = this.db.dialect.now();
    const result = await this.db.exec(
      `UPDATE insight_improvement_item
       SET title = ?, user_guidance = ?, status = ?, version = version + 1, gmt_modified = ?
       WHERE owner_user_id = ? AND id = ? AND version = ?`,
      [title, userGuidance, status, now, ownerUserId, improvementId, existing.version],
    );
    if (result.affectedRows !== 1) return "VERSION_CONFLICT";
    const updated = await this.findItem(ownerUserId, improvementId);
    return updated ? itemView(updated) : null;
  }

  async recordSelfRepairHandoff(
    ownerUserId: string,
    improvementId: number,
    expectedVersion: number,
  ): Promise<ImprovementView | null | "VERSION_CONFLICT" | "STATE_CONFLICT"> {
    const existing = await this.findItem(ownerUserId, improvementId);
    if (!existing) return null;
    if (existing.version !== expectedVersion) return "VERSION_CONFLICT";
    if (!["ACTIVE", "IN_PROGRESS"].includes(existing.status.toUpperCase())) return "STATE_CONFLICT";
    const now = this.db.dialect.now();
    const guidance = appendPendingVerification(appendGovernanceEvent(existing.user_guidance, {
      title: "用户已处理",
      values: [["时间", now], ["方式", "AGENT_HANDOFF"]],
    }));
    const result = await this.db.exec(
      `UPDATE insight_improvement_item
          SET status = 'IN_PROGRESS', user_guidance = ?, version = version + 1, gmt_modified = ?
        WHERE owner_user_id = ? AND id = ? AND version = ?
          AND source_type IN (${OWNER_VISIBLE_SOURCE_SQL})
          AND status IN ('ACTIVE', 'IN_PROGRESS')`,
      [guidance, now, ownerUserId, improvementId, expectedVersion],
    );
    if (result.affectedRows !== 1) return "VERSION_CONFLICT";
    const updated = await this.findItem(ownerUserId, improvementId);
    return updated ? itemView(updated) : null;
  }

  async linkEvolveTask(input: {
    improvementId: number;
    ownerUserId: string;
    evolveTaskId: string;
    requestId: string;
    createdBy: string;
  }): Promise<void> {
    await this.db.transaction(async (tx) => {
      const now = tx.dialect.now();
      const stateUpdate = await tx.exec(
        `UPDATE insight_improvement_item
            SET status = 'IN_PROGRESS', version = version + 1, gmt_modified = ?
          WHERE id = ? AND owner_user_id = ?
            AND source_type IN (${OWNER_VISIBLE_SOURCE_SQL})`,
        [now, input.improvementId, input.ownerUserId],
      );
      if (stateUpdate.affectedRows !== 1) {
        throw new ImprovementEvolveLinkConflictError("改进项不可见或负责人已变化，请刷新后重试");
      }
      await tx.exec(
        `INSERT INTO insight_improvement_evolve_link
         (improvement_id, evolve_task_id, request_id, created_by, gmt_create, gmt_modified)
         VALUES (?, ?, ?, ?, ?, ?)`,
        [input.improvementId, input.evolveTaskId, input.requestId, input.createdBy, now, now],
      );
    });
  }

  async findEvolveLinkByRequest(
    improvementId: number,
    requestId: string,
  ): Promise<ImprovementEvolveLinkRow | null> {
    return (
      (
        await this.db.query<ImprovementEvolveLinkRow>(
          `SELECT l.improvement_id, l.evolve_task_id, l.request_id,
              l.created_by, l.gmt_create, t.status AS task_status, t.task_name
       FROM insight_improvement_evolve_link l
       LEFT JOIN ce_tasks t ON t.task_id = l.evolve_task_id
       WHERE l.improvement_id = ? AND l.request_id = ?
       LIMIT 1`,
          [improvementId, requestId],
        )
      )[0] ?? null
    );
  }

  async findLatestEvolveLinkByImprovementId(
    improvementId: number,
  ): Promise<ImprovementEvolveLinkRow | null> {
    return (
      (
        await this.db.query<ImprovementEvolveLinkRow>(
          `SELECT l.improvement_id, l.evolve_task_id, l.request_id,
              l.created_by, l.gmt_create, t.status AS task_status, t.task_name
         FROM insight_improvement_evolve_link l
         LEFT JOIN ce_tasks t ON t.task_id = l.evolve_task_id
        WHERE l.improvement_id = ?
        ORDER BY l.id DESC
        LIMIT 1`,
          [improvementId],
        )
      )[0] ?? null
    );
  }

  async findEvolveLinkByTaskId(
    evolveTaskId: string,
  ): Promise<Pick<ImprovementEvolveLinkRow, "improvement_id" | "request_id" | "created_by"> | null> {
    return (
      (
        await this.db.query<Pick<ImprovementEvolveLinkRow, "improvement_id" | "request_id" | "created_by">>(
          `SELECT improvement_id, request_id, created_by
             FROM insight_improvement_evolve_link
            WHERE evolve_task_id = ?
            LIMIT 1`,
          [evolveTaskId],
        )
      )[0] ?? null
    );
  }

  async resolveFromApply(input: {
    improvementId: number;
    applyTaskId: string;
    requestId: string;
    appliedBy: string;
  }): Promise<
    | { outcome: "UPDATED" | "IDEMPOTENT"; item: ImprovementView }
    | { outcome: "NOT_FOUND" }
    | { outcome: "STATE_CONFLICT"; currentStatus: string }
  > {
    return this.db.transaction(async (tx) => {
      const findCurrent = async () => (
        await tx.query<ImprovementItemRow>(
          "SELECT * FROM insight_improvement_item WHERE id = ? LIMIT 1",
          [input.improvementId],
        )
      )[0] ?? null;

      const isSameApply = (item: ImprovementItemRow) =>
        item.applied_evolve_task_id === input.applyTaskId
        && item.apply_request_id === input.requestId;

      const current = await findCurrent();
      if (!current) return { outcome: "NOT_FOUND" };
      if (isSameApply(current)) {
        return { outcome: "IDEMPOTENT", item: itemView(current) };
      }
      const currentView = itemView(current);
      const retryAfterVerification = ["STILL_PRESENT", "INSUFFICIENT_DATA"].includes(currentView.verificationStatus);
      if ((current.applied_evolve_task_id || current.apply_request_id) && !retryAfterVerification) {
        return {
          outcome: "STATE_CONFLICT",
          currentStatus: current.status.toUpperCase(),
        };
      }
      if (current.status.toUpperCase() !== "IN_PROGRESS") {
        return {
          outcome: "STATE_CONFLICT",
          currentStatus: current.status.toUpperCase(),
        };
      }

      const now = tx.dialect.now();
      const guidance = appendPendingVerification(appendGovernanceEvent(current.user_guidance, {
        title: "用户已处理",
        values: [["时间", now], ["方式", retryAfterVerification ? "AUTO_EVOLUTION_RETRY" : "AUTO_EVOLUTION"]],
      }));
      const update = await tx.exec(
        `UPDATE insight_improvement_item
            SET status = 'IN_PROGRESS', user_guidance = ?, applied_evolve_task_id = ?, apply_request_id = ?,
                applied_by = ?, applied_at = ?, version = version + 1, gmt_modified = ?
          WHERE id = ? AND status = 'IN_PROGRESS'`,
        [
          guidance,
          input.applyTaskId,
          input.requestId,
          input.appliedBy,
          now,
          now,
          input.improvementId,
        ],
      );
      const latest = await findCurrent();
      if (update.affectedRows === 1 && latest) {
        return { outcome: "UPDATED", item: itemView(latest) };
      }
      if (latest && isSameApply(latest)) {
        return { outcome: "IDEMPOTENT", item: itemView(latest) };
      }
      return {
        outcome: "STATE_CONFLICT",
        currentStatus: latest?.status.toUpperCase() ?? "UNKNOWN",
      };
    });
  }
}
