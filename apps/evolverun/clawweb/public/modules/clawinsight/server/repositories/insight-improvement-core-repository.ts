import type { IDatabase } from "@avernet/clawweb-shared/server/db";

import type { ImprovementDetail, ImprovementView } from "../services/insight/contracts.js";
import { OWNER_VISIBLE_SOURCE_TYPES, actionTypeFromSourceType, appendGovernanceEvent, buildGovernanceGuidance, isGovernanceSourceType, isRejectedGovernanceSourceType, rejectedGovernanceSourceType, restoredGovernanceSourceType, type ImprovementSourceType } from "../services/insight/governance-item.js";

import { ImprovementItemRow, ImprovementEvidenceRow, ImprovementEvolveLinkRow, GOVERNANCE_SOURCE_SQL, OWNER_VISIBLE_SOURCE_SQL, TRUSTED_GOVERNANCE_SOURCE_SQL, REVIEWED_GOVERNANCE_SOURCE_SQL, REJECTED_GOVERNANCE_SOURCE_SQL, encodeImprovementCursor, decodeImprovementCursor, CreateImprovementInput, itemView, appendPendingVerification, evidenceView, evolveLinkView } from "./insight-improvement-model.js";

export class InsightImprovementCoreRepository {
  constructor(protected readonly db: IDatabase) {}

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
    // 无论治理/非治理源：Owner 标记已修复即写入「用户已处理」+「自动验证/PENDING」，
    // 使 handledAt 有值、可进标准验收队列(listVerificationCandidates 按 handledAt 过滤)。
    const guidance = appendPendingVerification(appendGovernanceEvent(existing.user_guidance, {
      title: "用户已处理",
      values: [["时间", now]],
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

}
