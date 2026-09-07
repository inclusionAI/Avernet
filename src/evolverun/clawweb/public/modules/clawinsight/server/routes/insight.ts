import { Router, type NextFunction, type Request, type Response } from "express";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import {
  InsightConflictError,
  InsightNotFoundError,
  InsightService,
  InsightUnauthorizedError,
  InsightValidationError,
} from "../services/insight/insight-service.js";
import {
  InsightCursorError,
  InsightDataNotReadyError,
} from "../services/insight/providers/insight-read-provider.js";
import { type CompletionState, type InsightQueryScope, type ImprovementDetail, type ImprovementView } from "../services/insight/contracts.js";
import { DEFAULT_INSIGHT_OSS_BUCKET, parseInsightEvidenceRef } from "../services/insight/evidence-ref.js";
import { resolveClawWebOssEnvironment } from "../services/object-storage/clawweb-oss-runtime.js";
import type { InsightMetricDailyRepository, UpsertInsightMetricDailyInput } from "../repositories/insight-metric-daily-repository.js";
import type {
  InsightFailureTaskCleanupScope,
  InsightTaskIndexRepository,
  UpsertInsightFailureTaskInput,
} from "../repositories/insight-task-index-repository.js";
import type { InsightAgentAuthorizer } from "../services/insight/agent-auth.js";
import type { GovernanceRuleProvider } from "../services/insight/governance-rule-provider.js";
import type { InsightAutoRepairRepository } from "../repositories/insight-auto-repair-repository.js";
import type { RuleEvolutionService } from "../services/insight/rule-evolution-service.js";
import { readAutoRepairRule } from "../services/insight/auto-repair-policy.js";
import { createAdminConsentToken } from "../services/insight/admin-consent.js";
import { getClawWebPublicBaseUrl } from "@avernet/clawweb-shared/server/env";
import { InsightTaskCreationError } from "../services/evolve/insight-task-service.js";
import type { CreateInsightTaskInput, InsightTaskCreationResult } from "@avernet/clawevolve/server/internal/module-api";
import type { RepairTaskService } from "../services/repair/repair-runtime.js";
import { RepairError } from "../services/repair/errors.js";

const MAX_PAGE_SIZE = 100;

type InsightTaskCreator = { create(input: CreateInsightTaskInput): Promise<InsightTaskCreationResult> };

function isInsightTaskCreationError(error: unknown): error is InsightTaskCreationError {
  if (error instanceof InsightTaskCreationError) return true;
  if (!(error instanceof Error) || !("code" in error) || !("category" in error)) return false;
  const category = (error as { category?: unknown }).category;
  return typeof (error as { code?: unknown }).code === "string"
    && typeof category === "string"
    && ["validation", "auth", "forbidden", "not_found", "conflict", "source"].includes(category);
}

type InsightRouterOptions = {
  metricWriter?: InsightMetricDailyRepository | null;
  taskWriter?: InsightTaskIndexRepository | null;
  internalWriteToken?: string | null;
  internalWriteEnabled?: boolean;
  agentAuthorizer?: InsightAgentAuthorizer | null;
  ruleProvider?: GovernanceRuleProvider | null;
  autoRepairRepo?: InsightAutoRepairRepository | null;
  insightTaskService?: InsightTaskCreator | null;
  repairService?: RepairTaskService | null;
  ruleEvolutionService?: RuleEvolutionService | null;
};

function stringValue(value: unknown): string | undefined {
  if (Array.isArray(value)) return stringValue(value[0]);
  if (typeof value !== "string") return undefined;
  const normalized = value.trim();
  return normalized || undefined;
}

function resolveUserId(req: Request): string {
  const cookies = req.cookies as Record<string, string> | undefined;
  const userId = [
    req.header("X-Staff-Id"),
    req.header("staff_id"),
    req.header("X-User-Id"),
    cookies?.staff_id,
  ].map((value) => value?.trim()).find(Boolean);
  if (userId) return userId;

  const host = req.get("host") ?? "";
  if (host.includes("localhost") || host.includes("127.0.0.1")) return "dev_local";
  throw new InsightUnauthorizedError("无法识别当前登录用户");
}

function resolveOwnerUserId(req: Request): string {
  const currentUserId = resolveUserId(req);
  const requestedOwnerUserId = stringValue(req.query.ownerUserId);
  if (!requestedOwnerUserId || requestedOwnerUserId === currentUserId) return currentUserId;
  if (!req.isAdmin) throw new InsightUnauthorizedError("只有管理员可以切换用户范围");
  return requestedOwnerUserId;
}

function parseDate(value: unknown, name: string): string | undefined {
  const raw = stringValue(value);
  if (!raw) return undefined;
  const compact = raw.replaceAll("-", "");
  if (!/^\d{8}$/.test(compact)) {
    throw new InsightValidationError(`${name} 必须是 yyyyMMdd 或 yyyy-MM-dd`);
  }
  const year = Number(compact.slice(0, 4));
  const month = Number(compact.slice(4, 6));
  const day = Number(compact.slice(6, 8));
  const date = new Date(Date.UTC(year, month - 1, day));
  if (
    date.getUTCFullYear() !== year
    || date.getUTCMonth() + 1 !== month
    || date.getUTCDate() !== day
  ) {
    throw new InsightValidationError(`${name} 不是有效日期`);
  }
  return compact;
}

function parseBoolean(value: unknown, name: string): boolean | undefined {
  const raw = stringValue(value)?.toLowerCase();
  if (raw === undefined) return undefined;
  if (raw === "true" || raw === "1") return true;
  if (raw === "false" || raw === "0") return false;
  throw new InsightValidationError(`${name} 必须是 true 或 false`);
}

function parsePageSize(value: unknown): number {
  if (value === undefined) return 20;
  const pageSize = Number(stringValue(value));
  if (!Number.isInteger(pageSize) || pageSize < 1 || pageSize > MAX_PAGE_SIZE) {
    throw new InsightValidationError(`pageSize 必须是 1 到 ${MAX_PAGE_SIZE} 的整数`);
  }
  return pageSize;
}

function parseTaskIndex(value: string | string[]): number {
  const taskIndex = Number(Array.isArray(value) ? value[0] : value);
  if (!Number.isInteger(taskIndex) || taskIndex < 0) {
    throw new InsightValidationError("taskIndex 必须是非负整数");
  }
  return taskIndex;
}

function parseImprovementId(value: string | string[]): number {
  const raw = String(Array.isArray(value) ? value[0] : value).trim();
  const improvementId = Number(raw);
  if (!/^\d+$/.test(raw) || !Number.isSafeInteger(improvementId) || improvementId <= 0) {
    throw new InsightValidationError("improvementId 必须是正整数");
  }
  return improvementId;
}

function parseOptionalTaskIndex(value: unknown, name: string): number | undefined {
  const raw = stringValue(value);
  if (raw === undefined) return undefined;
  const taskIndex = Number(raw);
  if (!Number.isInteger(taskIndex) || taskIndex < 0) {
    throw new InsightValidationError(`${name} 必须是非负整数`);
  }
  return taskIndex;
}

function parseCompletionStates(value: unknown): CompletionState[] | undefined {
  if (value === undefined) return undefined;
  const rawValues = (Array.isArray(value) ? value : [value])
    .flatMap((item) => String(item).split(","))
    .map((item) => item.trim())
    .filter(Boolean);
  if (rawValues.length === 0) throw new InsightValidationError("completionStates 不能为空");
  const values = [...new Set(rawValues.map(Number))];
  if (values.some((item) => !Number.isInteger(item) || item < 0 || item > 3)) {
    throw new InsightValidationError("completionStates 只能包含 0、1、2、3");
  }
  return values as CompletionState[];
}

function parseScope(req: Request): InsightQueryScope {
  const from = parseDate(req.query.from, "from");
  const to = parseDate(req.query.to, "to");
  if (from && to && from > to) throw new InsightValidationError("from 不能晚于 to");
  return {
    userId: resolveOwnerUserId(req),
    botId: stringValue(req.query.botId),
    from,
    to,
    isCron: parseBoolean(req.query.isCron, "isCron"),
  };
}

function requireService(service: InsightService | null): InsightService {
  if (!service) throw new InsightDataNotReadyError("Insight Center 数据库或数据 Provider 不可用");
  return service;
}

function bodyRecord(req: Request): Record<string, unknown> {
  if (!req.body || typeof req.body !== "object" || Array.isArray(req.body)) {
    throw new InsightValidationError("请求体必须是 JSON 对象");
  }
  return req.body as Record<string, unknown>;
}

function asRecord(value: unknown, name: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new InsightValidationError(`${name} 必须是 JSON 对象`);
  }
  return value as Record<string, unknown>;
}

function pickValue(record: Record<string, unknown>, camelName: string, snakeName?: string): unknown {
  return record[camelName] ?? (snakeName ? record[snakeName] : undefined);
}

function normalizeString(value: unknown, name: string, options: { required?: boolean; max?: number } = {}): string | null {
  if (value == null) {
    if (options.required) throw new InsightValidationError(`${name} 为必填项`);
    return null;
  }
  const normalized = String(value).trim();
  if (!normalized) {
    if (options.required) throw new InsightValidationError(`${name} 为必填项`);
    return null;
  }
  if (options.max && normalized.length > options.max) {
    throw new InsightValidationError(`${name} 不能超过 ${options.max} 个字符`);
  }
  return normalized;
}

function normalizeInteger(value: unknown, name: string, options: { required?: boolean; min?: number } = {}): number | null {
  if (value == null || value === "") {
    if (options.required) throw new InsightValidationError(`${name} 为必填项`);
    return null;
  }
  const number = Number(value);
  if (!Number.isInteger(number) || (options.min != null && number < options.min)) {
    throw new InsightValidationError(`${name} 必须是合法整数`);
  }
  return number;
}

function normalizeNumber(value: unknown, name: string, options: { required?: boolean; min?: number } = {}): number | null {
  if (value == null || value === "") {
    if (options.required) throw new InsightValidationError(`${name} 为必填项`);
    return null;
  }
  const number = Number(value);
  if (!Number.isFinite(number) || (options.min != null && number < options.min)) {
    throw new InsightValidationError(`${name} 必须是合法数字`);
  }
  return number;
}

function normalizeCompletionState(value: unknown, name: string): CompletionState {
  const number = normalizeInteger(value, name, { required: true, min: 0 });
  if (![0, 1, 2, 3].includes(number ?? -1)) {
    throw new InsightValidationError(`${name} 只能是 0、1、2、3`);
  }
  return number as CompletionState;
}

function normalizeDateCompact(value: unknown, name: string): string {
  const raw = normalizeString(value, name, { required: true, max: 16 }) ?? "";
  const compact = raw.replaceAll("-", "");
  if (!/^\d{8}$/.test(compact)) throw new InsightValidationError(`${name} 必须是 yyyyMMdd 或 yyyy-MM-dd`);
  return compact;
}

function normalizeBoolean(value: unknown): boolean {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value === 1;
  if (typeof value === "string") return ["true", "1", "yes"].includes(value.trim().toLowerCase());
  return false;
}

function normalizeCleanupScope(body: Record<string, unknown>): InsightFailureTaskCleanupScope {
  const rawOwnerUserIds = pickValue(body, "ownerUserIds", "owner_user_ids");
  if (!Array.isArray(rawOwnerUserIds) || rawOwnerUserIds.length === 0 || rawOwnerUserIds.length > 100) {
    throw new InsightValidationError("ownerUserIds 必须包含 1 到 100 个用户 ID");
  }
  const ownerUserIds = [...new Set(rawOwnerUserIds.map((value, index) => (
    normalizeString(value, `ownerUserIds[${index}]`, { required: true, max: 128 }) ?? ""
  )))];
  const rawBotIds = pickValue(body, "botIds", "bot_ids");
  let botIds: string[] | undefined;
  if (rawBotIds != null) {
    if (!Array.isArray(rawBotIds) || rawBotIds.length === 0 || rawBotIds.length > 100) {
      throw new InsightValidationError("botIds 必须包含 1 到 100 个 Bot ID");
    }
    botIds = [...new Set(rawBotIds.map((value, index) => (
      normalizeString(value, `botIds[${index}]`, { required: true, max: 128 }) ?? ""
    )))];
  }
  const rawSourceDt = pickValue(body, "sourceDt", "source_dt");
  return {
    ownerUserIds,
    botIds,
    sourceDt: rawSourceDt == null || rawSourceDt === ""
      ? undefined
      : parseDate(rawSourceDt, "sourceDt"),
  };
}

function requireAdminMaintenance(req: Request): string {
  const actorUserId = resolveUserId(req);
  if (!req.isAdmin) throw new InsightUnauthorizedError("只有管理员可以清理失败任务数据");
  return actorUserId;
}

function requireAdmin(req: Request, message = "只有管理员可以执行该操作"): string {
  const actorUserId = resolveUserId(req);
  if (!req.isAdmin) throw new InsightUnauthorizedError(message);
  return actorUserId;
}

function authorizeAgent(req: Request, options: InsightRouterOptions, scope: string): string {
  if (!options.agentAuthorizer) {
    throw new InsightUnauthorizedError("Insight Agent 机器鉴权未配置");
  }
  return options.agentAuthorizer.authorize(req, scope);
}

function repairAuthHeaders(req: Request, userId: string): Record<string, string> {
  const headers: Record<string, string> = { "x-user-id": userId };
  const cookie = req.header("cookie")?.trim();
  if (cookie) headers.cookie = cookie;
  return headers;
}

function parsePositiveInteger(value: unknown, name: string, fallback: number, max: number): number {
  if (value === undefined) return fallback;
  const parsed = Number(stringValue(value));
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > max) {
    throw new InsightValidationError(`${name} 必须是 1 到 ${max} 的整数`);
  }
  return parsed;
}

function parseNonNegativeInteger(value: unknown, name: string, fallback: number, max: number): number {
  if (value === undefined) return fallback;
  const parsed = Number(stringValue(value));
  if (!Number.isInteger(parsed) || parsed < 0 || parsed > max) {
    throw new InsightValidationError(`${name} 必须是 0 到 ${max} 的整数`);
  }
  return parsed;
}

function parseCommaSeparated(value: unknown, name: string, maxItems = 50): string[] | undefined {
  const raw = stringValue(value);
  if (!raw) return undefined;
  const items = [...new Set(raw.split(",").map((item) => item.trim()).filter(Boolean))];
  if (!items.length || items.length > maxItems) {
    throw new InsightValidationError(`${name} 必须包含 1 到 ${maxItems} 个值`);
  }
  return items;
}

function parseIsoDate(value: unknown, name: string): Date | undefined {
  const raw = normalizeString(value, name, { max: 64 });
  if (!raw) return undefined;
  // Express query parsing converts an unescaped `+08:00` offset into a space.
  const normalized = raw.replace(/([T ]\d{2}:\d{2}:\d{2}) (\d{2}:\d{2})$/, "$1+$2");
  const timestamp = Date.parse(normalized);
  if (!Number.isFinite(timestamp)) {
    throw new InsightValidationError(`${name} 必须是合法的 ISO 8601 时间`);
  }
  return new Date(timestamp);
}

function normalizeIngestItems(body: Record<string, unknown>): UpsertInsightFailureTaskInput[] {
  const rawItems = body.items;
  if (!Array.isArray(rawItems) || rawItems.length === 0 || rawItems.length > 500) {
    throw new InsightValidationError("items 必须包含 1 到 500 条失败任务");
  }
  const topBatchId = normalizeString(pickValue(body, "batchId", "batch_id"), "batchId", { max: 64 });
  const topDataAsOf = normalizeString(pickValue(body, "dataAsOf", "data_as_of"), "dataAsOf", { max: 32 });

  return rawItems.map((raw, index) => {
    const prefix = `items[${index}]`;
    const item = asRecord(raw, prefix);
    const sourceDt = normalizeDateCompact(pickValue(item, "sourceDt", "source_dt"), `${prefix}.sourceDt`);
    const ownerUserId = normalizeString(
      pickValue(item, "ownerUserId", "owner_user_id") ?? pickValue(item, "userId", "user_id"),
      `${prefix}.ownerUserId`,
      { required: true, max: 128 },
    ) ?? "";
    const botId = normalizeString(pickValue(item, "botId", "bot_id"), `${prefix}.botId`, { required: true, max: 128 }) ?? "";
    const botName = normalizeString(pickValue(item, "botName", "bot_name"), `${prefix}.botName`, { max: 256 }) ?? botId;
    const sessionId = normalizeString(pickValue(item, "sessionId", "session_id"), `${prefix}.sessionId`, { required: true, max: 128 }) ?? "";
    const taskIndex = normalizeInteger(pickValue(item, "taskIndex", "task_index"), `${prefix}.taskIndex`, { required: true, min: 0 }) ?? 0;
    const taskDescription = normalizeString(
      pickValue(item, "taskDescription", "task_description"),
      `${prefix}.taskDescription`,
      { required: true, max: 1000 },
    ) ?? "";
    const isComplete = normalizeCompletionState(pickValue(item, "isComplete", "is_complete"), `${prefix}.isComplete`);
    const failureClass = normalizeString(
      pickValue(item, "failureClass", "failure_class") ?? pickValue(item, "taskFailureClass", "task_failure_class"),
      `${prefix}.failureClass`,
      { max: 64 },
    ) ?? (isComplete === 1 ? "COMPLETED" : "UNKNOWN");
    const payloadRef = normalizeString(pickValue(item, "payloadRef", "payload_ref"), `${prefix}.payloadRef`, { required: true, max: 512 }) ?? "";
    try {
      const evidenceEnvironment = resolveClawWebOssEnvironment(process.env);
      parseInsightEvidenceRef(payloadRef, {
        expectedBucket: process.env.INSIGHT_OSS_BUCKET ?? DEFAULT_INSIGHT_OSS_BUCKET,
        ...(evidenceEnvironment === "pre"
          ? { allowedEnvironments: ["pre", "prod"] as const }
          : { expectedEnvironment: "prod" }),
        expectedUserId: ownerUserId,
        expectedBotId: botId,
        expectedSourceDt: sourceDt,
        expectedSessionId: sessionId,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      throw new InsightValidationError(`${prefix}.payloadRef 不合法: ${message}`);
    }
    const payloadEtag = normalizeString(pickValue(item, "payloadEtag", "payload_etag"), `${prefix}.payloadEtag`, { required: true, max: 128 }) ?? "";

    return {
      sourceDt,
      ownerUserId,
      botId,
      botName,
      sessionId,
      taskIndex,
      taskDescription,
      isComplete,
      failureClass,
      judgeReasonSummary: normalizeString(
        pickValue(item, "judgeReasonSummary", "judge_reason_summary") ?? item.reasoning,
        `${prefix}.judgeReasonSummary`,
        { max: 1000 },
      ),
      sessionStartTime: normalizeString(pickValue(item, "sessionStartTime", "session_start_time"), `${prefix}.sessionStartTime`, { max: 32 }),
      sessionEndTime: normalizeString(pickValue(item, "sessionEndTime", "session_end_time"), `${prefix}.sessionEndTime`, { max: 32 }),
      sessionDurationSeconds: normalizeInteger(pickValue(item, "sessionDurationSeconds", "session_duration_seconds"), `${prefix}.sessionDurationSeconds`, { min: 0 }),
      isCron: normalizeBoolean(pickValue(item, "isCron", "is_cron")),
      payloadRef,
      payloadEtag,
      payloadVersionId: normalizeString(pickValue(item, "payloadVersionId", "payload_version_id"), `${prefix}.payloadVersionId`, { max: 128 }),
      batchId: normalizeString(pickValue(item, "batchId", "batch_id"), `${prefix}.batchId`, { max: 64 }) ?? topBatchId ?? sourceDt,
      dataAsOf: normalizeString(pickValue(item, "dataAsOf", "data_as_of"), `${prefix}.dataAsOf`, { max: 32 }) ?? topDataAsOf ?? new Date().toISOString(),
      judgedAt: normalizeString(pickValue(item, "judgedAt", "judged_at"), `${prefix}.judgedAt`, { max: 32 }),
    };
  });
}

function normalizeFailureDistribution(value: unknown, name: string): Record<string, number> {
  const record = asRecord(value ?? {}, name);
  const result: Record<string, number> = {};
  if (Object.keys(record).length > 100) throw new InsightValidationError(`${name} 最多包含 100 个失败分类`);
  for (const [failureClass, rawCount] of Object.entries(record)) {
    const normalizedClass = normalizeString(failureClass, `${name} key`, { required: true, max: 64 }) ?? "";
    result[normalizedClass] = normalizeNumber(rawCount, `${name}.${normalizedClass}`, { required: true, min: 0 }) ?? 0;
  }
  return result;
}

function normalizeMetricItems(body: Record<string, unknown>): UpsertInsightMetricDailyInput[] {
  const rawItems = body.items;
  if (!Array.isArray(rawItems) || rawItems.length === 0 || rawItems.length > 500) {
    throw new InsightValidationError("items 必须包含 1 到 500 条指标日快照");
  }
  const topBatchId = normalizeString(pickValue(body, "batchId", "batch_id"), "batchId", { max: 64 });
  const topDataAsOf = normalizeString(pickValue(body, "dataAsOf", "data_as_of"), "dataAsOf", { max: 32 });

  return rawItems.map((raw, index) => {
    const prefix = `items[${index}]`;
    const item = asRecord(raw, prefix);
    const totalTaskCount = normalizeNumber(pickValue(item, "totalTaskCount", "total_task_count"), `${prefix}.totalTaskCount`, { required: true, min: 0 }) ?? 0;
    const validTaskCount = normalizeNumber(pickValue(item, "validTaskCount", "valid_task_count"), `${prefix}.validTaskCount`, { required: true, min: 0 }) ?? 0;
    const completeTaskCount = normalizeNumber(pickValue(item, "completeTaskCount", "complete_task_count"), `${prefix}.completeTaskCount`, { required: true, min: 0 }) ?? 0;
    const capabilityTaskCount = normalizeNumber(pickValue(item, "capabilityTaskCount", "capability_task_count"), `${prefix}.capabilityTaskCount`, { required: true, min: 0 }) ?? 0;
    const capabilityCompleteTaskCount = normalizeNumber(
      pickValue(item, "capabilityCompleteTaskCount", "capability_complete_task_count"),
      `${prefix}.capabilityCompleteTaskCount`,
      { required: true, min: 0 },
    ) ?? 0;
    const autoCompleteTaskCount = normalizeNumber(pickValue(item, "autoCompleteTaskCount", "auto_complete_task_count"), `${prefix}.autoCompleteTaskCount`, { required: true, min: 0 }) ?? 0;
    if (validTaskCount > totalTaskCount || completeTaskCount > validTaskCount) {
      throw new InsightValidationError(`${prefix} 必须满足 completeTaskCount <= validTaskCount <= totalTaskCount`);
    }
    if (capabilityCompleteTaskCount > capabilityTaskCount || capabilityTaskCount > totalTaskCount) {
      throw new InsightValidationError(`${prefix} 必须满足 capabilityCompleteTaskCount <= capabilityTaskCount <= totalTaskCount`);
    }
    if (autoCompleteTaskCount > capabilityCompleteTaskCount) {
      throw new InsightValidationError(`${prefix}.autoCompleteTaskCount 不能超过 capabilityCompleteTaskCount`);
    }
    return {
      sourceDt: normalizeDateCompact(pickValue(item, "sourceDt", "source_dt"), `${prefix}.sourceDt`),
      ownerUserId: normalizeString(
        pickValue(item, "ownerUserId", "owner_user_id") ?? pickValue(item, "userId", "user_id"),
        `${prefix}.ownerUserId`,
        { required: true, max: 128 },
      ) ?? "",
      botId: normalizeString(pickValue(item, "botId", "bot_id"), `${prefix}.botId`, { required: true, max: 128 }) ?? "",
      botName: normalizeString(pickValue(item, "botName", "bot_name"), `${prefix}.botName`, { max: 256 })
        ?? normalizeString(pickValue(item, "botId", "bot_id"), `${prefix}.botId`, { required: true, max: 128 })
        ?? "",
      isCron: normalizeBoolean(pickValue(item, "isCron", "is_cron")),
      totalTaskCount,
      validTaskCount,
      completeTaskCount,
      capabilityTaskCount,
      capabilityCompleteTaskCount,
      autoCompleteTaskCount,
      failureDistribution: normalizeFailureDistribution(
        pickValue(item, "failureDistribution", "failure_distribution"),
        `${prefix}.failureDistribution`,
      ),
      batchId: normalizeString(pickValue(item, "batchId", "batch_id"), `${prefix}.batchId`, { max: 64 }) ?? topBatchId ?? "unknown",
      dataAsOf: normalizeString(pickValue(item, "dataAsOf", "data_as_of"), `${prefix}.dataAsOf`, { max: 32 }) ?? topDataAsOf ?? new Date().toISOString(),
    };
  });
}

function requireInternalWriter(options: InsightRouterOptions): InsightTaskIndexRepository {
  const writer = options.taskWriter ?? null;
  if (!writer) throw new InsightDataNotReadyError("失败任务写入表不可用");
  return writer;
}

function requireInternalMetricWriter(options: InsightRouterOptions): InsightMetricDailyRepository {
  const writer = options.metricWriter ?? null;
  if (!writer) throw new InsightDataNotReadyError("指标日快照写入表不可用");
  return writer;
}

function requireAutoRepairRepo(options: InsightRouterOptions): InsightAutoRepairRepository {
  const repository = options.autoRepairRepo ?? null;
  if (!repository) throw new InsightDataNotReadyError("自动修复授权存储不可用");
  return repository;
}

function authorizeInternalWrite(req: Request, options: InsightRouterOptions): void {
  const enabled = options.internalWriteEnabled ?? process.env.INSIGHT_INTERNAL_WRITE_ENABLED !== "false";
  if (!enabled) throw new InsightUnauthorizedError("Insight 内部写入接口未开启");
  const expectedToken = options.internalWriteToken ?? process.env.INSIGHT_INTERNAL_WRITE_TOKEN;
  if (!expectedToken) return;
  const actualToken = req.header("X-Insight-Write-Token") ?? req.header("Authorization")?.replace(/^Bearer\s+/i, "");
  if (actualToken !== expectedToken) throw new InsightUnauthorizedError("Insight 内部写入 Token 无效");
}

function errorResponse(error: unknown, _req: Request, res: Response, next: NextFunction): void {
  if (error instanceof InsightUnauthorizedError) {
    res.status(401).json({ code: error.code, message: error.message });
    return;
  }
  if (error instanceof InsightValidationError || error instanceof InsightCursorError) {
    res.status(400).json({ code: error.code, message: error.message });
    return;
  }
  if (error instanceof InsightNotFoundError) {
    res.status(404).json({ code: error.code, message: error.message });
    return;
  }
  if (error instanceof InsightConflictError) {
    res.status(409).json({ code: error.code, message: error.message });
    return;
  }
  if (error instanceof InsightDataNotReadyError) {
    res.status(503).json({ code: error.code, message: error.message });
    return;
  }
  next(error);
}

export function createInsightRouter(service: InsightService | null, options: InsightRouterOptions = {}): Router {
  const router = Router();

  router.get("/evidence-access/:ownerUserId/:improvementId/:sessionId/:taskIndex", asyncHandler(async (req, res) => {
    const ownerUserId = stringValue(req.params.ownerUserId);
    const sessionId = stringValue(req.params.sessionId);
    if (!ownerUserId || !sessionId) throw new InsightValidationError("Evidence 访问地址参数不能为空");
    res.set("Cache-Control", "no-store");
    res.json(await requireService(service).getEvidenceByReference(
      ownerUserId,
      parseImprovementId(req.params.improvementId),
      sessionId,
      parseTaskIndex(req.params.taskIndex),
    ));
  }));

  router.post("/internal/failure-tasks/upsert", asyncHandler(async (req, res) => {
    authorizeInternalWrite(req, options);
    const result = await requireInternalWriter(options).upsertMany(normalizeIngestItems(bodyRecord(req)));
    res.status(201).json({ success: true, data: result });
  }));

  router.post("/internal/metrics/daily/upsert", asyncHandler(async (req, res) => {
    authorizeInternalWrite(req, options);
    const result = await requireInternalMetricWriter(options).upsertMany(normalizeMetricItems(bodyRecord(req)));
    res.status(201).json({ success: true, data: result });
  }));

  router.post("/admin/failure-tasks/cleanup/preview", asyncHandler(async (req, res) => {
    requireAdminMaintenance(req);
    const scope = normalizeCleanupScope(bodyRecord(req));
    const result = await requireInternalWriter(options).previewCleanup(scope);
    res.json({ success: true, data: { ...result, botIds: scope.botIds ?? null, sourceDt: scope.sourceDt ?? null } });
  }));

  router.post("/admin/failure-tasks/cleanup/execute", asyncHandler(async (req, res) => {
    const actorUserId = requireAdminMaintenance(req);
    const scope = normalizeCleanupScope(bodyRecord(req));
    const result = await requireInternalWriter(options).cleanup(scope);
    console.info(
      `[insight-admin] failure task cleanup actor=${actorUserId} owners=${scope.ownerUserIds.join(",")} bots=${scope.botIds?.join(",") ?? "ALL"} sourceDt=${scope.sourceDt ?? "ALL"} deleted=${result.matched}`,
    );
    res.json({ success: true, data: { deleted: result.matched, byOwner: result.byOwner, botIds: scope.botIds ?? null, sourceDt: scope.sourceDt ?? null } });
  }));

  router.post("/internal/improvements/:improvementId/applied", asyncHandler(async (req, res) => {
    authorizeInternalWrite(req, options);
    const result = await requireService(service).markImprovementApplied(
      parseImprovementId(req.params.improvementId),
      bodyRecord(req),
    );
    res.json({ success: true, ...result });
  }));

  router.get("/internal/governance/rules", asyncHandler(async (req, res) => {
    authorizeAgent(req, options, "rule.read");
    if (!options.ruleProvider) throw new InsightDataNotReadyError("治理规则读取器不可用");
    const snapshot = await options.ruleProvider.read();
    res.set("Cache-Control", "private, max-age=30");
    if (snapshot.etag) res.set("ETag", snapshot.etag);
    res.json({ ...snapshot.document, source: snapshot.source, etag: snapshot.etag });
  }));

  router.get("/internal/governance/rejections", asyncHandler(async (req, res) => {
    authorizeAgent(req, options, "rejection.read");
    const items = await requireService(service).listRecentRejections({
      days: parsePositiveInteger(req.query.days, "days", 15, 90),
      ownerUserId: stringValue(req.query.ownerUserId),
      botId: stringValue(req.query.botId),
      sourceRuleId: stringValue(req.query.sourceRuleId),
      limit: parsePositiveInteger(req.query.limit, "limit", 100, 500),
    });
    res.json({ items });
  }));

  router.get("/internal/governance/actions", asyncHandler(async (req, res) => {
    const result = await requireService(service).listGovernanceActions({
      ownerUserId: normalizeString(req.query.ownerUserId, "ownerUserId", { max: 128 }) ?? undefined,
      botId: normalizeString(req.query.botId, "botId", { max: 128 }) ?? undefined,
      statuses: parseCommaSeparated(req.query.status, "status", 10),
      adminReviewStatuses: parseCommaSeparated(req.query.adminReviewStatus, "adminReviewStatus", 10),
      sourceRuleId: normalizeString(req.query.sourceRuleId, "sourceRuleId", { max: 64 }) ?? undefined,
      since: parseIsoDate(req.query.since, "since"),
      fields: parseCommaSeparated(req.query.fields, "fields", 50),
      limit: parsePositiveInteger(req.query.limit, "limit", 50, 200),
      offset: parseNonNegativeInteger(req.query.offset, "offset", 0, 1_000_000),
    });
    res.set("Cache-Control", "no-store");
    res.json(result);
  }));

  router.post("/internal/governance/actions", asyncHandler(async (req, res) => {
    const body = bodyRecord(req);
    const ownerUserId = normalizeString(body.ownerUserId, "ownerUserId", { required: true, max: 128 }) ?? "";
    const sourceUserId = normalizeString(body.sourceOwnerUserId, "sourceOwnerUserId", { max: 128 }) ?? ownerUserId;
    const idempotencyKey = req.header("Idempotency-Key")?.trim();
    if (!idempotencyKey || idempotencyKey.length > 128) {
      throw new InsightValidationError("Idempotency-Key 必填且不能超过 128 个字符");
    }
    normalizeString(body.sourceRuleId, "sourceRuleId", { required: true, max: 64 });
    const actionTypeRaw = normalizeString(body.actionType, "actionType", { required: true, max: 32 })?.toUpperCase();
    const actionType = actionTypeRaw === "DIRECT_EVOLUTION" || actionTypeRaw === "ASSIGN_OWNER"
      ? actionTypeRaw
      : null;
    if (!actionType) throw new InsightValidationError("actionType 不合法");
    const result = await requireService(service).createImprovement({
      actorUserId: "governance-agent",
      ownerUserId,
      sourceUserId,
      sourceType: "ADMIN_RULE",
    }, idempotencyKey, body, { notify: false });
    let improvement: ImprovementDetail | ImprovementView = result.improvement;
    let autoTaskId: string | null = null;
    if (
      result.created
      && improvement.actionType === "DIRECT_EVOLUTION"
      && improvement.sourceRuleId
      && options.ruleProvider
      && options.autoRepairRepo
      && (options.insightTaskService || options.repairService)
    ) {
      try {
        const rule = await readAutoRepairRule(options.ruleProvider, improvement.sourceRuleId, "DIRECT_EVOLUTION");
        const grant = rule
          ? await options.autoRepairRepo.findLatestActiveGrantForRule(improvement.ownerUserId, rule)
          : null;
        if (grant?.autoExecute) {
          improvement = await requireService(service).reviewAdminAction(
            "governance-auto-owner-consent",
            improvement.improvementId,
            { decision: "APPROVE", version: improvement.version },
            { notifyApproved: false },
          );
          const requestId = `insight-auto:${improvement.improvementId}:grant:${grant.grantId}`;
          if (options.repairService) {
            const task = await options.repairService.createTask({
              actorUserId: grant.ownerUserId,
              authHeaders: repairAuthHeaders(req, grant.ownerUserId),
              body: {
                taskName: `${improvement.title.slice(0, 119)} · 自动修复`,
                symptom: improvement.title,
                repairDirection: improvement.suggestedAction ?? improvement.userGuidance ?? undefined,
                targetUserId: grant.ownerUserId,
                botId: grant.botId,
                insightImprovementId: improvement.improvementId,
                insightRequestId: requestId,
                authorizationGrantId: grant.grantId,
                crossBotConfirmed: improvement.ownerUserId !== improvement.botOwnerUserId || grant.botId !== improvement.botId,
              },
            });
            autoTaskId = String(task.taskId);
          } else if (options.insightTaskService) {
            const task = await options.insightTaskService.create({
              taskType: "full",
              taskName: `${improvement.title.slice(0, 119)} · 自动修复`,
              remark: (improvement.suggestedAction ?? improvement.userGuidance ?? "Owner 已授权的自动修复项").slice(0, 1000),
              userId: grant.ownerUserId,
              botId: grant.botId,
              improvementId: improvement.improvementId,
              crossBotConfirmed: improvement.ownerUserId !== improvement.botOwnerUserId || grant.botId !== improvement.botId,
              maxRounds: 3,
              nodeCommandYamls: undefined,
              forceMessage: false,
              idempotencyKey: requestId,
              actorUserId: null,
              authorizationGrantId: grant.grantId,
              createdByOverride: "insight-auto-repair",
              callbackUrl: (taskId, stepId) => `${req.protocol}://${req.get("host")}/api/evolve/internal/tasks/${encodeURIComponent(taskId)}/steps/${encodeURIComponent(stepId)}/bot-callback`,
            });
            autoTaskId = task.task.task_id;
          }
        }
      } catch (error) {
        console.warn(`[clawweb] Governance owner auto-execution skipped improvement=${improvement.improvementId}: ${error instanceof Error ? error.message : String(error)}`);
      }
    }
    res.status(autoTaskId ? 201 : result.created ? 201 : 200).json({
      ...improvement,
      ...(autoTaskId ? { autoTaskId, autoExecution: true } : {}),
    });
  }));

  router.post("/internal/governance/actions/:improvementId/mark-handled", asyncHandler(async (req, res) => {
    const improvement = await requireService(service).markGovernanceActionHandled(
      parseImprovementId(req.params.improvementId),
      bodyRecord(req),
    );
    res.json(improvement);
  }));

  router.get("/internal/governance/verification-candidates", asyncHandler(async (req, res) => {
    const items = await requireService(service).listVerificationCandidates(
      parsePositiveInteger(req.query.limit, "limit", 100, 200),
    );
    res.json({ items });
  }));

  router.get("/internal/governance/verification-candidates/open", asyncHandler(async (req, res) => {
    const items = await requireService(service).listOpenVerificationCandidates(
      parsePositiveInteger(req.query.limit, "limit", 100, 200),
    );
    res.json({ items });
  }));

  router.post("/internal/governance/verification-results/force-resolved", asyncHandler(async (req, res) => {
    const agentId = authorizeAgent(req, options, "verification.force");
    const body = bodyRecord(req);
    const improvement = await requireService(service).recordForcedVerificationResult({
      ...body,
      operatedBy: body.operatedBy ?? agentId,
    });
    res.json({ improvement });
  }));

  router.post("/internal/governance/verification-results/open", asyncHandler(async (req, res) => {
    const improvement = await requireService(service).recordOpenVerificationResult(bodyRecord(req));
    let ruleEvolutionProposal = null;
    if (improvement.verificationStatus === "VERIFIED" && options.ruleEvolutionService) {
      try {
        ruleEvolutionProposal = await options.ruleEvolutionService.maybeCreateFromVerification(improvement);
      } catch (error) {
        console.warn(`[clawweb] Rule evolution proposal creation skipped improvement=${improvement.improvementId}: ${error instanceof Error ? error.message : String(error)}`);
      }
    }
    res.json({ improvement, ...(ruleEvolutionProposal ? { ruleEvolutionProposal } : {}) });
  }));

  router.post("/internal/governance/verification-results/resolved", asyncHandler(async (req, res) => {
    const improvement = await requireService(service).recordResolvedVerificationResult(bodyRecord(req));
    let ruleEvolutionProposal = null;
    if (improvement.verificationStatus === "VERIFIED" && options.ruleEvolutionService) {
      try {
        ruleEvolutionProposal = await options.ruleEvolutionService.maybeCreateFromVerification(improvement);
      } catch (error) {
        console.warn(`[clawweb] Rule evolution proposal creation skipped improvement=${improvement.improvementId}: ${error instanceof Error ? error.message : String(error)}`);
      }
    }
    res.json({ improvement, ...(ruleEvolutionProposal ? { ruleEvolutionProposal } : {}) });
  }));

  router.post("/internal/governance/verification-results", asyncHandler(async (req, res) => {
    const improvement = await requireService(service).recordVerificationResult(bodyRecord(req));
    let ruleEvolutionProposal = null;
    if (improvement.verificationStatus === "VERIFIED" && options.ruleEvolutionService) {
      try {
        ruleEvolutionProposal = await options.ruleEvolutionService.maybeCreateFromVerification(improvement);
      } catch (error) {
        console.warn(`[clawweb] Rule evolution proposal creation skipped improvement=${improvement.improvementId}: ${error instanceof Error ? error.message : String(error)}`);
      }
    }
    res.json({ improvement, ...(ruleEvolutionProposal ? { ruleEvolutionProposal } : {}) });
  }));

  router.get("/overview", asyncHandler(async (req, res) => {
    res.json(await requireService(service).getOverview(parseScope(req)));
  }));

  router.get("/trend", asyncHandler(async (req, res) => {
    res.json(await requireService(service).getTrend(parseScope(req), {
      includeAdminMetrics: req.isAdmin === true,
    }));
  }));

  router.get("/failure-tasks", asyncHandler(async (req, res) => {
    const scope = parseScope(req);
    res.json(await requireService(service).listFailureTasks({
      ...scope,
      failureClass: stringValue(req.query.failureClass),
      completionStates: parseCompletionStates(req.query.completionStates),
      cursor: stringValue(req.query.cursor),
      pageSize: parsePageSize(req.query.pageSize),
    }));
  }));

  router.get("/failure-tasks/:sessionId/tasks/:taskIndex", asyncHandler(async (req, res) => {
    const userId = resolveOwnerUserId(req);
    const sessionId = stringValue(req.params.sessionId);
    if (!sessionId) throw new InsightValidationError("sessionId 不能为空");
    res.json(await requireService(service).getFailureTaskDetail(
      userId,
      sessionId,
      parseTaskIndex(req.params.taskIndex),
      parseOptionalTaskIndex(req.query.anchorTaskIndex, "anchorTaskIndex"),
    ));
  }));

  router.get("/failure-tasks/:sessionId/tasks/:taskIndex/timeline", asyncHandler(async (req, res) => {
    const userId = resolveOwnerUserId(req);
    const sessionId = stringValue(req.params.sessionId);
    if (!sessionId) throw new InsightValidationError("sessionId 不能为空");
    res.json(await requireService(service).getTimeline(
      userId,
      sessionId,
      parseTaskIndex(req.params.taskIndex),
      {
        cursor: stringValue(req.query.cursor),
        blockId: stringValue(req.query.blockId),
        position: stringValue(req.query.position) === "tail" ? "tail" : undefined,
        all: parseBoolean(req.query.all, "all"),
        pageSize: parsePageSize(req.query.pageSize),
        anchorTaskIndex: parseOptionalTaskIndex(req.query.anchorTaskIndex, "anchorTaskIndex"),
      },
    ));
  }));

  router.post("/improvements", asyncHandler(async (req, res) => {
    const actorUserId = resolveUserId(req);
    const body = bodyRecord(req);
    const requestedOwnerUserId = normalizeString(body.ownerUserId, "ownerUserId", { max: 128 });
    if (requestedOwnerUserId && requestedOwnerUserId !== actorUserId && !req.isAdmin) {
      throw new InsightUnauthorizedError("只有管理员可以为其他用户创建改进项");
    }
    const ownerUserId = requestedOwnerUserId || actorUserId;
    const requestedSourceUserId = normalizeString(body.sourceOwnerUserId, "sourceOwnerUserId", { max: 128 });
    if (requestedSourceUserId && requestedSourceUserId !== actorUserId && !req.isAdmin) {
      throw new InsightUnauthorizedError("只有管理员可以使用其他用户的失败任务创建改进项");
    }
    const sourceUserId = requestedSourceUserId || ownerUserId;
    const idempotencyKey = req.header("Idempotency-Key")?.trim();
    if (!idempotencyKey || idempotencyKey.length > 128) {
      throw new InsightValidationError("Idempotency-Key 必填且不能超过 128 个字符");
    }
    const result = await requireService(service).createImprovement({
      actorUserId,
      ownerUserId,
      sourceUserId,
      sourceType: req.isAdmin && (requestedOwnerUserId || requestedSourceUserId) ? "ADMIN_SELECTED" : "USER_SELECTED",
    }, idempotencyKey, body);
    res.status(result.created ? 201 : 200).json(result.improvement);
  }));

  router.post("/improvements/batch", asyncHandler(async (req, res) => {
    const actorUserId = resolveUserId(req);
    const body = bodyRecord(req);
    if (!Array.isArray(body.items) || body.items.length === 0 || body.items.length > 50) {
      throw new InsightValidationError("items 必须包含 1 到 50 个改进项");
    }
    const batchRequestId = req.header("Idempotency-Key")?.trim();
    if (!batchRequestId || batchRequestId.length > 96) {
      throw new InsightValidationError("Idempotency-Key 必填且不能超过 96 个字符");
    }
    const items = body.items.map((raw, index) => {
      const item = asRecord(raw, `items[${index}]`);
      const requestedOwnerUserId = normalizeString(item.ownerUserId, `items[${index}].ownerUserId`, { max: 128 });
      if (requestedOwnerUserId && requestedOwnerUserId !== actorUserId && !req.isAdmin) {
        throw new InsightUnauthorizedError("只有管理员可以为其他用户创建改进项");
      }
      const ownerUserId = requestedOwnerUserId || actorUserId;
      const requestedSourceUserId = normalizeString(item.sourceOwnerUserId, `items[${index}].sourceOwnerUserId`, { max: 128 });
      if (requestedSourceUserId && requestedSourceUserId !== actorUserId && !req.isAdmin) {
        throw new InsightUnauthorizedError("只有管理员可以使用其他用户的失败任务创建改进项");
      }
      const sourceUserId = requestedSourceUserId || ownerUserId;
      return {
        identity: {
          actorUserId,
          ownerUserId,
          sourceUserId,
          sourceType: req.isAdmin && (requestedOwnerUserId || requestedSourceUserId)
            ? "ADMIN_SELECTED" as const
            : "USER_SELECTED" as const,
        },
        idempotencyKey: `${batchRequestId}:${index}`,
        body: item,
      };
    });
    const improvements = await requireService(service).createImprovementsBatch(items);
    res.status(201).json({ items: improvements });
  }));

  router.get("/admin/improvements", asyncHandler(async (req, res) => {
    requireAdmin(req, "只有管理员可以查看全部改进项");
    res.json(await requireService(service).listAdminImprovements({
      ownerUserId: stringValue(req.query.ownerUserId),
      botId: stringValue(req.query.botId),
      status: stringValue(req.query.status),
      adminReviewStatus: stringValue(req.query.adminReviewStatus),
      includeAll: parseBoolean(req.query.includeAll, "includeAll"),
      cursor: stringValue(req.query.cursor),
      pageSize: parsePageSize(req.query.pageSize),
    }));
  }));

  router.get("/admin/improvements/:improvementId", asyncHandler(async (req, res) => {
    requireAdmin(req, "只有管理员可以查看全部改进项详情");
    res.json(await requireService(service).getAdminImprovement(
      parseImprovementId(req.params.improvementId),
    ));
  }));

  router.get("/admin/governance/rule-evolution", asyncHandler(async (req, res) => {
    requireAdmin(req, "只有管理员可以查看规则进化建议");
    if (!options.ruleEvolutionService) throw new InsightDataNotReadyError("规则进化服务不可用");
    const status = stringValue(req.query.status)?.toUpperCase();
    if (status && !["PENDING", "APPROVED", "REJECTED"].includes(status)) {
      throw new InsightValidationError("规则进化建议 status 不合法");
    }
    res.json({ items: await options.ruleEvolutionService.list(status as "PENDING" | "APPROVED" | "REJECTED" | undefined) });
  }));

  router.post("/admin/governance/rule-evolution/:proposalId/review", asyncHandler(async (req, res) => {
    const actorUserId = requireAdmin(req, "只有管理员可以审核规则进化建议");
    if (!options.ruleEvolutionService) throw new InsightDataNotReadyError("规则进化服务不可用");
    const result = await options.ruleEvolutionService.review(
      actorUserId,
      parseImprovementId(req.params.proposalId),
      bodyRecord(req),
    );
    res.json(result);
  }));

  router.post("/admin/improvements/:improvementId/review", asyncHandler(async (req, res) => {
    const actorUserId = requireAdmin(req, "只有管理员可以审核治理 Action");
    const body = bodyRecord(req);
    const insightService = requireService(service);
    const decision = String(body.decision ?? "").trim().toUpperCase();
    let improvement = await insightService.reviewAdminAction(
      actorUserId,
      parseImprovementId(req.params.improvementId),
      body,
      { notifyApproved: false },
    );
    let autoRepairStarted = false;
    if (
      decision === "APPROVE"
      && improvement.actionType === "DIRECT_EVOLUTION"
      && improvement.sourceRuleId
      && options.ruleProvider
      && options.autoRepairRepo
      && (options.insightTaskService || options.repairService)
    ) {
      let activeGrantMatched = false;
      try {
        const rule = await readAutoRepairRule(options.ruleProvider, improvement.sourceRuleId, "DIRECT_EVOLUTION");
        const activeGrant = rule
          ? await options.autoRepairRepo.findLatestActiveGrantForRule(improvement.ownerUserId, rule)
          : null;
        if (activeGrant) {
          activeGrantMatched = true;
          const requestId = `insight-auto:${improvement.improvementId}:grant:${activeGrant.grantId}`;
          if (options.repairService) {
            await options.repairService.createTask({
              actorUserId: activeGrant.ownerUserId,
              authHeaders: repairAuthHeaders(req, activeGrant.ownerUserId),
              body: {
                taskName: `${improvement.title.slice(0, 119)} · 自动修复`,
                symptom: improvement.title,
                repairDirection: improvement.suggestedAction ?? improvement.userGuidance ?? undefined,
                targetUserId: activeGrant.ownerUserId,
                botId: activeGrant.botId,
                insightImprovementId: improvement.improvementId,
                insightRequestId: requestId,
                authorizationGrantId: activeGrant.grantId,
                crossBotConfirmed: improvement.ownerUserId !== improvement.botOwnerUserId
                  || activeGrant.botId !== improvement.botId,
              },
            });
          } else if (options.insightTaskService) {
            await options.insightTaskService.create({
              taskType: "full",
              taskName: `${improvement.title.slice(0, 119)} · 自动修复`,
              remark: (improvement.suggestedAction ?? improvement.userGuidance ?? "Admin 已批准的自动修复项").slice(0, 1000),
              userId: activeGrant.ownerUserId,
              botId: activeGrant.botId,
              improvementId: improvement.improvementId,
              crossBotConfirmed: improvement.ownerUserId !== improvement.botOwnerUserId
                || activeGrant.botId !== improvement.botId,
              maxRounds: 3,
              nodeCommandYamls: undefined,
              forceMessage: false,
              idempotencyKey: requestId,
              actorUserId: null,
              authorizationGrantId: activeGrant.grantId,
              createdByOverride: "insight-auto-repair",
              callbackUrl: (taskId, stepId) => `${req.protocol}://${req.get("host")}/api/evolve/internal/tasks/${encodeURIComponent(taskId)}/steps/${encodeURIComponent(stepId)}/bot-callback`,
            });
          }
        }
      } catch (error) {
        console.warn(`[clawweb] Insight approved auto-repair continuation skipped improvement=${improvement.improvementId}: ${error instanceof Error ? error.message : String(error)}`);
      } finally {
        if (activeGrantMatched) {
          improvement = await insightService.getAdminImprovement(improvement.improvementId);
          autoRepairStarted = improvement.status === "IN_PROGRESS";
        }
      }
    }
    if (decision === "APPROVE" && !autoRepairStarted) {
      insightService.notifyAdminApprovedImprovement(improvement);
    }
    res.json(improvement);
  }));

  router.post("/admin/improvements/:improvementId/execute-once", asyncHandler(async (req, res) => {
    const actorUserId = requireAdmin(req, "只有管理员可以代用户执行改进项");
    const taskService = options.insightTaskService;
    const repairService = options.repairService;
    if (!taskService && !repairService) throw new InsightDataNotReadyError("Insight Repair/Evolve Source 服务不可用");

    const body = bodyRecord(req);
    const allowedFields = new Set([
      "targetUserId",
      "targetBotId",
      "botEnv",
      "repairDirection",
      "reason",
      "crossBotConfirmed",
      "maxRounds",
    ]);
    const unknownFields = Object.keys(body).filter((field) => !allowedFields.has(field));
    if (unknownFields.length) {
      throw new InsightValidationError(`管理员代处理包含未知字段: ${unknownFields.join(", ")}`);
    }

    const insightService = requireService(service);
    const improvement = await insightService.getAdminImprovement(
      parseImprovementId(req.params.improvementId),
    );
    const rerunnable = improvement.status.toUpperCase() === "IN_PROGRESS"
      && (["failed", "canceled", "cancelled"].includes(improvement.latestEvolveTaskStatus?.toLowerCase() ?? "")
        || ["STILL_PRESENT", "INSUFFICIENT_DATA"].includes(improvement.verificationStatus));
    if (improvement.status.toUpperCase() !== "ACTIVE" && !rerunnable) {
      throw new InsightConflictError("当前改进项没有可重新发起的修复运行");
    }
    if (improvement.adminReviewStatus === "PENDING") {
      throw new InsightConflictError("候选改进项必须先批准后，才能由管理员代用户执行");
    }
    if (improvement.adminReviewStatus === "REJECTED") {
      throw new InsightConflictError("已驳回的改进项不能执行");
    }

    const targetUserId = normalizeString(body.targetUserId, "targetUserId", { max: 128 })
      ?? improvement.ownerUserId;
    if (targetUserId !== improvement.ownerUserId) {
      throw new InsightValidationError("第一版管理员代处理只能针对改进项所属用户");
    }
    const targetBotId = normalizeString(body.targetBotId, "targetBotId", { max: 128 })
      ?? improvement.botId;
    const botEnv = normalizeString(body.botEnv, "botEnv", { max: 32 }) ?? undefined;
    const reason = normalizeString(body.reason, "reason", { required: true, max: 1000 }) ?? "";
    const requestedDirection = normalizeString(body.repairDirection, "repairDirection", { max: 5000 });
    const repairDirection = requestedDirection
      ?? improvement.suggestedAction
      ?? improvement.userGuidance;
    if (improvement.actionType === "ASSIGN_OWNER" && !repairDirection) {
      throw new InsightValidationError("手动改进项必须填写修复方向，才能进入进化室");
    }
    const crossBotConfirmed = body.crossBotConfirmed === true;
    if (targetBotId !== improvement.botId && !crossBotConfirmed) {
      throw new InsightValidationError("目标 Bot 与问题证据来源不同，必须明确确认跨 Bot 执行");
    }
    const maxRounds = normalizeInteger(body.maxRounds, "maxRounds", { min: 1 }) ?? 3;
    if (maxRounds > 100) throw new InsightValidationError("maxRounds 必须是 1 到 100 的整数");
    const idempotencyKey = req.header("Idempotency-Key")?.trim();
    if (!idempotencyKey || idempotencyKey.length > 128) {
      throw new InsightValidationError("Idempotency-Key 必填且不能超过 128 个字符");
    }

    if (repairService) {
      try {
        const result = await repairService.createTask({
          actorUserId,
          isAdmin: true,
          authHeaders: repairAuthHeaders(req, actorUserId),
          body: {
            taskName: `${improvement.title.slice(0, 119)} · 管理员代处理`,
            symptom: improvement.title,
            repairDirection,
            targetUserId,
            botId: targetBotId,
            targetEnvironment: botEnv,
            insightImprovementId: improvement.improvementId,
            insightRequestId: idempotencyKey,
            adminOverrideReason: reason,
            crossBotConfirmed,
          },
        });
        res.status(202).json({
          taskId: result.taskId,
          taskName: result.taskName,
          status: result.status,
          improvementId: improvement.improvementId,
          executionMode: "ADMIN_ONCE",
          operatorUserId: actorUserId,
          targetUserId,
          targetBotId,
          persistentAuthorization: false,
          insightSource: result.insightSource ?? null,
        });
      } catch (error) {
        if (error instanceof RepairError) {
          res.status(error.status).json({
            error: error.code,
            message: error.message,
            ...(error.toolCallId ? { toolCallId: error.toolCallId } : {}),
            ...(error.recovery ? { recovery: error.recovery } : {}),
          });
          return;
        }
        throw error;
      }
      return;
    }

    if (!taskService) throw new InsightDataNotReadyError("Insight Evolve Source 服务不可用");
    try {
      const result = await taskService.create({
        taskType: "full",
        taskName: `${improvement.title.slice(0, 119)} · 管理员代处理`,
        remark: repairDirection,
        userId: targetUserId,
        botId: targetBotId,
        botEnv,
        improvementId: improvement.improvementId,
        crossBotConfirmed,
        maxRounds,
        nodeCommandYamls: undefined,
        forceMessage: false,
        runtimeMaintenance: true,
        idempotencyKey,
        actorUserId,
        createdByOverride: "insight-admin-override",
        adminOverrideOnce: {
          operatorUserId: actorUserId,
          reason,
          repairDirection,
        },
        callbackUrl: (taskId, stepId) => `${req.protocol}://${req.get("host")}/api/evolve/internal/tasks/${encodeURIComponent(taskId)}/steps/${encodeURIComponent(stepId)}/bot-callback`,
      });
      res.status(result.created ? 201 : 200).json({
        taskId: result.task.task_id,
        taskName: result.task.task_name,
        status: result.task.status,
        improvementId: improvement.improvementId,
        executionMode: "ADMIN_ONCE",
        operatorUserId: actorUserId,
        targetUserId,
        targetBotId,
        persistentAuthorization: false,
        source: result.source,
        steps: result.steps,
        idempotent: result.idempotent,
      });
    } catch (error) {
      if (isInsightTaskCreationError(error)) {
        const status = error.category === "validation" ? 400
          : error.category === "auth" ? 401
            : error.category === "forbidden" ? 403
              : error.category === "not_found" ? 404
                : error.category === "conflict" ? 409 : 422;
        res.status(status).json({
          code: error.code,
          error: error.message,
          stage: error.stage,
          retryable: error.retryable,
        });
        return;
      }
      throw error;
    }
  }));

  router.post("/admin/improvements/:improvementId/force-resolved", asyncHandler(async (req, res) => {
    const actorUserId = requireAdmin(req, "只有管理员可以强制推进改进项验收");
    const body = bodyRecord(req);
    res.json({ improvement: await requireService(service).recordForcedVerificationResult({
      ...body,
      improvementId: parseImprovementId(req.params.improvementId),
      operatedBy: actorUserId,
      resolvedSource: body.resolvedSource ?? "FORCE_VERIFIED",
    }) });
  }));

  router.post("/admin/improvements/:improvementId/handled", asyncHandler(async (req, res) => {
    const actorUserId = requireAdmin(req, "只有管理员可以推进改进项验收");
    res.json(await requireService(service).markAdminImprovementHandled(
      actorUserId,
      parseImprovementId(req.params.improvementId),
      bodyRecord(req),
    ));
  }));

  router.post("/admin/improvements/:improvementId/reject", asyncHandler(async (req, res) => {
    const actorUserId = requireAdmin(req, "只有管理员可以驳回改进项");
    res.json(await requireService(service).rejectAdminImprovement(
      actorUserId,
      parseImprovementId(req.params.improvementId),
      bodyRecord(req),
    ));
  }));

  router.post("/admin/improvements/:improvementId/reopen", asyncHandler(async (req, res) => {
    const actorUserId = requireAdmin(req, "只有管理员可以恢复改进项");
    res.json(await requireService(service).reopenAdminImprovement(
      actorUserId,
      parseImprovementId(req.params.improvementId),
      bodyRecord(req),
    ));
  }));

  router.get("/admin/improvements/:improvementId/consent-link", asyncHandler(async (req, res) => {
    requireAdmin(req, "只有管理员可以生成 Owner 授权链接");
    const insightService = requireService(service);
    const improvement = await insightService.getAdminImprovement(parseImprovementId(req.params.improvementId));
    if (improvement.status.toUpperCase() !== "ACTIVE" || improvement.actionType !== "DIRECT_EVOLUTION" || !improvement.sourceRuleId) {
      throw new InsightConflictError("只有 ACTIVE 的自动优化项可以生成持续授权链接");
    }
    if (!options.ruleProvider) throw new InsightDataNotReadyError("治理规则读取器不可用");
    const rule = await readAutoRepairRule(options.ruleProvider, improvement.sourceRuleId, "DIRECT_EVOLUTION");
    if (!rule) throw new InsightConflictError("当前治理规则不存在或已停用，无法生成授权链接");
    const expiresAt = Math.floor(Date.now() / 1000) + 7 * 24 * 60 * 60;
    const token = createAdminConsentToken({
      improvementId: improvement.improvementId,
      ownerUserId: improvement.ownerUserId,
      botId: improvement.botId,
      sourceRuleId: rule.sourceRuleId,
      ruleVersion: rule.ruleVersion,
      exp: expiresAt,
    });
    const url = `${getClawWebPublicBaseUrl().replace(/\/$/, "")}/evolve/new?type=full&source=improvement&improvementId=${encodeURIComponent(improvement.improvementId)}&adminConsent=${encodeURIComponent(token)}`;
    res.json({
      improvementId: improvement.improvementId,
      ownerUserId: improvement.ownerUserId,
      botId: improvement.botId,
      expiresAt: new Date(expiresAt * 1000).toISOString(),
      url,
    });
  }));

  router.get("/auto-repair-grants", asyncHandler(async (req, res) => {
    const ownerUserId = resolveUserId(req);
    res.json({ items: await requireAutoRepairRepo(options).list(ownerUserId) });
  }));

  router.delete("/auto-repair-grants/:grantId", asyncHandler(async (req, res) => {
    const ownerUserId = resolveUserId(req);
    const grantId = parseImprovementId(req.params.grantId);
    const body = bodyRecord(req);
    const version = Number(body.version);
    if (!Number.isInteger(version) || version < 1) {
      throw new InsightValidationError("version 必须是正整数");
    }
    const updated = await requireAutoRepairRepo(options).revoke({
      ownerUserId,
      grantId,
      expectedVersion: version,
      revokedBy: ownerUserId,
    });
    if (updated === null) throw new InsightNotFoundError("自动修复授权不存在");
    if (updated === "VERSION_CONFLICT") throw new InsightConflictError("授权已被更新，请刷新后重试");
    if (updated === "STATE_CONFLICT") throw new InsightConflictError("授权已经撤销");
    res.json(updated);
  }));

  router.get("/improvements", asyncHandler(async (req, res) => {
    const result = await requireService(service).listImprovements(resolveOwnerUserId(req), {
      botId: stringValue(req.query.botId),
      status: stringValue(req.query.status),
      cursor: stringValue(req.query.cursor),
      pageSize: parsePageSize(req.query.pageSize),
    });
    res.json(result);
  }));

  router.get("/improvements/:improvementId", asyncHandler(async (req, res) => {
    res.json(await requireService(service).getImprovement(
      resolveUserId(req),
      parseImprovementId(req.params.improvementId),
    ));
  }));

  router.get("/improvements/:improvementId/handoff", asyncHandler(async (req, res) => {
    res.json(await requireService(service).getImprovementHandoff(
      resolveUserId(req),
      parseImprovementId(req.params.improvementId),
    ));
  }));

  router.post("/improvements/:improvementId/handled", asyncHandler(async (req, res) => {
    res.json(await requireService(service).markImprovementHandled(
      resolveUserId(req),
      parseImprovementId(req.params.improvementId),
      bodyRecord(req),
    ));
  }));

  router.post("/improvements/:improvementId/reject", asyncHandler(async (req, res) => {
    res.json(await requireService(service).rejectImprovement(
      resolveUserId(req),
      parseImprovementId(req.params.improvementId),
      bodyRecord(req),
    ));
  }));

  router.post("/improvements/:improvementId/self-repair-handoff", asyncHandler(async (req, res) => {
    res.json(await requireService(service).recordSelfRepairHandoff(
      resolveUserId(req),
      parseImprovementId(req.params.improvementId),
      bodyRecord(req),
    ));
  }));

  router.patch("/improvements/:improvementId", asyncHandler(async (req, res) => {
    res.json(await requireService(service).updateImprovement(
      resolveUserId(req),
      parseImprovementId(req.params.improvementId),
      bodyRecord(req),
    ));
  }));

  router.use(errorResponse);
  return router;
}
