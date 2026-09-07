import { createHash } from "node:crypto";
import type {
  InsightQueryScope,
  FailureTaskDetail,
  FailureTaskQuery,
  ImprovementDetail,
  ImprovementEvidenceSnapshot,
  ImprovementView,
  SessionEvidence,
  TimelinePage,
} from "./contracts.js";
import { INSIGHT_CONTRACT_VERSION } from "./contracts.js";
import type { EvidenceProvider } from "./providers/evidence-provider.js";
import {
  InsightCursorError,
  InsightDataNotReadyError,
  type InsightReadProvider,
} from "./providers/insight-read-provider.js";
import { buildTimelineBlocks, toTimelineSummary } from "./timeline.js";
import type { InsightImprovementRepository } from "../../repositories/insight-improvement-repository.js";
import type { DingTalkSender } from "./dingtalk-sender.js";
import { buildSelfRepairMarkdown, buildEvidenceAccessUrl, type SelfRepairHandoffEvidence } from "./self-repair-handoff.js";
import { governanceSourceType } from "./governance-item.js";

export class InsightValidationError extends Error {
  readonly code = "INVALID_ARGUMENT";
}

export class InsightConflictError extends Error {
  constructor(
    message: string,
    readonly code: string = "CONFLICT",
  ) {
    super(message);
  }
}

export class InsightNotFoundError extends Error {
  readonly code = "NOT_FOUND";
}

export class InsightUnauthorizedError extends Error {
  readonly code = "UNAUTHORIZED";
}

type SelectedTask = { sessionId: string; taskIndex: number };
type TimelineCursor = { offset: number; taskKey: string; etag: string };
type ResolveTaskOptions = {
  evidenceCache?: Map<string, Promise<SessionEvidence>>;
  anchorTaskIndex?: number;
};

function nullableString(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  return normalized || null;
}

function nullableNumber(value: unknown): number | null {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function fingerprint(value: unknown): string {
  return createHash("sha256").update(JSON.stringify(value)).digest("hex");
}

function encodeTimelineCursor(value: TimelineCursor): string {
  return Buffer.from(JSON.stringify(value), "utf8").toString("base64url");
}

function decodeTimelineCursor(raw: string): TimelineCursor {
  try {
    const value = JSON.parse(
      Buffer.from(raw, "base64url").toString("utf8"),
    ) as Partial<TimelineCursor>;
    if (
      !Number.isInteger(value.offset) ||
      Number(value.offset) < 0 ||
      !value.taskKey ||
      !value.etag
    ) {
      throw new Error("invalid cursor");
    }
    return value as TimelineCursor;
  } catch {
    throw new InsightCursorError("时间线游标无效或已过期");
  }
}

const GOVERNANCE_ACTION_STATUSES = new Set([
  "PENDING_ADMIN",
  "ACTIVE",
  "IN_PROGRESS",
  "RESOLVED",
  "ARCHIVED",
  // Query aliases for the resolved verification outcome/source.
  "VERIFIED",
  "AUTO_VERIFIED",
]);
const GOVERNANCE_ACTION_REVIEW_STATUSES = new Set(["PENDING", "APPROVED", "REJECTED"]);
const GOVERNANCE_ACTION_DEFAULT_FIELDS = [
  "improvementId",
  "ownerUserId",
  "botOwnerUserId",
  "botId",
  "title",
  "rootCauseSummary",
  "assignmentReason",
  "suggestedAction",
  "sourceRuleId",
  "actionType",
  "status",
  "adminReviewStatus",
  "adminReviewReason",
  "adminReviewedBy",
  "adminReviewedAt",
  "handledAt",
  "verificationStatus",
  "verificationLastCheckedAt",
  "verificationNewSessionCount",
  "verificationLastRecurrenceAt",
  "resolvedSource",
  "resolvedAt",
  "evidenceCount",
  "sessionCount",
  "dataStartTime",
  "dataEndTime",
  "createdBy",
  "createdAt",
  "updatedAt",
  "version",
] as const;
const GOVERNANCE_ACTION_FIELDS = new Set<string>(["id", ...GOVERNANCE_ACTION_DEFAULT_FIELDS]);

function governanceTimestamp(value: unknown): string | null {
  if (value == null || value === "") return null;
  if (value instanceof Date) return value.toISOString();
  if (typeof value === "number") return new Date(value * 1000).toISOString();
  const text = String(value).trim();
  if (!text) return null;
  if (/^\d+$/.test(text)) return new Date(Number(text) * 1000).toISOString();
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(text)
    ? `${text.replace(" ", "T")}+08:00`
    : text;
  const parsed = new Date(normalized);
  return Number.isFinite(parsed.getTime()) ? parsed.toISOString() : text;
}

function governanceActionRecord(item: ImprovementView): Record<string, unknown> {
  const resolvedAt = item.status.toUpperCase() === "RESOLVED"
    ? governanceTimestamp(item.verificationLastCheckedAt ?? item.appliedAt ?? item.gmtModified)
    : null;
  return {
    id: item.improvementId,
    improvementId: item.improvementId,
    ownerUserId: item.ownerUserId,
    botOwnerUserId: item.botOwnerUserId,
    botId: item.botId,
    title: item.title,
    rootCauseSummary: item.rootCauseSummary,
    assignmentReason: item.assignmentReason,
    suggestedAction: item.suggestedAction,
    sourceRuleId: item.sourceRuleId,
    actionType: item.actionType,
    status: item.status,
    adminReviewStatus: item.adminReviewStatus,
    adminReviewReason: item.adminReviewComment ?? item.rejectComment,
    adminReviewedBy: item.adminReviewedBy,
    adminReviewedAt: governanceTimestamp(item.adminReviewedAt),
    handledAt: governanceTimestamp(item.handledAt),
    verificationStatus: item.verificationStatus,
    verificationLastCheckedAt: governanceTimestamp(item.verificationLastCheckedAt),
    verificationNewSessionCount: item.verificationNewSessionCount,
    verificationLastRecurrenceAt: item.verificationLastRecurrenceAt,
    resolvedSource: item.resolvedSource,
    resolvedAt,
    evidenceCount: item.evidenceCount,
    sessionCount: item.sessionCount,
    dataStartTime: item.dataStartTime,
    dataEndTime: item.dataEndTime,
    createdBy: item.createdBy,
    createdAt: governanceTimestamp(item.gmtCreate),
    updatedAt: governanceTimestamp(item.gmtModified),
    version: item.version,
  };
}

function validateSelectedTasks(value: unknown): SelectedTask[] {
  if (!Array.isArray(value) || value.length === 0 || value.length > 50) {
    throw new InsightValidationError(
      "selectedTasks 必须包含 1 到 50 个失败任务",
    );
  }
  const selected: SelectedTask[] = [];
  const seen = new Set<string>();
  for (const raw of value) {
    if (!raw || typeof raw !== "object")
      throw new InsightValidationError("selectedTasks 格式不正确");
    const item = raw as Record<string, unknown>;
    const sessionId = String(item.sessionId ?? "").trim();
    const taskIndex = Number(item.taskIndex);
    if (!sessionId || !Number.isInteger(taskIndex) || taskIndex < 0) {
      throw new InsightValidationError(
        "selectedTasks 需要合法的 sessionId 和 taskIndex",
      );
    }
    const key = `${sessionId}:${taskIndex}`;
    if (seen.has(key))
      throw new InsightValidationError(`重复选择失败任务: ${key}`);
    seen.add(key);
    selected.push({ sessionId, taskIndex });
  }
  return selected;
}

export class InsightService {
  constructor(
    private readonly readProvider: InsightReadProvider,
    private readonly evidenceProvider: EvidenceProvider,
    private readonly improvementRepo: InsightImprovementRepository,
    private readonly dingTalkSender: DingTalkSender | null = null,
    private readonly evidencePublicBaseUrl = "http://localhost:5173",
  ) {}

  getOverview(scope: InsightQueryScope) {
    return this.readProvider.getOverview(scope);
  }

  async getTrend(scope: InsightQueryScope, options: { includeAdminMetrics?: boolean } = {}) {
    const includeAdminMetrics = options.includeAdminMetrics === true;
    const [trend, governanceEvents, adminMetrics, autoClosureRates] = await Promise.all([
      this.readProvider.getTrend(scope),
      this.improvementRepo.listEffectEvents(scope),
      includeAdminMetrics ? this.readProvider.getAdminTrendMetrics(scope) : Promise.resolve(null),
      this.improvementRepo.getAutoClosureRateByDate(scope),
    ]);
    return {
      ...trend,
      governanceEvents,
      points: trend.points.map((point) => ({
        ...point,
        autoClosureRate: autoClosureRates[point.date] ?? null,
        ...(adminMetrics
          ? {
              overallTaskCount: adminMetrics.overallTaskCountByDate[point.date] ?? 0,
              repairBotCapabilityFailureTaskCount: adminMetrics.repairBotCapabilityFailureTaskCountByDate[point.date] ?? 0,
            }
          : {}),
      })),
    };
  }

  listFailureTasks(query: FailureTaskQuery) {
    return this.readProvider.listFailureTasks(query);
  }

  private async resolveTask(
    userId: string,
    sessionId: string,
    taskIndex: number,
    options: ResolveTaskOptions = {},
  ) {
    const directTask = await this.readProvider.getFailureTask(
      userId,
      sessionId,
      taskIndex,
    );
    const anchorTaskIndex = options.anchorTaskIndex ?? taskIndex;
    const anchorTask =
      directTask ??
      (anchorTaskIndex !== taskIndex
        ? await this.readProvider.getFailureTask(
            userId,
            sessionId,
            anchorTaskIndex,
          )
        : null);
    if (!anchorTask)
      throw new InsightNotFoundError("失败任务不存在或缺少同 Session 证据锚点");

    const evidenceKey = `${anchorTask.payloadRef}:${anchorTask.payloadVersionId ?? ""}:${anchorTask.payloadEtag}`;
    let evidencePromise = options.evidenceCache?.get(evidenceKey);
    if (!evidencePromise) {
      evidencePromise = this.evidenceProvider.readEvidence(
        anchorTask.payloadRef,
        {
          versionId: anchorTask.payloadVersionId,
          expectedEtag: anchorTask.payloadEtag,
        },
      );
      options.evidenceCache?.set(evidenceKey, evidencePromise);
    }
    const evidence = await evidencePromise;
    if (
      evidence.user_id !== userId ||
      evidence.user_id !== anchorTask.ownerUserId ||
      evidence.bot_id !== anchorTask.botId ||
      evidence.session_id !== sessionId ||
      evidence.dt !== anchorTask.sourceDt ||
      evidence.batch_id !== anchorTask.batchId
    ) {
      throw new InsightDataNotReadyError(
        "Task Index 与 Evidence 元数据或数据批次不一致",
      );
    }
    const judgeTask = evidence.tasks.find(
      (item) => item.task_index === taskIndex,
    );
    if (!judgeTask)
      throw new InsightDataNotReadyError("Evidence 中缺少对应 Task");

    if (directTask) {
      if (
        judgeTask.task_description !== directTask.taskDescription ||
        judgeTask.is_complete !== directTask.isComplete ||
        String(judgeTask.task_failure_class ?? "") !== directTask.failureClass
      ) {
        throw new InsightDataNotReadyError(
          "Task Index 与 Evidence 的任务描述、完成状态或失败分类不一致",
        );
      }
      return { task: directTask, evidence, judgeTask };
    }

    return {
      task: {
        ...anchorTask,
        taskIndex: judgeTask.task_index,
        taskDescription: judgeTask.task_description,
        isComplete: judgeTask.is_complete,
        failureClass: String(
          judgeTask.task_failure_class ??
            (judgeTask.is_complete === 1 ? "COMPLETED" : "UNKNOWN"),
        ),
        judgeReasonSummary: nullableString(judgeTask.reasoning),
      },
      evidence,
      judgeTask,
    };
  }

  async getFailureTaskDetail(
    userId: string,
    sessionId: string,
    taskIndex: number,
    anchorTaskIndex?: number,
  ): Promise<FailureTaskDetail> {
    const { task, evidence, judgeTask } = await this.resolveTask(
      userId,
      sessionId,
      taskIndex,
      { anchorTaskIndex },
    );
    const blocks = buildTimelineBlocks(evidence.messages, judgeTask);
    const session = evidence.session;
    return {
      contractVersion: INSIGHT_CONTRACT_VERSION,
      dataAsOf: task.dataAsOf,
      sourceBatchId: task.batchId,
      task,
      session: {
        sessionId: evidence.session_id,
        userId: evidence.user_id,
        botId: evidence.bot_id,
        botName: nullableString(session.bot_name) ?? task.botName,
        startTime: nullableString(session.start_time) ?? task.sessionStartTime,
        endTime: nullableString(session.end_time) ?? task.sessionEndTime,
        durationSeconds:
          nullableNumber(session.duration_seconds) ??
          task.sessionDurationSeconds,
        isCron:
          typeof session.is_cron === "boolean" ? session.is_cron : task.isCron,
        messageCount: Math.max(
          0,
          Math.trunc(
            nullableNumber(session.message_count) ?? evidence.messages.length,
          ),
        ),
      },
      sessionTasks: evidence.tasks.map((item) => ({
        taskIndex: item.task_index,
        taskDescription: item.task_description,
        messageRange: item.message_range,
        isComplete: item.is_complete,
        failureClass: String(
          item.task_failure_class ??
            (item.is_complete === 1 ? "COMPLETED" : "UNKNOWN"),
        ),
      })),
      judge: judgeTask,
      evidence: {
        schemaVersion: evidence.schema_version,
        batchId: evidence.batch_id,
        generatedAt: evidence.generated_at,
        etag: task.payloadEtag,
        versionId: task.payloadVersionId,
      },
      timeline: {
        totalBlocks: blocks.length,
        blocks: blocks.map(toTimelineSummary),
      },
    };
  }

  async getTimeline(
    userId: string,
    sessionId: string,
    taskIndex: number,
    input: {
      cursor?: string;
      blockId?: string;
      position?: "tail";
      all?: boolean;
      pageSize: number;
      anchorTaskIndex?: number;
    },
  ): Promise<TimelinePage> {
    const { task, evidence, judgeTask } = await this.resolveTask(
      userId,
      sessionId,
      taskIndex,
      { anchorTaskIndex: input.anchorTaskIndex },
    );
    const blocks = buildTimelineBlocks(evidence.messages, judgeTask);
    const taskKey = `${sessionId}:${taskIndex}`;
    if (input.blockId) {
      const block = blocks.find((item) => item.blockId === input.blockId);
      if (!block) throw new InsightNotFoundError("时间线节点不存在");
      return {
        contractVersion: INSIGHT_CONTRACT_VERSION,
        dataAsOf: task.dataAsOf,
        sourceBatchId: task.batchId,
        task: { sessionId, taskIndex, messageRange: judgeTask.message_range },
        items: [block],
        nextCursor: null,
      };
    }
    let offset = input.position === "tail" ? Math.max(0, blocks.length - input.pageSize) : 0;
    if (input.cursor) {
      const cursor = decodeTimelineCursor(input.cursor);
      if (cursor.taskKey !== taskKey || cursor.etag !== task.payloadEtag) {
        throw new InsightCursorError(
          "时间线游标与当前 Task 或 Evidence 版本不匹配",
        );
      }
      offset = cursor.offset;
    }
    const page = input.all ? blocks : blocks.slice(offset, offset + input.pageSize);
    const nextOffset = offset + page.length;
    return {
      contractVersion: INSIGHT_CONTRACT_VERSION,
      dataAsOf: task.dataAsOf,
      sourceBatchId: task.batchId,
      task: { sessionId, taskIndex, messageRange: judgeTask.message_range },
      items: page.map(toTimelineSummary),
      nextCursor: input.all || input.position === "tail"
        ? null
        :
        nextOffset < blocks.length
          ? encodeTimelineCursor({
              offset: nextOffset,
              taskKey,
              etag: task.payloadEtag,
            })
          : null,
    };
  }

  async createImprovement(
    identity: {
      actorUserId: string;
      ownerUserId: string;
      sourceUserId: string;
      sourceType: "USER_SELECTED" | "ADMIN_SELECTED" | "ADMIN_RULE";
    },
    idempotencyKey: string,
    body: Record<string, unknown>,
    options: { notify?: boolean } = {},
  ): Promise<{
    created: boolean;
    improvement: ImprovementDetail;
  }> {
    if (body.userId !== undefined || body.user_id !== undefined) {
      throw new InsightValidationError(
        "用户身份由服务端注入，请勿在请求体传 userId",
      );
    }
    const { actorUserId, ownerUserId, sourceUserId, sourceType } = identity;
    const botId = String(body.botId ?? "").trim();
    const title = String(body.title ?? "").trim();
    const userGuidance =
      body.userGuidance == null
        ? null
        : String(body.userGuidance).trim() || null;
    const sourceRuleId = body.sourceRuleId == null ? null : String(body.sourceRuleId).trim() || null;
    const actionTypeRaw = body.actionType == null ? null : String(body.actionType).trim().toUpperCase();
    const actionType = actionTypeRaw === "DIRECT_EVOLUTION" || actionTypeRaw === "ASSIGN_OWNER"
      ? actionTypeRaw
      : null;
    const assignmentReason = body.assignmentReason == null ? null : String(body.assignmentReason).trim() || null;
    const rootCauseSummary = body.rootCauseSummary == null ? null : String(body.rootCauseSummary).trim() || null;
    const suggestedAction = body.suggestedAction == null ? null : String(body.suggestedAction).trim() || null;
    if (!botId || !title)
      throw new InsightValidationError("botId 和 title 为必填项");
    if (title.length > 256)
      throw new InsightValidationError("title 不能超过 256 个字符");
    if ((userGuidance?.length ?? 0) > 5000)
      throw new InsightValidationError("userGuidance 不能超过 5000 个字符");
    if (sourceType === "ADMIN_RULE" && (!sourceRuleId || !actionType)) {
      throw new InsightValidationError("治理 Action 必须提供 sourceRuleId 和 actionType");
    }
    if ((sourceRuleId?.length ?? 0) > 64) {
      throw new InsightValidationError("sourceRuleId 不能超过 64 个字符");
    }
    if ((assignmentReason?.length ?? 0) > 1000 || (rootCauseSummary?.length ?? 0) > 1000) {
      throw new InsightValidationError("assignmentReason 或 rootCauseSummary 不能超过 1000 个字符");
    }
    if ((suggestedAction?.length ?? 0) > 5000) {
      throw new InsightValidationError("suggestedAction 不能超过 5000 个字符");
    }
    const selectedTasks = validateSelectedTasks(body.selectedTasks);
    const requestFingerprint = fingerprint({
      userId: ownerUserId,
      sourceUserId,
      botId,
      title,
      userGuidance,
      sourceRuleId,
      actionType,
      assignmentReason,
      rootCauseSummary,
      suggestedAction,
      selectedTasks: [...selectedTasks].sort((left, right) =>
        `${left.sessionId}:${left.taskIndex}`.localeCompare(
          `${right.sessionId}:${right.taskIndex}`,
        ),
      ),
    });
    const idempotent = await this.improvementRepo.findByIdempotency(
      ownerUserId,
      idempotencyKey,
    );
    if (idempotent) {
      if (idempotent.content_fingerprint !== requestFingerprint) {
        throw new InsightConflictError(
          "同一个 Idempotency-Key 已用于不同的创建请求",
        );
      }
      const detail = await this.improvementRepo.getDetailById(idempotent.id);
      if (!detail) throw new InsightDataNotReadyError("幂等改进项记录不完整");
      return { created: false, improvement: detail };
    }

    const evidenceCache = new Map<string, Promise<SessionEvidence>>();
    const resolved = await Promise.all(
      selectedTasks.map(async (selected, ordinal) => {
        let task;
        try {
          ({ task } = await this.resolveTask(
            sourceUserId,
            selected.sessionId,
            selected.taskIndex,
            { evidenceCache },
          ));
        } catch (error) {
          if (error instanceof InsightNotFoundError) {
            throw new InsightValidationError(
              `失败任务不存在: ${selected.sessionId}:${selected.taskIndex}`,
            );
          }
          throw error;
        }
        if (task.botId !== botId)
          throw new InsightValidationError("选中的失败任务不属于同一个 Bot");
        if (![0, 2, 3].includes(task.isComplete))
          throw new InsightValidationError("只能选择未完成、未知或中止的 Task");
        const snapshot: ImprovementEvidenceSnapshot = {
          sessionId: task.sessionId,
          taskIndex: task.taskIndex,
          ordinal,
          taskDescription: task.taskDescription,
          failureClass: task.failureClass,
          reasoningSummary: task.judgeReasonSummary,
          payloadRef: task.payloadRef,
          payloadEtag: task.payloadEtag,
          payloadVersionId: task.payloadVersionId,
        };
        return { task, snapshot };
      }),
    );
    const dataAsOfValues = new Set(resolved.map((item) => item.task.dataAsOf));
    const batchIds = new Set(resolved.map((item) => item.task.batchId));
    const dataAsOf = [...dataAsOfValues].sort().at(-1)!;
    const batchId =
      batchIds.size === 1 ? [...batchIds][0] : "MULTI_BATCH";
    const startTimes = resolved
      .map((item) => item.task.sessionStartTime)
      .filter((value): value is string => Boolean(value));
    const endTimes = resolved
      .map((item) => item.task.sessionEndTime)
      .filter((value): value is string => Boolean(value));
    const created = await this.improvementRepo.create({
      ownerUserId,
      botOwnerUserId: sourceUserId,
      botId,
      title,
      userGuidance,
      sourceType: sourceType === "ADMIN_RULE"
        ? governanceSourceType(actionType as "DIRECT_EVOLUTION" | "ASSIGN_OWNER")
        : sourceType,
      sourceRuleId,
      dataStartTime: startTimes.length ? startTimes.sort()[0] : null,
      dataEndTime: endTimes.length ? (endTimes.sort().at(-1) ?? null) : null,
      dataAsOf,
      batchId,
      contentFingerprint: requestFingerprint,
      idempotencyKey,
      createdBy: actorUserId,
      initialStatus: sourceType === "ADMIN_RULE"
        ? "PENDING_ADMIN"
        : "ACTIVE",
      assignmentReason,
      rootCauseSummary,
      suggestedAction,
      evidence: resolved.map((item) => item.snapshot),
    });
    if (
      !created.created &&
      created.item.content_fingerprint !== requestFingerprint
    ) {
      throw new InsightConflictError(
        "同一个 Idempotency-Key 已用于不同的创建请求",
      );
    }
    const detail = await this.improvementRepo.getDetailById(created.item.id);
    if (!detail) throw new InsightDataNotReadyError("改进项创建后读取失败");
    if (
      options.notify !== false
      && created.created
      && sourceType === "ADMIN_SELECTED"
      && this.dingTalkSender?.enabled
    ) {
      void this.dingTalkSender.sendImprovementNotification({
        improvementId: detail.improvementId,
        recipientUserId: ownerUserId,
        title: detail.title,
        botId: detail.botId,
        userGuidance: detail.userGuidance,
        evidenceCount: detail.evidenceCount,
        actionType: detail.actionType,
      }).then((result) => {
        console.log(`[clawweb] Insight DingTalk notification sent improvement=${detail.improvementId} recipient=${ownerUserId} processQueryKey=${result.processQueryKey ?? "none"}`);
      }).catch((error) => {
        console.warn(`[clawweb] Insight DingTalk notification failed improvement=${detail.improvementId} recipient=${ownerUserId}: ${error instanceof Error ? error.message : String(error)}`);
      });
    }
    return { created: created.created, improvement: detail };
  }

  async createImprovementsBatch(items: Array<{
    identity: {
      actorUserId: string;
      ownerUserId: string;
      sourceUserId: string;
      sourceType: "USER_SELECTED" | "ADMIN_SELECTED" | "ADMIN_RULE";
    };
    idempotencyKey: string;
    body: Record<string, unknown>;
  }>): Promise<ImprovementDetail[]> {
    if (items.length === 0 || items.length > 50) {
      throw new InsightValidationError("items 必须包含 1 到 50 个改进项");
    }

    const results: Array<{ created: boolean; improvement: ImprovementDetail; sourceType: "USER_SELECTED" | "ADMIN_SELECTED" | "ADMIN_RULE" }> = [];
    for (const item of items) {
      const result = await this.createImprovement(
        item.identity,
        item.idempotencyKey,
        item.body,
        { notify: false },
      );
      results.push({ ...result, sourceType: item.identity.sourceType });
    }

    if (this.dingTalkSender?.enabled) {
      const byRecipient = new Map<string, ImprovementDetail[]>();
      for (const result of results) {
        if (!result.created || result.sourceType !== "ADMIN_SELECTED") continue;
        const current = byRecipient.get(result.improvement.ownerUserId) ?? [];
        current.push(result.improvement);
        byRecipient.set(result.improvement.ownerUserId, current);
      }
      for (const [recipientUserId, improvements] of byRecipient) {
        void this.dingTalkSender.sendImprovementBatchNotification({
          recipientUserId,
          improvements: improvements.map((improvement) => ({
            improvementId: improvement.improvementId,
            recipientUserId,
            title: improvement.title,
            botId: improvement.botId,
            userGuidance: improvement.userGuidance,
            evidenceCount: improvement.evidenceCount,
            actionType: improvement.actionType,
          })),
        }).then((result) => {
          console.log(`[clawweb] Insight DingTalk batch notification sent improvements=${improvements.map((item) => item.improvementId).join(",")} recipient=${recipientUserId} processQueryKey=${result.processQueryKey ?? "none"}`);
        }).catch((error) => {
          console.warn(`[clawweb] Insight DingTalk batch notification failed improvements=${improvements.map((item) => item.improvementId).join(",")} recipient=${recipientUserId}: ${error instanceof Error ? error.message : String(error)}`);
        });
      }
    }

    return results.map((result) => result.improvement);
  }

  listImprovements(
    userId: string,
    input: {
      botId?: string;
      status?: string;
      cursor?: string;
      pageSize: number;
    },
  ) {
    const status = input.status?.trim().toUpperCase();
    if (
      status !== undefined &&
      !["ACTIVE", "IN_PROGRESS", "RESOLVED", "ARCHIVED"].includes(status)
    ) {
      throw new InsightValidationError("改进项 status 不合法");
    }
    return this.improvementRepo.list(userId, { ...input, status });
  }

  async getImprovement(
    userId: string,
    improvementIdValue: number,
  ): Promise<ImprovementDetail> {
    const detail = await this.improvementRepo.getDetail(
      userId,
      improvementIdValue,
    );
    if (!detail) throw new InsightNotFoundError("改进项不存在");
    return detail;
  }

  listAdminImprovements(input: {
    ownerUserId?: string;
    botId?: string;
    status?: string;
    adminReviewStatus?: string;
    includeAll?: boolean;
    cursor?: string;
    pageSize: number;
  }) {
    const status = input.status?.trim().toUpperCase();
    const adminReviewStatus = input.adminReviewStatus?.trim().toUpperCase();
    if (status && !["PENDING_ADMIN", "ACTIVE", "IN_PROGRESS", "RESOLVED", "ARCHIVED"].includes(status)) {
      throw new InsightValidationError("管理员改进项 status 不合法");
    }
    if (!input.includeAll && adminReviewStatus && !["PENDING", "APPROVED", "REJECTED"].includes(adminReviewStatus)) {
      throw new InsightValidationError("adminReviewStatus 不合法");
    }
    return this.improvementRepo.listAdmin({ ...input, status, adminReviewStatus });
  }

  async getAdminImprovement(improvementIdValue: number): Promise<ImprovementDetail> {
    const detail = await this.improvementRepo.getDetailById(improvementIdValue);
    if (!detail) throw new InsightNotFoundError("改进项不存在");
    return detail;
  }

  async reviewAdminAction(
    actorUserId: string,
    improvementIdValue: number,
    body: Record<string, unknown>,
    options: { notifyApproved?: boolean } = {},
  ): Promise<ImprovementView> {
    const allowedFields = new Set(["decision", "comment", "version"]);
    const unknownFields = Object.keys(body).filter((field) => !allowedFields.has(field));
    if (unknownFields.length) {
      throw new InsightValidationError(`Admin 审核包含未知字段: ${unknownFields.join(", ")}`);
    }
    const decision = String(body.decision ?? "").trim().toUpperCase();
    const comment = body.comment == null ? null : String(body.comment).trim() || null;
    const version = Number(body.version);
    if (!["APPROVE", "REJECT"].includes(decision)) {
      throw new InsightValidationError("decision 只能是 APPROVE 或 REJECT");
    }
    if (!Number.isInteger(version) || version < 1) {
      throw new InsightValidationError("version 必须是正整数");
    }
    if ((comment?.length ?? 0) > 1000) {
      throw new InsightValidationError("comment 不能超过 1000 个字符");
    }
    if (decision === "REJECT" && !comment) {
      throw new InsightValidationError("驳回时必须填写理由");
    }
    const updated = await this.improvementRepo.reviewAdminAction({
      improvementId: improvementIdValue,
      expectedVersion: version,
      decision: decision as "APPROVE" | "REJECT",
      reviewedBy: actorUserId,
      comment,
    });
    if (updated === null) throw new InsightNotFoundError("改进项不存在");
    if (updated === "VERSION_CONFLICT") throw new InsightConflictError("改进项已被更新，请刷新后重试");
    if (updated === "STATE_CONFLICT") throw new InsightConflictError("当前改进项不在待审核状态");
    if (decision !== "REJECT" && options.notifyApproved !== false) {
      this.notifyAdminApprovedImprovement(updated);
    }
    return updated;
  }

  notifyAdminApprovedImprovement(improvement: ImprovementView): void {
    if (!this.dingTalkSender?.enabled) return;
    void this.dingTalkSender.sendImprovementNotification({
      improvementId: improvement.improvementId,
      recipientUserId: improvement.ownerUserId,
      title: improvement.title,
      botId: improvement.botId,
      userGuidance: improvement.suggestedAction ?? improvement.userGuidance,
      evidenceCount: improvement.evidenceCount,
      actionType: improvement.actionType,
    }).catch((error) => {
      console.warn(`[clawweb] Insight DingTalk admin-approved notification failed improvement=${improvement.improvementId}: ${error instanceof Error ? error.message : String(error)}`);
    });
  }

  async markImprovementHandled(
    userId: string,
    improvementIdValue: number,
    body: Record<string, unknown>,
  ): Promise<ImprovementView> {
    const unknownFields = Object.keys(body).filter((field) => field !== "version");
    if (unknownFields.length) throw new InsightValidationError(`处理回写包含未知字段: ${unknownFields.join(", ")}`);
    const version = Number(body.version);
    if (!Number.isInteger(version) || version < 1) throw new InsightValidationError("version 必须是正整数");
    const updated = await this.improvementRepo.markHandled(userId, improvementIdValue, version);
    if (updated === null) throw new InsightNotFoundError("改进项不存在");
    if (updated === "VERSION_CONFLICT") throw new InsightConflictError("改进项已被更新，请刷新后重试");
    if (updated === "STATE_CONFLICT") throw new InsightConflictError("当前状态不允许声明已处理");
    return updated;
  }

  async markAdminImprovementHandled(
    operatedBy: string,
    improvementIdValue: number,
    body: Record<string, unknown>,
  ): Promise<ImprovementView> {
    const unknownFields = Object.keys(body).filter((field) => field !== "version");
    if (unknownFields.length) throw new InsightValidationError(`管理员处理回写包含未知字段: ${unknownFields.join(", ")}`);
    const version = Number(body.version);
    if (!Number.isInteger(version) || version < 1) throw new InsightValidationError("version 必须是正整数");
    const updated = await this.improvementRepo.markAdminHandled({
      improvementId: improvementIdValue,
      expectedVersion: version,
      operatedBy,
    });
    if (updated === null) throw new InsightNotFoundError("改进项不存在");
    if (updated === "VERSION_CONFLICT") throw new InsightConflictError("改进项已被更新，请刷新后重试");
    if (updated === "STATE_CONFLICT") throw new InsightConflictError("当前状态不允许由管理员推进到验收");
    return updated;
  }

  async rejectImprovement(
    userId: string,
    improvementIdValue: number,
    body: Record<string, unknown>,
  ): Promise<ImprovementView> {
    const allowedFields = new Set(["reasonCode", "comment", "version"]);
    const unknownFields = Object.keys(body).filter((field) => !allowedFields.has(field));
    if (unknownFields.length) throw new InsightValidationError(`驳回包含未知字段: ${unknownFields.join(", ")}`);
    const reasonCode = String(body.reasonCode ?? "").trim().toUpperCase();
    const comment = body.comment == null ? null : String(body.comment).trim() || null;
    const version = Number(body.version);
    const allowedReasons = [
      "EXPECTED_BUSINESS_FAILURE",
      "ALREADY_FIXED",
      "NOT_OWNER_SCOPE",
      "NO_ACTION_NEEDED",
      "MISIDENTIFIED",
      "OTHER",
    ];
    if (!allowedReasons.includes(reasonCode)) throw new InsightValidationError("reasonCode 不合法");
    if (reasonCode === "OTHER" && !comment) throw new InsightValidationError("其他原因必须填写补充说明");
    if ((comment?.length ?? 0) > 2000) throw new InsightValidationError("comment 不能超过 2000 个字符");
    if (!Number.isInteger(version) || version < 1) throw new InsightValidationError("version 必须是正整数");
    const updated = await this.improvementRepo.reject({
      ownerUserId: userId,
      improvementId: improvementIdValue,
      expectedVersion: version,
      reasonCode,
      comment,
    });
    if (updated === null) throw new InsightNotFoundError("改进项不存在");
    if (updated === "VERSION_CONFLICT") throw new InsightConflictError("改进项已被更新，请刷新后重试");
    if (updated === "STATE_CONFLICT") throw new InsightConflictError("当前状态不允许驳回");
    return updated;
  }

  async rejectAdminImprovement(
    operatedBy: string,
    improvementIdValue: number,
    body: Record<string, unknown>,
  ): Promise<ImprovementView> {
    const allowedFields = new Set(["reasonCode", "comment", "version"]);
    const unknownFields = Object.keys(body).filter((field) => !allowedFields.has(field));
    if (unknownFields.length) throw new InsightValidationError(`管理员驳回包含未知字段: ${unknownFields.join(", ")}`);
    const reasonCode = String(body.reasonCode ?? "").trim().toUpperCase();
    const comment = body.comment == null ? null : String(body.comment).trim() || null;
    const version = Number(body.version);
    const allowedReasons = [
      "EXPECTED_BUSINESS_FAILURE", "ALREADY_FIXED", "NOT_OWNER_SCOPE",
      "NO_ACTION_NEEDED", "MISIDENTIFIED", "OTHER",
    ];
    if (!allowedReasons.includes(reasonCode)) throw new InsightValidationError("reasonCode 不合法");
    if (reasonCode === "OTHER" && !comment) throw new InsightValidationError("其他原因必须填写补充说明");
    if ((comment?.length ?? 0) > 2000) throw new InsightValidationError("comment 不能超过 2000 个字符");
    if (!Number.isInteger(version) || version < 1) throw new InsightValidationError("version 必须是正整数");
    const updated = await this.improvementRepo.rejectAdmin({
      improvementId: improvementIdValue,
      expectedVersion: version,
      reasonCode,
      comment,
      operatedBy,
    });
    if (updated === null) throw new InsightNotFoundError("改进项不存在");
    if (updated === "VERSION_CONFLICT") throw new InsightConflictError("改进项已被更新，请刷新后重试");
    if (updated === "STATE_CONFLICT") throw new InsightConflictError("当前状态不允许由管理员驳回");
    return updated;
  }

  async reopenAdminImprovement(
    operatedBy: string,
    improvementIdValue: number,
    body: Record<string, unknown>,
  ): Promise<ImprovementView> {
    const allowedFields = new Set(["reason", "version"]);
    const unknownFields = Object.keys(body).filter((field) => !allowedFields.has(field));
    if (unknownFields.length) throw new InsightValidationError(`管理员恢复处理包含未知字段: ${unknownFields.join(", ")}`);
    const reason = body.reason == null ? null : String(body.reason).trim() || null;
    const version = Number(body.version);
    if (reason && reason.length > 1000) throw new InsightValidationError("reason 不能超过 1000 个字符");
    if (!Number.isInteger(version) || version < 1) throw new InsightValidationError("version 必须是正整数");

    const updated = await this.improvementRepo.reopenAdmin({
      improvementId: improvementIdValue,
      expectedVersion: version,
      reason,
      operatedBy,
    });
    if (updated === null) throw new InsightNotFoundError("改进项不存在");
    if (updated === "VERSION_CONFLICT") throw new InsightConflictError("改进项已被更新，请刷新后重试");
    if (updated === "STATE_CONFLICT") {
      throw new InsightConflictError("只有已归档的治理改进项可以恢复处理");
    }
    return updated;
  }

  listRecentRejections(input: {
    days: number;
    ownerUserId?: string;
    botId?: string;
    sourceRuleId?: string;
    limit: number;
  }) {
    if (!Number.isInteger(input.days) || input.days < 1 || input.days > 90) {
      throw new InsightValidationError("days 必须是 1 到 90 的整数");
    }
    return this.improvementRepo.listRecentRejections(input);
  }

  async markGovernanceActionHandled(
    improvementIdValue: number,
    body: Record<string, unknown>,
  ): Promise<ImprovementView> {
    const allowedFields = new Set(["handledAt", "appliedEvolveTaskId"]);
    const unknownFields = Object.keys(body).filter((field) => !allowedFields.has(field));
    if (unknownFields.length) {
      throw new InsightValidationError(`自动修复完成回写包含未知字段: ${unknownFields.join(", ")}`);
    }
    const handledAtRaw = String(body.handledAt ?? "").trim();
    if (!handledAtRaw || !/(?:Z|[+-]\d{2}:\d{2})$/i.test(handledAtRaw)) {
      throw new InsightValidationError("handledAt 必须是包含时区的 ISO 8601 时间");
    }
    const handledAt = new Date(handledAtRaw);
    if (!Number.isFinite(handledAt.getTime())) {
      throw new InsightValidationError("handledAt 必须是合法的 ISO 8601 时间");
    }
    const appliedEvolveTaskId = body.appliedEvolveTaskId == null
      ? null
      : String(body.appliedEvolveTaskId).trim();
    if (body.appliedEvolveTaskId != null && !appliedEvolveTaskId) {
      throw new InsightValidationError("appliedEvolveTaskId 不能为空");
    }
    if ((appliedEvolveTaskId?.length ?? 0) > 64) {
      throw new InsightValidationError("appliedEvolveTaskId 不能超过 64 个字符");
    }

    const updated = await this.improvementRepo.markGovernanceActionHandled({
      improvementId: improvementIdValue,
      handledAt,
      appliedEvolveTaskId,
    });
    if (updated === null) throw new InsightNotFoundError("改进项不存在");
    if (updated === "VERSION_CONFLICT") {
      throw new InsightConflictError("改进项已被更新，请重新读取");
    }
    if (updated === "STATE_CONFLICT") {
      throw new InsightConflictError("只能标记已审批、修复中的 DIRECT_EVOLUTION 项，且不能重复标记");
    }
    return updated;
  }

  async listGovernanceActions(input: {
    ownerUserId?: string;
    botId?: string;
    statuses?: string[];
    adminReviewStatuses?: string[];
    sourceRuleId?: string;
    since?: Date;
    fields?: string[];
    limit: number;
    offset: number;
  }): Promise<{ total: number; items: Array<Record<string, unknown>> }> {
    const statuses = [...new Set((input.statuses ?? []).map((value) => value.trim().toUpperCase()).filter(Boolean))];
    const invalidStatuses = statuses.filter((value) => !GOVERNANCE_ACTION_STATUSES.has(value));
    if (invalidStatuses.length) {
      throw new InsightValidationError(`status 不合法: ${invalidStatuses.join(", ")}`);
    }
    const adminReviewStatuses = [
      ...new Set((input.adminReviewStatuses ?? []).map((value) => value.trim().toUpperCase()).filter(Boolean)),
    ];
    const invalidReviewStatuses = adminReviewStatuses.filter(
      (value) => !GOVERNANCE_ACTION_REVIEW_STATUSES.has(value),
    );
    if (invalidReviewStatuses.length) {
      throw new InsightValidationError(`adminReviewStatus 不合法: ${invalidReviewStatuses.join(", ")}`);
    }
    const fields = input.fields?.length
      ? [...new Set(input.fields.map((value) => value.trim()).filter(Boolean))]
      : [...GOVERNANCE_ACTION_DEFAULT_FIELDS];
    const invalidFields = fields.filter((value) => !GOVERNANCE_ACTION_FIELDS.has(value));
    if (invalidFields.length) {
      throw new InsightValidationError(`fields 包含不支持的字段: ${invalidFields.join(", ")}`);
    }
    if (!Number.isInteger(input.limit) || input.limit < 1 || input.limit > 200) {
      throw new InsightValidationError("limit 必须是 1 到 200 的整数");
    }
    if (!Number.isInteger(input.offset) || input.offset < 0 || input.offset > 1_000_000) {
      throw new InsightValidationError("offset 必须是 0 到 1000000 的整数");
    }
    if (input.since && !Number.isFinite(input.since.getTime())) {
      throw new InsightValidationError("since 必须是合法的 ISO 8601 时间");
    }

    const result = await this.improvementRepo.listGovernanceActions({
      ownerUserId: input.ownerUserId,
      botId: input.botId,
      statuses,
      adminReviewStatuses,
      sourceRuleId: input.sourceRuleId,
      since: input.since,
      limit: input.limit,
      offset: input.offset,
    });
    return {
      total: result.total,
      items: result.items.map((item) => {
        const record = governanceActionRecord(item);
        return Object.fromEntries(fields.map((field) => [field, record[field]]));
      }),
    };
  }

  listVerificationCandidates(limit: number) {
    if (!Number.isInteger(limit) || limit < 1 || limit > 200) {
      throw new InsightValidationError("limit 必须是 1 到 200 的整数");
    }
    return this.improvementRepo.listVerificationCandidates(limit);
  }

  listOpenVerificationCandidates(limit: number) {
    if (!Number.isInteger(limit) || limit < 1 || limit > 200) {
      throw new InsightValidationError("limit 必须是 1 到 200 的整数");
    }
    return this.improvementRepo.listOpenVerificationCandidates(limit);
  }

  async recordResolvedVerificationResult(body: Record<string, unknown>): Promise<ImprovementView> {
    const allowedFields = new Set(["improvementId", "version", "newSessionCount"]);
    const unknownFields = Object.keys(body).filter((field) => !allowedFields.has(field));
    if (unknownFields.length) {
      throw new InsightValidationError(`RESOLVED 验收回写包含未知字段: ${unknownFields.join(", ")}`);
    }
    return this.recordVerificationResult({ ...body, outcome: "DISAPPEARED" });
  }

  async recordForcedVerificationResult(body: Record<string, unknown>): Promise<ImprovementView> {
    const allowedFields = new Set(["improvementId", "version", "newSessionCount", "reason", "resolvedSource", "operatedBy"]);
    const unknownFields = Object.keys(body).filter((field) => !allowedFields.has(field));
    if (unknownFields.length) {
      throw new InsightValidationError(`强制验收回写包含未知字段: ${unknownFields.join(", ")}`);
    }
    const improvementId = Number(body.improvementId);
    const version = Number(body.version);
    const newSessionCount = Number(body.newSessionCount ?? 0);
    const reason = String(body.reason ?? "").trim();
    const resolvedSource = String(body.resolvedSource ?? "TEST_FORCE_VERIFIED").trim().toUpperCase();
    const operatedBy = String(body.operatedBy ?? "").trim();
    if (!Number.isSafeInteger(improvementId) || improvementId < 1) throw new InsightValidationError("improvementId 不合法");
    if (!Number.isInteger(version) || version < 1) throw new InsightValidationError("version 必须是正整数");
    if (!Number.isInteger(newSessionCount) || newSessionCount < 1) throw new InsightValidationError("newSessionCount 必须是正整数");
    if (!reason || reason.length > 1000) throw new InsightValidationError("reason 必填且不能超过 1000 个字符");
    if (!["FORCE_VERIFIED", "TEST_FORCE_VERIFIED"].includes(resolvedSource)) {
      throw new InsightValidationError("resolvedSource 只能是 FORCE_VERIFIED 或 TEST_FORCE_VERIFIED");
    }
    if (!operatedBy || operatedBy.length > 128) throw new InsightValidationError("operatedBy 必填且不能超过 128 个字符");

    const updated = await this.improvementRepo.forceResolveVerification({
      improvementId,
      expectedVersion: version,
      newSessionCount,
      reason,
      resolvedSource: resolvedSource as "FORCE_VERIFIED" | "TEST_FORCE_VERIFIED",
      operatedBy,
    });
    if (updated === null) throw new InsightNotFoundError("改进项不存在");
    if (updated === "VERSION_CONFLICT") throw new InsightConflictError("改进项已被更新，请重新读取");
    if (updated === "STATE_CONFLICT") {
      throw new InsightConflictError("只有已进入修复或验收阶段的改进项可以强制验收");
    }
    return updated;
  }

  async recordOpenVerificationResult(body: Record<string, unknown>): Promise<ImprovementView> {
    const allowedFields = new Set([
      "improvementId",
      "version",
      "outcome",
      "newSessionCount",
      "lastRecurrenceAt",
      "overrideActionType",
    ]);
    const unknownFields = Object.keys(body).filter((field) => !allowedFields.has(field));
    if (unknownFields.length) {
      throw new InsightValidationError(`未回传改进项验收包含未知字段: ${unknownFields.join(", ")}`);
    }
    const improvementId = Number(body.improvementId);
    const version = Number(body.version);
    const outcome = String(body.outcome ?? "").trim().toUpperCase();
    const newSessionCount = Number(body.newSessionCount ?? 0);
    const lastRecurrenceAt = body.lastRecurrenceAt == null ? null : String(body.lastRecurrenceAt).trim() || null;
    const overrideActionType = body.overrideActionType == null
      ? null
      : String(body.overrideActionType).trim().toUpperCase();
    if (!Number.isSafeInteger(improvementId) || improvementId < 1) throw new InsightValidationError("improvementId 不合法");
    if (!Number.isInteger(version) || version < 1) throw new InsightValidationError("version 必须是正整数");
    if (!Number.isInteger(newSessionCount) || newSessionCount < 0) throw new InsightValidationError("newSessionCount 必须是非负整数");
    if (!["DISAPPEARED", "STILL_PRESENT", "INSUFFICIENT_DATA"].includes(outcome)) {
      throw new InsightValidationError("outcome 不合法");
    }
    if (outcome === "DISAPPEARED" && newSessionCount < 1) {
      throw new InsightValidationError("没有新 Session 时不能确认问题已消失");
    }
    if (outcome === "STILL_PRESENT" && newSessionCount < 1) {
      throw new InsightValidationError("没有新 Session 时不能确认问题仍然存在");
    }
    if (overrideActionType && overrideActionType !== "ASSIGN_OWNER") {
      throw new InsightValidationError("overrideActionType 只能是 ASSIGN_OWNER");
    }
    if (overrideActionType && outcome !== "STILL_PRESENT") {
      throw new InsightValidationError("overrideActionType 只能用于 STILL_PRESENT");
    }
    const updated = await this.improvementRepo.recordOpenVerification({
      improvementId,
      expectedVersion: version,
      outcome: outcome as "DISAPPEARED" | "STILL_PRESENT" | "INSUFFICIENT_DATA",
      newSessionCount,
      lastRecurrenceAt,
      overrideActionType: overrideActionType as "ASSIGN_OWNER" | null,
    });
    if (updated === null) throw new InsightNotFoundError("改进项不存在");
    if (updated === "VERSION_CONFLICT") throw new InsightConflictError("改进项已被更新，请重新读取");
    if (updated === "TOO_EARLY") throw new InsightConflictError("开放改进项至少需要观察 7 天且没有同类问题，才能确认验收通过", "OPEN_VERIFICATION_TOO_EARLY");
    if (updated === "STATE_CONFLICT") throw new InsightConflictError("当前改进项不属于未回传修复的主动验收范围");
    return updated;
  }

  async recordVerificationResult(body: Record<string, unknown>): Promise<ImprovementView> {
    const allowedFields = new Set([
      "improvementId",
      "version",
      "outcome",
      "newSessionCount",
      "lastRecurrenceAt",
      "overrideActionType",
    ]);
    const unknownFields = Object.keys(body).filter((field) => !allowedFields.has(field));
    if (unknownFields.length) {
      throw new InsightValidationError(`验证回写包含未知字段: ${unknownFields.join(", ")}`);
    }
    const improvementId = Number(body.improvementId);
    const version = Number(body.version);
    const outcome = String(body.outcome ?? "").trim().toUpperCase();
    const newSessionCount = Number(body.newSessionCount ?? 0);
    const lastRecurrenceAt = body.lastRecurrenceAt == null ? null : String(body.lastRecurrenceAt).trim() || null;
    const overrideActionType = body.overrideActionType == null
      ? null
      : String(body.overrideActionType).trim().toUpperCase();
    if (!Number.isSafeInteger(improvementId) || improvementId < 1) throw new InsightValidationError("improvementId 不合法");
    if (!Number.isInteger(version) || version < 1) throw new InsightValidationError("version 必须是正整数");
    if (!Number.isInteger(newSessionCount) || newSessionCount < 0) throw new InsightValidationError("newSessionCount 必须是非负整数");
    if (!["DISAPPEARED", "STILL_PRESENT", "INSUFFICIENT_DATA"].includes(outcome)) {
      throw new InsightValidationError("outcome 不合法");
    }
    if (outcome === "DISAPPEARED" && newSessionCount < 1) {
      throw new InsightValidationError("没有新 Session 时不能确认问题已消失");
    }
    if (outcome === "STILL_PRESENT" && newSessionCount < 1) {
      throw new InsightValidationError("没有新 Session 时不能确认问题仍然存在");
    }
    if (overrideActionType && overrideActionType !== "ASSIGN_OWNER") {
      throw new InsightValidationError("overrideActionType 只能是 ASSIGN_OWNER");
    }
    if (overrideActionType && outcome !== "STILL_PRESENT") {
      throw new InsightValidationError("overrideActionType 只能用于 STILL_PRESENT");
    }
    const updated = await this.improvementRepo.recordVerification({
      improvementId,
      expectedVersion: version,
      outcome: outcome as "DISAPPEARED" | "STILL_PRESENT" | "INSUFFICIENT_DATA",
      newSessionCount,
      lastRecurrenceAt,
      overrideActionType: overrideActionType as "ASSIGN_OWNER" | null,
    });
    if (updated === null) throw new InsightNotFoundError("改进项不存在");
    if (updated === "VERSION_CONFLICT") throw new InsightConflictError("改进项已被更新，请重新读取");
    if (updated === "TOO_EARLY") throw new InsightConflictError("修复后至少需要观察 2 天且没有同类问题，才能确认验收通过", "VERIFICATION_TOO_EARLY");
    if (updated === "STATE_CONFLICT") throw new InsightConflictError("当前状态不允许验证回写");
    return updated;
  }

  async getImprovementHandoff(userId: string, improvementIdValue: number) {
    const detail = await this.getImprovement(userId, improvementIdValue);
    const evidence: SelfRepairHandoffEvidence[] = detail.evidence.map((item) => ({
      ...item,
      evidenceAccessUrl: buildEvidenceAccessUrl({
        publicBaseUrl: this.evidencePublicBaseUrl,
        improvementId: detail.improvementId,
        ownerUserId: detail.ownerUserId,
        sessionId: item.sessionId,
        taskIndex: item.taskIndex,
      }),
    }));
    return {
      contractVersion: "insight-improvement-handoff/v1",
      improvement: {
        improvementId: detail.improvementId,
        ownerUserId: detail.ownerUserId,
        botOwnerUserId: detail.botOwnerUserId,
        botId: detail.botId,
        title: detail.title,
        userGuidance: detail.userGuidance,
        actionType: detail.actionType,
        adminReviewStatus: detail.adminReviewStatus,
        sourceRuleId: detail.sourceRuleId,
        dataAsOf: detail.dataAsOf,
        batchId: detail.batchId,
        evidenceCount: detail.evidenceCount,
      },
      evidence,
      agentMarkdown: buildSelfRepairMarkdown(detail, evidence),
    };
  }

  async getEvidenceByReference(
    ownerUserId: string,
    improvementId: number,
    sessionId: string,
    taskIndex: number,
  ): Promise<SessionEvidence> {
    const detail = await this.improvementRepo.getDetail(ownerUserId, improvementId);
    if (!detail) throw new InsightNotFoundError("Evidence 访问地址无效");
    const snapshot = detail.evidence.find((item) => (
      item.sessionId === sessionId && item.taskIndex === taskIndex
    ));
    if (!snapshot) throw new InsightNotFoundError("Evidence 访问地址无效");
    const evidence = await this.evidenceProvider.readEvidence(snapshot.payloadRef, {
      versionId: snapshot.payloadVersionId,
      expectedEtag: snapshot.payloadEtag,
    });
    if (
      evidence.session_id !== snapshot.sessionId
      || evidence.bot_id !== detail.botId
      || evidence.user_id !== detail.botOwnerUserId
      || !evidence.tasks.some((task) => task.task_index === snapshot.taskIndex)
    ) {
      throw new InsightDataNotReadyError("Evidence 与改进项冻结证据不一致");
    }
    return evidence;
  }

  /** Read exactly the frozen Evidence version; identity checks stay in the consuming Adapter. */
  async readFrozenEvidence(snapshot: ImprovementEvidenceSnapshot): Promise<SessionEvidence> {
    return this.evidenceProvider.readEvidence(snapshot.payloadRef, {
      versionId: snapshot.payloadVersionId,
      expectedEtag: snapshot.payloadEtag,
    });
  }

  async recordSelfRepairHandoff(
    userId: string,
    improvementIdValue: number,
    body: Record<string, unknown>,
  ): Promise<ImprovementView> {
    const unknownFields = Object.keys(body).filter((field) => field !== "version");
    if (unknownFields.length > 0) {
      throw new InsightValidationError(`Agent 处理状态更新包含未知字段: ${unknownFields.join(", ")}`);
    }
    const expectedVersion = Number(body.version);
    if (!Number.isInteger(expectedVersion) || expectedVersion < 1) {
      throw new InsightValidationError("version 必须是正整数");
    }
    const updated = await this.improvementRepo.recordSelfRepairHandoff(
      userId,
      improvementIdValue,
      expectedVersion,
    );
    if (updated === null) throw new InsightNotFoundError("改进项不存在");
    if (updated === "VERSION_CONFLICT") {
      throw new InsightConflictError("改进项已被更新，请刷新后重试");
    }
    if (updated === "STATE_CONFLICT") {
      throw new InsightConflictError("当前状态不允许交给 Agent 处理");
    }
    return updated;
  }

  async updateImprovement(
    userId: string,
    improvementIdValue: number,
    body: Record<string, unknown>,
  ): Promise<ImprovementView> {
    const allowedFields = new Set(["title", "userGuidance", "status", "version"]);
    const immutableFields = Object.keys(body).filter(
      (field) => !allowedFields.has(field),
    );
    if (immutableFields.length > 0) {
      throw new InsightValidationError(
        `改进项证据和归属不可修改: ${immutableFields.join(", ")}`,
      );
    }
    if (
      body.title === undefined &&
      body.userGuidance === undefined &&
      body.status === undefined
    ) {
      throw new InsightValidationError("至少提供 title、userGuidance 或 status");
    }
    const title =
      body.title === undefined ? undefined : String(body.title).trim();
    const userGuidance =
      body.userGuidance === undefined
        ? undefined
        : body.userGuidance == null
          ? null
          : String(body.userGuidance).trim() || null;
    const status =
      body.status === undefined
        ? undefined
        : String(body.status).trim().toUpperCase();
    const expectedVersion =
      body.version === undefined ? undefined : Number(body.version);
    if (title !== undefined && (!title || title.length > 256)) {
      throw new InsightValidationError("title 不能为空且不能超过 256 个字符");
    }
    if ((userGuidance?.length ?? 0) > 5000)
      throw new InsightValidationError("userGuidance 不能超过 5000 个字符");
    if (
      status !== undefined &&
      status !== "ACTIVE" &&
      status !== "IN_PROGRESS" &&
      status !== "RESOLVED" &&
      status !== "ARCHIVED"
    ) {
      throw new InsightValidationError("status 只能是 ACTIVE、IN_PROGRESS、RESOLVED 或 ARCHIVED");
    }
    if (
      expectedVersion !== undefined &&
      (!Number.isInteger(expectedVersion) || expectedVersion < 1)
    ) {
      throw new InsightValidationError("version 必须是正整数");
    }

    if (status !== undefined) {
      const existing = await this.improvementRepo.findItem(
        userId,
        improvementIdValue,
      );
      if (!existing) throw new InsightNotFoundError("改进项不存在");
      const currentStatus = existing.status.toUpperCase();
      const canStart = status === "IN_PROGRESS" && currentStatus === "ACTIVE";
      const canArchive =
        status === "ARCHIVED" &&
        ["ACTIVE", "IN_PROGRESS"].includes(currentStatus);
      const canRestore = status === "ACTIVE" && currentStatus === "ARCHIVED";
      const canResolve =
        status === "RESOLVED" && ["ACTIVE", "IN_PROGRESS"].includes(currentStatus);
      if (!canStart && !canArchive && !canRestore && !canResolve) {
        throw new InsightConflictError(
          `改进项不能从 ${currentStatus} 变更为 ${status}`,
        );
      }
    }

    const updated = await this.improvementRepo.update(
      userId,
      improvementIdValue,
      {
        title,
        userGuidance,
        status: status as "ACTIVE" | "IN_PROGRESS" | "RESOLVED" | "ARCHIVED" | undefined,
        expectedVersion,
      },
    );
    if (updated === null) throw new InsightNotFoundError("改进项不存在");
    if (updated === "VERSION_CONFLICT")
      throw new InsightConflictError("改进项已被更新，请刷新后重试");
    return updated;
  }

  async markImprovementApplied(
    improvementIdValue: number,
    body: Record<string, unknown>,
  ): Promise<{ idempotent: boolean; improvement: ImprovementView }> {
    const allowedFields = new Set(["applyTaskId", "requestId", "appliedBy"]);
    const unknownFields = Object.keys(body).filter(
      (field) => !allowedFields.has(field),
    );
    if (unknownFields.length > 0) {
      throw new InsightValidationError(
        `Apply 回写包含未知字段: ${unknownFields.join(", ")}`,
      );
    }
    const applyTaskId = String(body.applyTaskId ?? "").trim();
    const requestId = String(body.requestId ?? "").trim();
    const appliedBy = String(body.appliedBy ?? "").trim();
    if (!applyTaskId || !requestId || !appliedBy) {
      throw new InsightValidationError(
        "improvementId、applyTaskId、requestId 和 appliedBy 为必填项",
      );
    }
    if (applyTaskId.length > 64) {
      throw new InsightValidationError("applyTaskId 不能超过 64 个字符");
    }
    if (requestId.length > 128 || appliedBy.length > 128) {
      throw new InsightValidationError("requestId 或 appliedBy 不能超过 128 个字符");
    }

    const result = await this.improvementRepo.resolveFromApply({
      improvementId: improvementIdValue,
      applyTaskId,
      requestId,
      appliedBy,
    });
    if (result.outcome === "NOT_FOUND") {
      throw new InsightNotFoundError("改进项不存在");
    }
    if (result.outcome === "STATE_CONFLICT") {
      throw new InsightConflictError(
        `改进项当前状态不允许确认应用: ${result.currentStatus}`,
        "IMPROVEMENT_STATE_CONFLICT",
      );
    }
    return {
      idempotent: result.outcome === "IDEMPOTENT",
      improvement: result.item,
    };
  }
}
