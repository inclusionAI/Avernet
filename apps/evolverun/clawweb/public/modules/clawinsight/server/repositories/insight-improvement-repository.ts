import type { ImprovementView, InsightGovernanceEvent, InsightQueryScope } from "../services/insight/contracts.js";
import { OWNER_VISIBLE_SOURCE_TYPES, assignOwnerGovernanceSourceType, appendGovernanceEvent, isGovernanceSourceType } from "../services/insight/governance-item.js";

import { ImprovementItemRow, ImprovementEvolveLinkRow, timestampForDb, GOVERNANCE_SOURCE_SQL, OWNER_VISIBLE_SOURCE_SQL, TRUSTED_GOVERNANCE_SOURCE_SQL, REVIEWED_GOVERNANCE_SOURCE_SQL, REJECTED_GOVERNANCE_SOURCE_SQL, REJECTABLE_SOURCE_SQL, OWNER_REJECTION_EVENT_SQL, ADMIN_REJECTION_EVENT_SQL, ANY_REJECTION_EVENT_SQL, STANDARD_VERIFICATION_WAIT_SECONDS, OPEN_VERIFICATION_WAIT_SECONDS, epochSeconds, hasElapsed, compactDate, effectEvent, ImprovementEvolveLinkConflictError, itemView, appendPendingVerification } from "./insight-improvement-model.js";

import { InsightImprovementCoreRepository } from "./insight-improvement-core-repository.js";
export * from "./insight-improvement-model.js";

export class InsightImprovementRepository extends InsightImprovementCoreRepository {
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

  /** 全部已驳回改进项（用户驳回 + 管理员驳回），跨 owner、跨来源类型。 */
  async listAllRejections(input: {
    since: Date;
    ownerUserId?: string;
    botId?: string;
    sourceRuleId?: string;
    rejectedBy?: "OWNER" | "ADMIN";
    limit: number;
    offset: number;
  }): Promise<{ total: number; items: ImprovementView[] }> {
    const conditions = [
      "i.status = 'ARCHIVED'",
      `i.source_type IN (${REJECTABLE_SOURCE_SQL})`,
      ANY_REJECTION_EVENT_SQL,
      "i.gmt_modified >= ?",
    ];
    const params: unknown[] = [timestampForDb(this.db.dbType, input.since)];
    if (input.ownerUserId) { conditions.push("i.owner_user_id = ?"); params.push(input.ownerUserId); }
    if (input.botId) { conditions.push("i.bot_id = ?"); params.push(input.botId); }
    if (input.sourceRuleId) { conditions.push("i.source_rule_id = ?"); params.push(input.sourceRuleId); }
    if (input.rejectedBy === "OWNER") conditions.push(OWNER_REJECTION_EVENT_SQL);
    if (input.rejectedBy === "ADMIN") conditions.push(ADMIN_REJECTION_EVENT_SQL);
    const where = conditions.join(" AND ");
    const countRow = (await this.db.query<{ item_count: number | string }>(
      `SELECT COUNT(*) AS item_count FROM insight_improvement_item i WHERE ${where}`,
      params,
    ))[0];
    const rows = await this.db.query<ImprovementItemRow>(
      `SELECT i.* FROM insight_improvement_item i
        WHERE ${where}
        ORDER BY i.gmt_modified DESC, i.id DESC LIMIT ? OFFSET ?`,
      [...params, input.limit, input.offset],
    );
    return { total: Number(countRow?.item_count ?? 0) || 0, items: rows.map(itemView) };
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
    const rows = await this.db.query<ImprovementItemRow>(
      `SELECT * FROM insight_improvement_item
       WHERE source_rule_id = ? AND status = 'RESOLVED'
         AND source_type IN ('ADMIN_RULE_DIRECT_EVOLUTION', 'TRUSTED_RULE_DIRECT_EVOLUTION')`,
      [sourceRuleId],
    );
    // Inspect the current projection, not historical VERIFIED substrings in an audit log.
    const verified = rows.map(itemView).filter(item => item.verificationStatus === "VERIFIED"
      && (item.resolvedSource === "AUTO_VERIFIED" || (item.resolvedSource === null && item.userGuidance?.includes("[自动验证]"))));
    const times = verified.map(item => item.verificationLastCheckedAt).filter(value => value !== null);
    times.sort((a, b) => (epochSeconds(b) ?? 0) - (epochSeconds(a) ?? 0));
    return {
      successCount: verified.length,
      ownerCount: new Set(verified.map(item => item.ownerUserId)).size,
      botCount: new Set(verified.map(item => item.botId)).size,
      lastVerifiedAt: times[0] ?? null,
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
    allowZeroSession?: boolean;
    lastRecurrenceAt?: string | null;
    overrideActionType?: "ASSIGN_OWNER" | null;
  }): Promise<ImprovementView | null | "VERSION_CONFLICT" | "STATE_CONFLICT" | "TOO_EARLY" | "RECURRENCE_FOUND"> {
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
    if (pendingVerification && input.outcome === "DISAPPEARED") {
      if (!hasElapsed(existingView.handledAt, STANDARD_VERIFICATION_WAIT_SECONDS)) return "TOO_EARLY";
      if (input.newSessionCount === 0 && (!input.allowZeroSession || input.lastRecurrenceAt)) return "RECURRENCE_FOUND";
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
    allowZeroSession?: boolean;
    lastRecurrenceAt?: string | null;
    overrideActionType?: "ASSIGN_OWNER" | null;
  }): Promise<ImprovementView | null | "VERSION_CONFLICT" | "STATE_CONFLICT" | "TOO_EARLY" | "RECURRENCE_FOUND"> {
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
    if (input.outcome === "DISAPPEARED") {
      if (!hasElapsed(existing.gmt_modified, OPEN_VERIFICATION_WAIT_SECONDS)) return "TOO_EARLY";
      if (input.newSessionCount === 0 && Boolean(input.lastRecurrenceAt)) return "RECURRENCE_FOUND";
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
