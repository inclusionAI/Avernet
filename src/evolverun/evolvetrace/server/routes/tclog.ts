import { Router, type Request, type Response } from "express";
import type { IDatabase } from "../db.js";
import type { FlowRunRepository, FlowRunRow } from "../repositories/flow-run-repository.js";
import type { BotWorkflowPermissionRepository } from "../repositories/bot-workflow-permission-repository.js";
import { asyncHandler } from "../middleware/async-handler.js";
import { col, safeParseJson } from "./langfuse.js";

type QueryableDb = { query<T = Record<string, unknown>>(sql: string, params?: unknown[]): Promise<T[]> };

type TraceRow = Record<string, unknown>;
type ObservationRow = Record<string, unknown>;
type BizRef = { ref_type: string; ref_value: string };

type TCLogTrace = {
  traceId: string;
  sessionId: string | null;
  sessionKey: string | null;
  botId: string | null;
  ownerId: string | null;
  engine: string | null;
  status: string | null;
  name: string | null;
  startTimeMs: number | null;
  endTimeMs: number | null;
  latencyMs: number | null;
  inputPreview: string | null;
  outputPreview: string | null;
  inputTokens: number | null;
  outputTokens: number | null;
  cacheReadTokens: number | null;
  cacheWriteTokens: number | null;
  totalTokens: number | null;
  totalCost: number | null;
  matchTypes: string[];
  matchSource?: "direct" | "biz_ref" | "both";
  observations?: TCLogObservation[];
};

type TCLogObservation = {
  observationId: string;
  traceId: string;
  parentObservationId: string | null;
  type: string | null;
  name: string | null;
  model: string | null;
  status: string | null;
  startTimeMs: number | null;
  endTimeMs: number | null;
  latencyMs: number | null;
  input: unknown;
  output: unknown;
  promptTokens: number | null;
  completionTokens: number | null;
  totalTokens: number | null;
  metadata?: unknown;
};

type TCLogTaskSummary = {
  bizScene: string;
  taskId: string;
  botId: string | null;
  ownerId: string | null;
  source: string;
  refCount: number;
  traceCount: number;
  workflowRunCount: number;
  lastEventTimeMs: number | null;
};

type TCLogSessionSummary = ReturnType<typeof groupSessions>[number];

type NodeExecutionRow = Record<string, unknown>;

const DAY_MS = 24 * 60 * 60 * 1000;
const MAX_TCLOG_QUERY_RANGE_MS = 90 * DAY_MS;
const MAX_EMPTY_TASK_SEARCH_LIMIT = 100;

function getHeaderUserId(req: Request): string | undefined {
  return (req.headers["x-user-id"] as string | undefined)?.trim()
    || (req.cookies?.staff_id as string | undefined)?.trim()
    || undefined;
}

type OwnerResult = { ok: "success"; ownerId: string } | { ok: "error"; status: number; message: string };

function resolveOwner(req: Request): OwnerResult {
  const loginUserId = getHeaderUserId(req);
  const requestedOwnerId = (req.query.ownerId as string | undefined)?.trim();
  if (!loginUserId && !requestedOwnerId) {
    return { ok: "error", status: 400, message: "ownerId is required" };
  }
  if (!req.isLogAdmin && requestedOwnerId && loginUserId && requestedOwnerId !== loginUserId) {
    return { ok: "error", status: 403, message: "Cannot query another owner's logs" };
  }
  return { ok: "success", ownerId: req.isLogAdmin ? (requestedOwnerId || loginUserId || "") : (loginUserId || requestedOwnerId || "") };
}

function asNumberMs(value: unknown): number | null {
  if (value == null) return null;
  if (value instanceof Date) return value.getTime();
  if (typeof value === "number") return value < 1e12 ? value * 1000 : value;
  if (typeof value === "string") {
    const n = Number(value);
    if (Number.isFinite(n)) return n < 1e12 ? n * 1000 : n;
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function asFiniteNumber(value: unknown): number | null {
  if (value == null) return null;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string") {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function parseTimeMs(value: unknown): number | undefined {
  if (value == null || value === "") return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function validateTimeRange(from: number | undefined, to: number | undefined) {
  if (from == null || to == null) return "from and to are required";
  if (to < from) return "to must be greater than or equal to from";
  if (to - from > MAX_TCLOG_QUERY_RANGE_MS) return "time range must be within 90 days";
  return null;
}

function parseTraceDataSource(value: unknown): "auto" | "tc" | "langfuse" {
  return value === "tc" || value === "langfuse" ? value : "auto";
}

function preview(value: unknown, limit = 240): string | null {
  if (value == null) return null;
  const parsed = safeParseJson(value);
  const text = typeof parsed === "string" ? parsed : JSON.stringify(parsed);
  if (!text) return null;
  return text.length > limit ? `${text.slice(0, limit)}...` : text;
}

function normalizeBotId(botId: string | null | undefined): string | null {
  if (!botId) return null;
  const idx = botId.indexOf(":");
  return idx > 0 ? botId.slice(0, idx) : botId;
}

function getMatchSource(matchTypes: string[]): TCLogTrace["matchSource"] {
  const hasDirect = matchTypes.some((type) => type !== "biz_ref");
  const hasBizRef = matchTypes.includes("biz_ref");
  if (hasDirect && hasBizRef) return "both";
  if (hasBizRef) return "biz_ref";
  if (hasDirect) return "direct";
  return undefined;
}

function mapOtelTrace(row: TraceRow, matchTypes: string[]): TCLogTrace {
  const start = asNumberMs(col(row, "start_time_ms") ?? col(row, "start_time") ?? col(row, "gmt_create"));
  const end = asNumberMs(col(row, "end_time_ms") ?? col(row, "end_time"));
  const inputTokens = asFiniteNumber(col(row, "usage_input_tokens") ?? col(row, "input_tokens") ?? col(row, "prompt_tokens"));
  const outputTokens = asFiniteNumber(col(row, "usage_output_tokens") ?? col(row, "output_tokens") ?? col(row, "completion_tokens"));
  const cacheReadTokens = asFiniteNumber(col(row, "usage_cache_read_tokens") ?? col(row, "cache_read_tokens") ?? col(row, "cached_prompt_tokens"));
  const cacheWriteTokens = asFiniteNumber(col(row, "usage_cache_write_tokens") ?? col(row, "cache_write_tokens"));
  return {
    traceId: col<string>(row, "trace_id") ?? "",
    sessionId: col<string | null>(row, "session_id") ?? null,
    sessionKey: col<string | null>(row, "session_key") ?? null,
    botId: normalizeBotId(col<string | null>(row, "bot_id") ?? null),
    ownerId: col<string | null>(row, "user_id") ?? null,
    engine: col<string | null>(row, "engine") ?? null,
    status: col<string | null>(row, "status") ?? col<string | null>(row, "level") ?? null,
    name: col<string | null>(row, "name") ?? null,
    startTimeMs: start,
    endTimeMs: end,
    latencyMs: asFiniteNumber(col(row, "latency_ms")),
    inputPreview: preview(col(row, "input")),
    outputPreview: preview(col(row, "output")),
    inputTokens,
    outputTokens,
    cacheReadTokens,
    cacheWriteTokens,
    totalTokens: asFiniteNumber(col(row, "usage_total_tokens")),
    totalCost: asFiniteNumber(col(row, "total_cost")),
    matchTypes,
    matchSource: getMatchSource(matchTypes),
  };
}

function mapLegacyTrace(row: TraceRow, matchTypes: string[]): TCLogTrace {
  const start = asNumberMs(col(row, "gmt_trace") ?? col(row, "start_time"));
  const latencySeconds = col<number | null>(row, "latency") ?? null;
  const inputTokens = asFiniteNumber(col(row, "usage_input_tokens") ?? col(row, "input_tokens") ?? col(row, "prompt_tokens"));
  const outputTokens = asFiniteNumber(col(row, "usage_output_tokens") ?? col(row, "output_tokens") ?? col(row, "completion_tokens"));
  const cacheReadTokens = asFiniteNumber(col(row, "usage_cache_read_tokens") ?? col(row, "cache_read_tokens") ?? col(row, "cached_prompt_tokens"));
  const cacheWriteTokens = asFiniteNumber(col(row, "usage_cache_write_tokens") ?? col(row, "cache_write_tokens"));
  return {
    traceId: col<string>(row, "trace_id") ?? String(col<number>(row, "id") ?? ""),
    sessionId: col<string | null>(row, "real_session_id") ?? col<string | null>(row, "session_id") ?? null,
    sessionKey: col<string | null>(row, "session_id") ?? null,
    botId: normalizeBotId(col<string | null>(row, "bot_id") ?? null),
    ownerId: col<string | null>(row, "user_id") ?? null,
    engine: "langfuse_legacy",
    status: col<string | null>(row, "status") ?? null,
    name: col<string | null>(row, "name") ?? null,
    startTimeMs: start,
    endTimeMs: start != null && latencySeconds != null ? start + latencySeconds * 1000 : null,
    latencyMs: latencySeconds != null ? Number(latencySeconds) * 1000 : null,
    inputPreview: preview(col(row, "input")),
    outputPreview: preview(col(row, "output")),
    inputTokens,
    outputTokens,
    cacheReadTokens,
    cacheWriteTokens,
    totalTokens: asFiniteNumber(col(row, "usage_total_tokens")),
    totalCost: asFiniteNumber(col(row, "total_cost")),
    matchTypes,
    matchSource: getMatchSource(matchTypes),
  };
}

function mapObservation(row: ObservationRow): TCLogObservation {
  return {
    observationId: col<string>(row, "observation_id") ?? col<string>(row, "id") ?? "",
    traceId: col<string>(row, "trace_id") ?? "",
    parentObservationId: col<string | null>(row, "parent_observation_id") ?? null,
    type: col<string | null>(row, "type") ?? null,
    name: col<string | null>(row, "name") ?? null,
    model: col<string | null>(row, "model") ?? null,
    status: col<string | null>(row, "status") ?? col<string | null>(row, "status_message") ?? null,
    startTimeMs: asNumberMs(col(row, "start_time_ms") ?? col(row, "start_time")),
    endTimeMs: asNumberMs(col(row, "end_time_ms") ?? col(row, "end_time")),
    latencyMs: asFiniteNumber(col(row, "latency_ms")) ?? (col(row, "latency") != null ? Number(col(row, "latency")) * 1000 : null),
    input: safeParseJson(col(row, "input") ?? null),
    output: safeParseJson(col(row, "output") ?? null),
    promptTokens: asFiniteNumber(col(row, "usage_input_tokens")),
    completionTokens: asFiniteNumber(col(row, "usage_output_tokens")),
    totalTokens: asFiniteNumber(col(row, "usage_total_tokens")),
    metadata: safeParseJson(col(row, "metadata") ?? null),
  };
}

export function buildTraceWhere(params: {
  ownerId: string;
  botId?: string;
  traceId?: string;
  sessionId?: string;
  sessionKey?: string;
  skipOwnerFilter?: boolean;
  keyword?: string;
  bizScene?: string;
  taskId?: string;
  from?: number;
  to?: number;
}, mode: "otel" | "legacy"): { sql: string; values: unknown[]; matchTypes: string[] } {
  const conds: string[] = [];
  const values: unknown[] = [];
  const matchTypes: string[] = [];

  if (params.traceId) {
    conds.push("trace_id = ?");
    values.push(params.traceId);
    matchTypes.push("trace_id");
  }
  if (params.sessionId) {
    if (mode === "legacy") {
      conds.push("real_session_id = ?");
      values.push(params.sessionId);
    } else {
      conds.push("session_id = ?");
      values.push(params.sessionId);
    }
    matchTypes.push("session_id");
  }
  if (params.sessionKey) {
    if (mode === "legacy") {
      conds.push("session_id = ?");
    } else {
      conds.push("session_key = ?");
    }
    values.push(params.sessionKey);
    matchTypes.push("session_key");
  }
  if (mode === "otel" && params.bizScene) {
    if (params.taskId) {
      conds.push("biz_scene = ? AND biz_task_id = ?");
      values.push(params.bizScene, params.taskId);
      matchTypes.push("biz_task_id");
    } else {
      conds.push("biz_scene = ?");
      values.push(params.bizScene);
      matchTypes.push("biz_scene");
    }
  }
  if (!params.botId) {
    if (params.ownerId && !params.skipOwnerFilter) {
      conds.push("user_id = ?");
      values.push(params.ownerId);
    }
  } else if (params.botId === "default") {
    conds.push("user_id = ?");
    values.push(params.ownerId);
    conds.push(mode === "otel" ? "bot_id IN (?, ?, ?)" : "bot_id = ?");
    if (mode === "otel") {
      values.push("default", `${params.ownerId}_default`, `default:${params.ownerId}`);
    } else {
      values.push("default");
    }
  } else {
    conds.push(mode === "otel" ? "bot_id IN (?, ?)" : "bot_id = ?");
    if (mode === "otel") {
      values.push(params.botId, `${params.botId}:${params.ownerId}`);
    } else {
      values.push(params.botId);
    }
  }
  if (params.keyword) {
    conds.push("(name LIKE ? OR input LIKE ? OR output LIKE ?)");
    values.push(`%${params.keyword}%`, `%${params.keyword}%`, `%${params.keyword}%`);
    matchTypes.push("keyword");
  }
  if (!params.traceId && !params.sessionId && !params.sessionKey) {
    if (params.from) {
      conds.push(mode === "otel" ? "start_time_ms >= ?" : "gmt_trace >= ?");
      values.push(params.from);
    }
    if (params.to) {
      conds.push(mode === "otel" ? "start_time_ms <= ?" : "gmt_trace <= ?");
      values.push(params.to);
    }
  }
  return { sql: conds.length > 0 ? `WHERE ${conds.join(" AND ")}` : "", values, matchTypes };
}

async function queryOtelTraces(db: QueryableDb, params: Parameters<typeof buildTraceWhere>[0], limit: number, offset: number): Promise<TCLogTrace[]> {
  const where = buildTraceWhere(params, "otel");
  try {
    const rows = await db.query<TraceRow>(
      `SELECT * FROM ac_otel_log_trace ${where.sql} ORDER BY start_time_ms DESC LIMIT ? OFFSET ?`,
      [...where.values, limit, offset],
    );
    return rows.map((row) => mapOtelTrace(row, where.matchTypes));
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    console.warn(`[tclog] tc_direct_query_failed: ${msg}`);
    throw new Error(`tc_direct_query_failed: ${msg}`);
  }
}

async function hasOtelTrace(db: QueryableDb, params: Parameters<typeof buildTraceWhere>[0]): Promise<boolean> {
  const traces = await queryOtelTraces(db, params, 1, 0);
  return traces.length > 0;
}

async function queryLegacyTraces(db: QueryableDb, params: Parameters<typeof buildTraceWhere>[0], limit: number, offset: number): Promise<TCLogTrace[]> {
  const where = buildTraceWhere(params, "legacy");
  try {
    const rows = await db.query<TraceRow>(
      `SELECT * FROM aw_langfuse_traces ${where.sql} ORDER BY gmt_trace DESC LIMIT ? OFFSET ?`,
      [...where.values, limit, offset],
    );
    return rows.map((row) => mapLegacyTrace(row, where.matchTypes));
  } catch (error) {
    console.warn(`[tclog] query aw_langfuse_traces failed: ${error instanceof Error ? error.message : String(error)}`);
    throw error;
  }
}

export async function queryTracesWithFallback(db: QueryableDb, params: Parameters<typeof buildTraceWhere>[0], limit: number, offset: number, dataSource: "auto" | "tc" | "langfuse" = "auto") {
  if (dataSource === "tc") {
    const traces = await queryOtelTraces(db, params, limit, offset);
    return { traces, dataSource: "ocb_otel", fallbackUsed: false };
  }
  if (dataSource === "langfuse") {
    const traces = await queryLegacyTraces(db, params, limit, offset);
    return { traces, dataSource: "langfuse_legacy", fallbackUsed: false };
  }
  const otel = await queryOtelTraces(db, params, limit, offset);
  if (otel.length > 0) return { traces: otel, dataSource: "ocb_otel", fallbackUsed: false };
  if (offset > 0 && await hasOtelTrace(db, params)) {
    return { traces: [], dataSource: "ocb_otel", fallbackUsed: false };
  }
  const legacy = await queryLegacyTraces(db, params, limit, offset);
  return { traces: legacy, dataSource: "langfuse_legacy", fallbackUsed: legacy.length > 0 };
}

function hasLegacyExactCondition(params: Parameters<typeof buildTraceWhere>[0]) {
  if (params.traceId || params.sessionId || params.sessionKey) return true;
  return !params.taskId && !params.bizScene && !params.keyword && !!params.botId && params.from != null && params.to != null;
}

function hasOtelTaskDirectCondition(params: Parameters<typeof buildTraceWhere>[0]) {
  if (params.traceId || params.sessionId || params.sessionKey) return true;
  if (params.bizScene) return true;
  return !params.taskId && !!params.botId && params.from != null && params.to != null;
}

function countRuntimeRefs(refs: BizRef[]) {
  const runtimeRefs = new Set<string>();
  for (const ref of refs) {
    if (!ref.ref_value) continue;
    if (ref.ref_type !== "trace_id" && ref.ref_type !== "session_id" && ref.ref_type !== "session_key") continue;
    runtimeRefs.add(`${ref.ref_type}:${ref.ref_value}`);
  }
  return runtimeRefs.size;
}

function uniqueNonEmpty(values: Array<string | null | undefined>) {
  return Array.from(new Set(values.filter((value): value is string => !!value)));
}

export function buildWorkflowLookupParams(params: {
  bizScene?: string;
  taskId?: string;
  traces: Array<Pick<TCLogTrace, "sessionId" | "sessionKey">>;
  refs: BizRef[];
}) {
  const sessionIds = uniqueNonEmpty([
    ...params.traces.map((trace) => trace.sessionId),
    ...params.refs.filter((ref) => ref.ref_type === "session_id").map((ref) => ref.ref_value),
  ]);
  const sessionKeys = uniqueNonEmpty([
    ...params.traces.map((trace) => trace.sessionKey),
    ...params.refs.filter((ref) => ref.ref_type === "session_key").map((ref) => ref.ref_value),
  ]);
  return {
    taskId: params.bizScene === "clawmind_workflow" ? params.taskId : undefined,
    sessionIds,
    sessionKeys,
  };
}

export async function queryTaskTraces(db: QueryableDb, params: Parameters<typeof buildTraceWhere>[0], refs: BizRef[], limit: number, dataSource: "auto" | "tc" | "langfuse" = "auto") {
  if (dataSource === "langfuse") {
    const directLegacy = hasLegacyExactCondition(params) ? await queryLegacyTraces(db, params, limit, 0) : [];
    const refLegacy = await queryLegacyTracesByRefs(db, {
      ownerId: params.ownerId,
      botId: params.botId,
      refs,
      limit,
    });
    const traces = mergeTraces(directLegacy, refLegacy);
    return { traces, dataSource: "langfuse_legacy", fallbackUsed: false };
  }

  const directOtel = hasOtelTaskDirectCondition(params) ? await queryOtelTraces(db, params, limit, 0) : [];
  const refTraces = await queryOtelTracesByRefs(db, {
    ownerId: params.ownerId,
    botId: params.botId,
    refs,
    limit,
  });
  const otelTraces = mergeTraces(directOtel, refTraces);
  if (dataSource === "tc") {
    return { traces: otelTraces, dataSource: "ocb_otel", fallbackUsed: false };
  }
  if (otelTraces.length > 0) {
    const expectedRefCount = params.taskId ? countRuntimeRefs(refs) : 0;
    if (expectedRefCount > 0 && otelTraces.length < expectedRefCount) {
      const refLegacy = await queryLegacyTracesByRefs(db, {
        ownerId: params.ownerId,
        botId: params.botId,
        refs,
        limit,
      });
      const traces = mergeTraces(otelTraces, refLegacy);
      return {
        traces,
        dataSource: refLegacy.length > 0 ? "ocb_otel,langfuse_legacy" : "ocb_otel",
        fallbackUsed: refLegacy.length > 0,
      };
    }
    return { traces: otelTraces, dataSource: "ocb_otel", fallbackUsed: false };
  }
  if (!hasLegacyExactCondition(params)) {
    const legacyByRefs = await queryLegacyTracesByRefs(db, {
      ownerId: params.ownerId,
      botId: params.botId,
      refs,
      limit,
    });
    return legacyByRefs.length > 0
      ? { traces: legacyByRefs, dataSource: "langfuse_legacy", fallbackUsed: true }
      : { traces: [], dataSource: "ocb_otel", fallbackUsed: false };
  }

  const directLegacy = await queryLegacyTraces(db, params, limit, 0);
  const refLegacy = await queryLegacyTracesByRefs(db, {
    ownerId: params.ownerId,
    botId: params.botId,
    refs,
    limit,
  });
  const legacy = mergeTraces(directLegacy, refLegacy);
  return { traces: legacy, dataSource: "langfuse_legacy", fallbackUsed: legacy.length > 0 };
}

async function queryBizRefs(db: QueryableDb, bizScene: string | undefined, taskId: string) {
  if (!bizScene) return [];
  try {
    return await db.query<BizRef>(
      "SELECT ref_type, ref_value FROM ac_otel_log_biz_ref WHERE biz_scene = ? AND biz_task_id = ?",
      [bizScene, taskId],
    );
  } catch (error) {
    console.warn(`[tclog] query ac_otel_log_biz_ref failed: ${error instanceof Error ? error.message : String(error)}`);
    throw error;
  }
}

async function queryOtelTracesByRefs(db: QueryableDb, params: {
  ownerId: string;
  botId?: string;
  refs: BizRef[];
  limit: number;
}): Promise<TCLogTrace[]> {
  const traceIds = params.refs.filter((r) => r.ref_type === "trace_id").map((r) => r.ref_value);
  const sessionIds = params.refs.filter((r) => r.ref_type === "session_id").map((r) => r.ref_value);
  const sessionKeys = params.refs.filter((r) => r.ref_type === "session_key").map((r) => r.ref_value);
  const conds: string[] = [];
  const values: unknown[] = [];
  if (traceIds.length) {
    conds.push(`trace_id IN (${traceIds.map(() => "?").join(",")})`);
    values.push(...traceIds);
  }
  if (sessionIds.length) {
    conds.push(`session_id IN (${sessionIds.map(() => "?").join(",")})`);
    values.push(...sessionIds);
  }
  if (sessionKeys.length) {
    conds.push(`session_key IN (${sessionKeys.map(() => "?").join(",")})`);
    values.push(...sessionKeys);
  }
  if (conds.length === 0) return [];
  if (params.ownerId) {
    values.push(params.ownerId);
  }
  if (params.botId) {
    values.push(params.botId, `${params.botId}:${params.ownerId}`);
  }
  try {
    const rows = await db.query<TraceRow>(
      `SELECT * FROM ac_otel_log_trace WHERE (${conds.join(" OR ")})${params.ownerId ? " AND user_id = ?" : ""}${params.botId ? " AND bot_id IN (?, ?)" : ""} ORDER BY start_time_ms DESC LIMIT ?`,
      [...values, params.limit],
    );
    return rows.map((row) => mapOtelTrace(row, ["biz_ref"]));
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    console.warn(`[tclog] tc_biz_ref_query_failed: ${msg}`);
    throw new Error(`tc_biz_ref_query_failed: ${msg}`);
  }
}

async function queryLegacyTracesByRefs(db: QueryableDb, params: {
  ownerId: string;
  botId?: string;
  refs: BizRef[];
  limit: number;
}): Promise<TCLogTrace[]> {
  const traceIds = params.refs.filter((r) => r.ref_type === "trace_id").map((r) => r.ref_value);
  const sessionIds = params.refs.filter((r) => r.ref_type === "session_id").map((r) => r.ref_value);
  const sessionKeys = params.refs.filter((r) => r.ref_type === "session_key").map((r) => r.ref_value);
  const conds: string[] = [];
  const values: unknown[] = [];
  if (traceIds.length) {
    conds.push(`trace_id IN (${traceIds.map(() => "?").join(",")})`);
    values.push(...traceIds);
  }
  if (sessionIds.length) {
    conds.push(`real_session_id IN (${sessionIds.map(() => "?").join(",")})`);
    values.push(...sessionIds);
  }
  if (sessionKeys.length) {
    conds.push(`session_id IN (${sessionKeys.map(() => "?").join(",")})`);
    values.push(...sessionKeys);
  }
  if (conds.length === 0) return [];

  if (params.botId) {
    values.push(params.botId);
  } else if (params.ownerId) {
    values.push(params.ownerId);
  }
  try {
    const rows = await db.query<TraceRow>(
      `SELECT * FROM aw_langfuse_traces WHERE (${conds.join(" OR ")})${params.botId ? " AND bot_id = ?" : params.ownerId ? " AND user_id = ?" : ""} ORDER BY gmt_trace DESC LIMIT ?`,
      [...values, params.limit],
    );
    return rows.map((row) => mapLegacyTrace(row, ["biz_ref"]));
  } catch (error) {
    console.warn(`[tclog] query aw_langfuse_traces by biz refs failed: ${error instanceof Error ? error.message : String(error)}`);
    throw error;
  }
}

function mergeTraces(primary: TCLogTrace[], extra: TCLogTrace[]) {
  const map = new Map<string, TCLogTrace>();
  for (const trace of primary) map.set(trace.traceId, trace);
  for (const trace of extra) {
    const existing = map.get(trace.traceId);
    if (existing) {
      existing.matchTypes = Array.from(new Set([...existing.matchTypes, ...trace.matchTypes]));
      existing.matchSource = getMatchSource(existing.matchTypes);
    } else {
      map.set(trace.traceId, trace);
    }
  }
  return Array.from(map.values()).sort((a, b) => (b.startTimeMs ?? 0) - (a.startTimeMs ?? 0));
}

function groupSessions(traces: TCLogTrace[]) {
  const map = new Map<string, {
    sessionKey: string | null;
    sessionId: string | null;
    ownerId: string | null;
    botId: string | null;
    engine: string | null;
    traceCount: number;
    startTimeMs: number | null;
    endTimeMs: number | null;
    totalTokens: number | null;
    totalCost: number | null;
    latestStatus: string | null;
    traces: TCLogTrace[];
  }>();

  for (const trace of traces) {
    const key = trace.sessionKey || trace.sessionId || trace.traceId;
    const existing = map.get(key);
    if (!existing) {
      map.set(key, {
        sessionKey: trace.sessionKey,
        sessionId: trace.sessionId,
        ownerId: trace.ownerId,
        botId: trace.botId,
        engine: trace.engine,
        traceCount: 1,
        startTimeMs: trace.startTimeMs,
        endTimeMs: trace.endTimeMs,
        totalTokens: trace.totalTokens,
        totalCost: trace.totalCost,
        latestStatus: trace.status,
        traces: [trace],
      });
    } else {
      existing.traceCount += 1;
      existing.traces.push(trace);
      existing.startTimeMs = [existing.startTimeMs, trace.startTimeMs].filter((v): v is number => v != null).sort((a, b) => a - b)[0] ?? null;
      existing.endTimeMs = [existing.endTimeMs, trace.endTimeMs].filter((v): v is number => v != null).sort((a, b) => b - a)[0] ?? null;
      existing.totalTokens = (existing.totalTokens ?? 0) + (trace.totalTokens ?? 0);
      existing.totalCost = (existing.totalCost ?? 0) + (trace.totalCost ?? 0);
      existing.latestStatus = existing.latestStatus ?? trace.status;
    }
  }
  return Array.from(map.values()).sort((a, b) => (b.endTimeMs ?? b.startTimeMs ?? 0) - (a.endTimeMs ?? a.startTimeMs ?? 0));
}

function mapSessionFromTraceRow(row: TraceRow, mode: "otel" | "legacy"): TCLogSessionSummary {
  if (mode === "legacy") {
    const start = asNumberMs(col(row, "gmt_trace") ?? col(row, "start_time"));
    return {
      sessionKey: col<string | null>(row, "session_id") ?? null,
      sessionId: col<string | null>(row, "real_session_id") ?? null,
      ownerId: col<string | null>(row, "user_id") ?? null,
      botId: normalizeBotId(col<string | null>(row, "bot_id") ?? null),
      engine: "langfuse_legacy",
      traceCount: 0,
      startTimeMs: start,
      endTimeMs: start,
      totalTokens: null,
      totalCost: null,
      latestStatus: col<string | null>(row, "status") ?? null,
      traces: [],
    };
  }
  const start = asNumberMs(col(row, "start_time_ms") ?? col(row, "start_time") ?? col(row, "gmt_create"));
  const end = asNumberMs(col(row, "end_time_ms") ?? col(row, "end_time")) ?? start;
  return {
    sessionKey: col<string | null>(row, "session_key") ?? null,
    sessionId: col<string | null>(row, "session_id") ?? null,
    ownerId: col<string | null>(row, "user_id") ?? null,
    botId: normalizeBotId(col<string | null>(row, "bot_id") ?? null),
    engine: col<string | null>(row, "engine") ?? null,
    traceCount: 0,
    startTimeMs: start,
    endTimeMs: end,
    totalTokens: null,
    totalCost: null,
    latestStatus: col<string | null>(row, "status") ?? col<string | null>(row, "level") ?? null,
    traces: [],
  };
}

function dedupeSessionRows(rows: TraceRow[], mode: "otel" | "legacy", limit: number): TCLogSessionSummary[] {
  const sessions: TCLogSessionSummary[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    const session = mapSessionFromTraceRow(row, mode);
    const key = session.sessionKey || session.sessionId || col<string>(row, "trace_id") || "";
    if (!key || seen.has(key)) continue;
    seen.add(key);
    sessions.push(session);
    if (sessions.length >= limit) break;
  }
  return sessions;
}

async function queryOtelSessions(db: QueryableDb, params: Parameters<typeof buildTraceWhere>[0], limit: number, offset: number): Promise<TCLogSessionSummary[]> {
  const where = buildTraceWhere(params, "otel");
  const scanLimit = Math.min(1000, Math.max(limit * 10, 200));
  const scanOffset = offset * 5;
  try {
    const rows = await db.query<TraceRow>(
      `SELECT *
       FROM ac_otel_log_trace ${where.sql}
       ORDER BY start_time_ms DESC
       LIMIT ? OFFSET ?`,
      [...where.values, scanLimit, scanOffset],
    );
    return dedupeSessionRows(rows, "otel", limit);
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    console.warn(`[tclog] tc_direct_query_failed: ${msg}`);
    throw new Error(`tc_direct_query_failed: ${msg}`);
  }
}

async function hasOtelSession(db: QueryableDb, params: Parameters<typeof buildTraceWhere>[0]): Promise<boolean> {
  const sessions = await queryOtelSessions(db, params, 1, 0);
  return sessions.length > 0;
}

async function queryLegacySessions(db: QueryableDb, params: Parameters<typeof buildTraceWhere>[0], limit: number, offset: number): Promise<TCLogSessionSummary[]> {
  const where = buildTraceWhere(params, "legacy");
  const scanLimit = Math.min(1000, Math.max(limit * 10, 200));
  const scanOffset = offset * 5;
  try {
    const rows = await db.query<TraceRow>(
      `SELECT *
       FROM aw_langfuse_traces ${where.sql}
       ORDER BY gmt_trace DESC
       LIMIT ? OFFSET ?`,
      [...where.values, scanLimit, scanOffset],
    );
    return dedupeSessionRows(rows, "legacy", limit);
  } catch (error) {
    console.warn(`[tclog] query aw_langfuse_traces failed: ${error instanceof Error ? error.message : String(error)}`);
    throw error;
  }
}

async function querySessionsWithFallback(db: QueryableDb, params: Parameters<typeof buildTraceWhere>[0], limit: number, offset: number, dataSource: "auto" | "tc" | "langfuse" = "auto") {
  if (dataSource === "tc") {
    const sessions = await queryOtelSessions(db, params, limit, offset);
    return { sessions, dataSource: "ocb_otel", fallbackUsed: false };
  }
  if (dataSource === "langfuse") {
    const sessions = await queryLegacySessions(db, params, limit, offset);
    return { sessions, dataSource: "langfuse_legacy", fallbackUsed: false };
  }
  const otel = await queryOtelSessions(db, params, limit, offset);
  if (otel.length > 0) return { sessions: otel, dataSource: "ocb_otel", fallbackUsed: false };
  if (offset > 0 && await hasOtelSession(db, params)) {
    return { sessions: [], dataSource: "ocb_otel", fallbackUsed: false };
  }
  const legacy = await queryLegacySessions(db, params, limit, offset);
  return { sessions: legacy, dataSource: "langfuse_legacy", fallbackUsed: legacy.length > 0 };
}

export async function queryObservations(db: QueryableDb, traceId: string, source: string): Promise<TCLogObservation[]> {
  const table = source === "ocb_otel" ? "ac_otel_log_observation" : "aw_langfuse_observation";
  const orderColumn = source === "ocb_otel" ? "start_time_ms" : "start_time";
  try {
    const rows = await db.query<ObservationRow>(
      `SELECT * FROM ${table} WHERE trace_id = ? ORDER BY ${orderColumn} ASC`,
      [traceId],
    );
    return rows.map(mapObservation);
  } catch (error) {
    console.warn(`[tclog] query ${table} failed: ${error instanceof Error ? error.message : String(error)}`);
    return [];
  }
}

function toWorkflowRun(row: FlowRunRow, matchTypes: string[]) {
  return {
    flowId: row.flow_id,
    workflowId: row.workflow_id,
    workflowTitle: row.workflow_title,
    status: row.status,
    botId: normalizeBotId(row.origin_bot_id),
    ownerId: row.user_id ?? row.triggered_by,
    originSessionId: row.origin_session_id,
    originSessionKey: row.origin_session_key,
    startedAt: asNumberMs(row.started_at) ?? row.started_at,
    completedAt: row.completed_at == null ? null : (asNumberMs(row.completed_at) ?? row.completed_at),
    nodeCount: row.node_count,
    failedCount: row.failed_count,
    currentPhase: row.current_phase,
    params: safeParseJson(row.params_json),
    input: safeParseJson(row.input_json),
    output: safeParseJson(row.result_json),
    totalDurationMs: row.total_duration_ms,
    totalTokenUsage: row.total_token_usage,
    nodes: [],
    matchTypes,
  };
}

function toWorkflowNode(row: NodeExecutionRow) {
  return {
    id: asFiniteNumber(col(row, "id")),
    flowId: col<string>(row, "flow_id") ?? "",
    workflowId: col<string>(row, "workflow_id") ?? "",
    nodeId: col<string>(row, "node_id") ?? "",
    nodeTitle: col<string | null>(row, "node_title") ?? null,
    executorType: col<string | null>(row, "executor_type") ?? null,
    status: col<string | null>(row, "status") ?? null,
    attempt: asFiniteNumber(col(row, "attempt")) ?? 0,
    sessionKey: col<string | null>(row, "session_key") ?? null,
    sessionId: col<string | null>(row, "session_id") ?? null,
    embeddedSessionKey: col<string | null>(row, "embedded_session_key") ?? null,
    input: safeParseJson(col(row, "input_json") ?? null),
    output: safeParseJson(col(row, "output_json") ?? null),
    errorText: col<string | null>(row, "error_text") ?? null,
    durationMs: asFiniteNumber(col(row, "duration_ms")),
    tokenUsage: safeParseJson(col(row, "token_usage_json") ?? null),
    systemContext: safeParseJson(col(row, "system_context_json") ?? null),
    progressMessage: col<string | null>(row, "progress_message") ?? null,
    startedAt: asNumberMs(col(row, "started_at")),
    completedAt: asNumberMs(col(row, "completed_at")),
  };
}

async function queryWorkflowNodes(db: IDatabase, flowIds: string[], perFlowLimit = 200) {
  if (flowIds.length === 0) return new Map<string, ReturnType<typeof toWorkflowNode>[]>();
  const byFlow = new Map<string, ReturnType<typeof toWorkflowNode>[]>();
  try {
    const rows = await db.query<NodeExecutionRow>(
      `SELECT * FROM node_executions
       WHERE flow_id IN (${flowIds.map(() => "?").join(",")})
       ORDER BY flow_id ASC, started_at ASC, id ASC`,
      flowIds,
    );
    for (const row of rows) {
      const node = toWorkflowNode(row);
      const list = byFlow.get(node.flowId) ?? [];
      if (list.length < perFlowLimit) list.push(node);
      byFlow.set(node.flowId, list);
    }
  } catch (error) {
    console.warn(`[tclog] query node_executions failed: ${error instanceof Error ? error.message : String(error)}`);
  }
  return byFlow;
}

export async function findWorkflowRuns(db: IDatabase, params: {
  ownerId: string;
  botId?: string;
  taskId?: string;
  sessionIds?: string[];
  sessionKeys?: string[];
  traceIds?: string[];
  from?: number;
  to?: number;
  limit: number;
}): Promise<ReturnType<typeof toWorkflowRun>[]> {
  const conds: string[] = [];
  const values: unknown[] = [];
  const matchTypes: string[] = [];
  if (params.ownerId) {
    conds.push("(user_id = ? OR triggered_by = ?)");
    values.push(params.ownerId, params.ownerId);
  }
  if (params.botId) {
    if (params.ownerId) {
      conds.push("origin_bot_id IN (?, ?)");
      values.push(params.botId, `${params.botId}:${params.ownerId}`);
    } else {
      conds.push("origin_bot_id = ?");
      values.push(params.botId);
    }
  }
  const orConds: string[] = [];
  if (params.taskId) {
    orConds.push("flow_id = ?", "identity_key = ?");
    values.push(params.taskId, params.taskId);
    matchTypes.push("task_id");
  }
  if (params.sessionIds?.length) {
    orConds.push(`origin_session_id IN (${params.sessionIds.map(() => "?").join(",")})`);
    values.push(...params.sessionIds);
    matchTypes.push("session_id");
  }
  if (params.sessionKeys?.length) {
    orConds.push(`origin_session_key IN (${params.sessionKeys.map(() => "?").join(",")})`);
    values.push(...params.sessionKeys);
    matchTypes.push("session_key");
  }
  if (orConds.length > 0) conds.push(`(${orConds.join(" OR ")})`);
  if (conds.length === 0) return [];
  if (params.from) {
    conds.push("started_at >= ?");
    values.push(Math.floor(params.from / 1000));
  }
  if (params.to) {
    conds.push("started_at <= ?");
    values.push(Math.floor(params.to / 1000));
  }
  try {
    const rows = await db.query<FlowRunRow>(
      `SELECT * FROM flow_runs WHERE ${conds.join(" AND ")} ORDER BY started_at DESC LIMIT ?`,
      [...values, params.limit],
    );
    return rows.map((row) => toWorkflowRun(row, matchTypes));
  } catch (error) {
    console.warn(`[tclog] query flow_runs failed: ${error instanceof Error ? error.message : String(error)}`);
    return [];
  }
}

async function listAuthorizedBotRows(db: IDatabase, ownerId: string, status: "active" | "all" = "active") {
  const rows: Array<{
    bot_id: string | null; bot_name?: string | null; env?: string | null;
    device_provider?: string | null; source: string; owner_id?: string | null;
  }> = [];
  const ownedBotRows = await db.query<Record<string, unknown>>(
    `SELECT b.*, d.device_provider
     FROM ac_bots b
     LEFT JOIN ac_entity_device_binding d ON d.id = b.binding_id
     WHERE (b.entity_id = ? OR b.owner_id = ?) AND b.is_delete = 0 AND b.bot_id IS NOT NULL AND b.bot_id <> ''
       AND (? = 'all' OR LOWER(b.status) = 'active')
     ORDER BY b.id DESC`,
    [ownerId, ownerId, status],
  ).catch(() => []);
  rows.push(...ownedBotRows.map((row) => ({
    bot_id: col<string | null>(row, "bot_id") ?? null,
    bot_name: col<string | null>(row, "bot_name") ?? null,
    env: col<string | null>(row, "env") ?? col<string | null>(row, "environment") ?? col<string | null>(row, "bot_env") ?? null,
    device_provider: col<string | null>(row, "device_provider") ?? null,
    owner_id: col<string | null>(row, "owner_id") ?? ownerId,
    source: "ac_bots",
  })));

  const collaboratorBotRows = await db.query<{
    bot_id: string | null; bot_name: string | null; env: string | null;
    owner_id: string | null; device_provider: string | null;
  }>(
    `SELECT DISTINCT c.bot_id, c.owner_id, b.bot_name, b.env, d.device_provider
     FROM ac_bot_collaborator c
     LEFT JOIN ac_bots b ON b.bot_id = c.bot_id
       AND (b.owner_id = c.owner_id OR b.entity_id = c.owner_id) AND b.is_delete = 0
     LEFT JOIN ac_entity_device_binding d ON d.id = b.binding_id
     WHERE c.user_id = ? AND c.bot_id IS NOT NULL AND c.bot_id <> ''
     ORDER BY c.id DESC`,
    [ownerId],
  ).catch(() => []);
  rows.push(...collaboratorBotRows.map((row) => ({
    bot_id: row.bot_id, bot_name: row.bot_name, env: row.env, owner_id: row.owner_id,
    device_provider: row.device_provider, source: "ac_bot_collaborator",
  })));
  return rows;
}

async function canAccessBot(db: IDatabase, ownerId: string, botId: string | undefined) {
  if (!botId) return true;
  const normalized = normalizeBotId(botId);
  if (!normalized) return true;
  if (normalized === "default" || normalized === `${ownerId}_default`) return true;
  const authorizedRows = await listAuthorizedBotRows(db, ownerId, "all");
  return authorizedRows.some((row) => normalizeBotId(row.bot_id) === normalized);
}

async function queryTraceById(db: QueryableDb, traceId: string, source: "ocb_otel" | "langfuse_legacy"): Promise<TCLogTrace | null> {
  const table = source === "ocb_otel" ? "ac_otel_log_trace" : "aw_langfuse_traces";
  const mapper = source === "ocb_otel" ? mapOtelTrace : mapLegacyTrace;
  try {
    const rows = await db.query<TraceRow>(
      `SELECT * FROM ${table} WHERE trace_id = ? LIMIT 1`,
      [traceId],
    );
    return rows[0] ? mapper(rows[0], ["trace_id"]) : null;
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    if (source === "ocb_otel") {
      console.warn(`[tclog] tc_direct_query_failed: ${msg}`);
      throw new Error(`tc_direct_query_failed: ${msg}`);
    }
    console.warn(`[tclog] query aw_langfuse_traces failed: ${msg}`);
    throw error;
  }
}

export async function queryTraceDetailWithAccess(db: IDatabase, params: {
  ownerId: string;
  requestedBotId?: string;
  traceId: string;
  dataSource: "auto" | "tc" | "langfuse";
  isLogAdmin?: boolean;
}): Promise<{ status: 200; trace: TCLogTrace; dataSource: "ocb_otel" | "langfuse_legacy"; fallbackUsed: boolean } | { status: 403 | 404; message: string }> {
  const sources: Array<"ocb_otel" | "langfuse_legacy"> = params.dataSource === "tc"
    ? ["ocb_otel"]
    : params.dataSource === "langfuse"
      ? ["langfuse_legacy"]
      : ["ocb_otel", "langfuse_legacy"];

  for (const source of sources) {
    const trace = await queryTraceById(db, params.traceId, source);
    if (!trace) continue;

    const actualBotId = normalizeBotId(trace.botId);
    const requestedBotId = normalizeBotId(params.requestedBotId);
    if (params.isLogAdmin) {
      return { status: 200, trace, dataSource: source, fallbackUsed: params.dataSource === "auto" && source === "langfuse_legacy" };
    }
    if (requestedBotId && requestedBotId !== actualBotId) {
      return { status: 403, message: "Requested botId does not match trace owner bot" };
    }

    const ownsTrace = trace.ownerId === params.ownerId;
    const canViewBot = actualBotId ? await canAccessBot(db, params.ownerId, actualBotId) : false;
    if (!ownsTrace && !canViewBot) {
      return { status: 403, message: "Cannot query this trace" };
    }

    return { status: 200, trace, dataSource: source, fallbackUsed: params.dataSource === "auto" && source === "langfuse_legacy" };
  }

  return { status: 404, message: `Trace ${params.traceId} not found` };
}

async function listBots(db: IDatabase, ownerId: string, status: "active" | "all") {
  const activeStatuses = ["running", "waiting", "blocked"];
  const rows = await listAuthorizedBotRows(db, ownerId, status);
  const authorizedBotIds = new Set(rows.map((row) => normalizeBotId(row.bot_id)).filter((botId): botId is string => !!botId));
  const canUseInferred = (botId: string | null | undefined) => {
    const normalized = normalizeBotId(botId);
    return !!normalized && authorizedBotIds.has(normalized);
  };

  const flowRows = await db.query<{ bot_id: string | null }>(
    `SELECT DISTINCT origin_bot_id AS bot_id
     FROM flow_runs
     WHERE (user_id = ? OR triggered_by = ?) AND origin_bot_id IS NOT NULL
       ${status === "active" ? `AND status IN (${activeStatuses.map(() => "?").join(",")})` : ""}
     ORDER BY origin_bot_id`,
    status === "active" ? [ownerId, ownerId, ...activeStatuses] : [ownerId, ownerId],
  ).catch(() => []);
  rows.push(...flowRows.filter((row) => canUseInferred(row.bot_id)).map((row) => ({ bot_id: row.bot_id, owner_id: ownerId, source: "flow_runs" })));

  const traceRows = await db.query<{ bot_id: string | null }>(
    `SELECT DISTINCT bot_id
     FROM ac_otel_log_trace
     WHERE bot_id IS NOT NULL AND bot_id <> ''
       AND user_id = ?
     ORDER BY bot_id`,
    [ownerId],
  ).catch(() => []);
  rows.push(...traceRows.filter((row) => canUseInferred(row.bot_id)).map((row) => ({ bot_id: row.bot_id, owner_id: ownerId, source: "ocb_trace" })));

  const bizRows = await db.query<{ bot_id: string | null }>(
    `SELECT DISTINCT bot_id
     FROM ac_otel_log_biz_ref
     WHERE bot_id IS NOT NULL AND bot_id <> '' AND user_id = ?
     ORDER BY bot_id`,
    [ownerId],
  ).catch(() => []);
  rows.push(...bizRows.filter((row) => canUseInferred(row.bot_id)).map((row) => ({ bot_id: row.bot_id, owner_id: ownerId, source: "ocb_biz_ref" })));

  // Provider is configuration metadata. Never infer it from logs or run records.
  const configuredBotIds = [...new Set(rows.map((row) => normalizeBotId(row.bot_id)).filter((botId): botId is string => !!botId))];
  const providerRows = configuredBotIds.length === 0 ? [] : await db.query<{
    bot_id: string; device_provider: string | null; active_engine: string | null;
    bot_type: string | null;
  }>(
    `SELECT b.bot_id, b.active_engine, b.bot_type, d.device_provider
     FROM ac_bots b
     LEFT JOIN ac_entity_device_binding d ON d.id = b.binding_id
     WHERE b.bot_id IN (${configuredBotIds.map(() => "?").join(",")}) AND b.is_delete = 0
     ORDER BY b.id DESC`,
    configuredBotIds,
  ).catch(() => []);
  const serviceRows = configuredBotIds.length === 0 ? [] : await db.query<{ bot_id: string }>(
    `SELECT DISTINCT b.bot_id
     FROM ac_bots b
     JOIN ac_bot_publish p ON p.source_bot_pk = b.id AND p.env = b.env
     WHERE b.bot_id IN (${configuredBotIds.map(() => "?").join(",")})
       AND b.is_delete = 0 AND p.status = 'success'`,
    configuredBotIds,
  ).catch(() => []);
  const serviceBotIds = new Set(serviceRows.map((row) => row.bot_id));
  const configuredMetadata = new Map<string, { deviceProvider: string | null; activeEngine: string | null; botType: string | null; hasServiceBot: boolean }>();
  for (const row of providerRows) {
    if (!configuredMetadata.has(row.bot_id)) {
      configuredMetadata.set(row.bot_id, {
        deviceProvider: row.device_provider?.toLowerCase() ?? null,
        activeEngine: row.active_engine?.toLowerCase() ?? null,
        botType: row.bot_type?.toLowerCase() ?? null,
        hasServiceBot: serviceBotIds.has(row.bot_id),
      });
    }
  }

  const byBot = new Map<string, {
    source: string; ownerId: string | null; botName: string | null;
    env: string | null; deviceProvider: string | null; activeEngine: string | null;
    botType: string | null; hasServiceBot: boolean;
  }>();
  rows
    .map((row) => ({
      botId: normalizeBotId(row.bot_id), source: row.source, ownerId: row.owner_id ?? null,
      botName: row.bot_name ?? null, env: row.env ?? null,
      deviceProvider: configuredMetadata.get(normalizeBotId(row.bot_id) ?? "")?.deviceProvider ?? null,
      activeEngine: configuredMetadata.get(normalizeBotId(row.bot_id) ?? "")?.activeEngine ?? null,
      botType: configuredMetadata.get(normalizeBotId(row.bot_id) ?? "")?.botType ?? null,
      hasServiceBot: configuredMetadata.get(normalizeBotId(row.bot_id) ?? "")?.hasServiceBot ?? false,
    }))
    .filter((row): row is {
      botId: string; source: string; ownerId: string | null; botName: string | null;
      env: string | null; deviceProvider: string | null; activeEngine: string | null;
      botType: string | null; hasServiceBot: boolean;
    } => !!row.botId)
    .forEach((row) => {
      const prev = byBot.get(row.botId);
      byBot.set(row.botId, {
        ownerId: prev?.ownerId ?? row.ownerId,
        botName: prev?.botName ?? row.botName,
        env: prev?.env ?? row.env,
        deviceProvider: prev?.deviceProvider ?? row.deviceProvider,
        activeEngine: prev?.activeEngine ?? row.activeEngine,
        botType: prev?.botType ?? row.botType,
        hasServiceBot: prev?.hasServiceBot || row.hasServiceBot,
        source: prev ? Array.from(new Set([...prev.source.split(","), row.source])).join(",") : row.source,
      });
    });

  return Array.from(byBot.entries())
    .map(([botId, info]) => ({
      botId, botName: info.botName, env: info.env, deviceProvider: info.deviceProvider,
      activeEngine: info.activeEngine, botType: info.botType, hasServiceBot: info.hasServiceBot,
      source: info.source, ownerId: info.ownerId,
    }))
    .sort((a, b) => a.botId.localeCompare(b.botId))
    .map(({ botId, botName, env, deviceProvider, activeEngine, botType, hasServiceBot, source, ownerId: botOwnerId }) => ({
      botId,
      botName,
      env,
      deviceProvider,
      displayBotId: `${botName ? `${botName} / ` : ""}${botOwnerId && botOwnerId !== ownerId ? `${botId} (${botOwnerId})` : botId} / ${env || "-"}`,
      status,
      activeEngine,
      botType,
      hasServiceBot,
      source,
      ownerId: botOwnerId,
      accessType: source.split(",").includes("ac_bot_collaborator") ? "collaborator" : "owner",
    }));
}

function mergeTaskSummaries(tasks: TCLogTaskSummary[], limit: number): TCLogTaskSummary[] {
  const byKey = new Map<string, TCLogTaskSummary>();
  for (const task of tasks) {
    if (!task.taskId) continue;
    const key = `${task.bizScene}:${task.taskId}`;
    const existing = byKey.get(key);
    if (!existing) {
      byKey.set(key, { ...task });
      continue;
    }
    existing.refCount += task.refCount;
    existing.traceCount += task.traceCount;
    existing.workflowRunCount += task.workflowRunCount;
    existing.lastEventTimeMs = Math.max(existing.lastEventTimeMs ?? 0, task.lastEventTimeMs ?? 0) || null;
    existing.botId = existing.botId ?? task.botId;
    existing.ownerId = existing.ownerId ?? task.ownerId;
    existing.source = Array.from(new Set([...existing.source.split(","), task.source])).join(",");
  }
  return Array.from(byKey.values())
    .sort((a, b) => (b.lastEventTimeMs ?? 0) - (a.lastEventTimeMs ?? 0))
    .slice(0, limit);
}

export async function listBusinessTasks(db: IDatabase, params: {
  ownerId: string;
  botId?: string;
  bizScene?: string;
  taskId?: string;
  from?: number;
  to?: number;
  limit: number;
}): Promise<TCLogTaskSummary[]> {
  const tasks: TCLogTaskSummary[] = [];
  const bizConds = ["user_id = ?"];
  const bizValues: unknown[] = [params.ownerId];
  if (params.botId) {
    bizConds.push("bot_id IN (?, ?)");
    bizValues.push(params.botId, `${params.botId}:${params.ownerId}`);
  }
  if (params.bizScene) {
    bizConds.push("biz_scene = ?");
    bizValues.push(params.bizScene);
  }
  if (params.taskId) {
    bizConds.push("biz_task_id = ?");
    bizValues.push(params.taskId);
  }
  if (params.from) {
    bizConds.push("gmt_modified >= FROM_UNIXTIME(?)");
    bizValues.push(Math.floor(params.from / 1000));
  }
  if (params.to) {
    bizConds.push("gmt_modified <= FROM_UNIXTIME(?)");
    bizValues.push(Math.floor(params.to / 1000));
  }
  try {
    const rows = await db.query<Record<string, unknown>>(
      `SELECT biz_scene, biz_task_id, MAX(bot_id) AS bot_id, MAX(user_id) AS owner_id,
              COUNT(*) AS ref_count, UNIX_TIMESTAMP(MAX(gmt_modified)) * 1000 AS last_event_time_ms
       FROM ac_otel_log_biz_ref
       WHERE ${bizConds.join(" AND ")}
       GROUP BY biz_scene, biz_task_id
       ORDER BY MAX(gmt_modified) DESC
       LIMIT ?`,
      [...bizValues, params.limit],
    );
    tasks.push(...rows.map((row) => ({
      bizScene: col<string>(row, "biz_scene") ?? "default",
      taskId: col<string>(row, "biz_task_id") ?? "",
      botId: normalizeBotId(col<string | null>(row, "bot_id") ?? null),
      ownerId: col<string | null>(row, "owner_id") ?? null,
      source: "ocb_biz_ref",
      refCount: asFiniteNumber(col(row, "ref_count")) ?? 0,
      traceCount: 0,
      workflowRunCount: 0,
      lastEventTimeMs: asNumberMs(col(row, "last_event_time_ms")),
    })));
  } catch (error) {
    console.warn(`[tclog] list ac_otel_log_biz_ref tasks failed: ${error instanceof Error ? error.message : String(error)}`);
  }

  const traceConds = ["biz_task_id IS NOT NULL", "biz_task_id <> ''", "user_id = ?"];
  const traceValues: unknown[] = [params.ownerId];
  if (params.botId) {
    traceConds.push("bot_id IN (?, ?)");
    traceValues.push(params.botId, `${params.botId}:${params.ownerId}`);
  }
  if (params.bizScene) {
    traceConds.push("biz_scene = ?");
    traceValues.push(params.bizScene);
  }
  if (params.taskId) {
    traceConds.push("biz_task_id = ?");
    traceValues.push(params.taskId);
  }
  if (params.from) {
    traceConds.push("start_time_ms >= ?");
    traceValues.push(params.from);
  }
  if (params.to) {
    traceConds.push("start_time_ms <= ?");
    traceValues.push(params.to);
  }
  try {
    const rows = await db.query<Record<string, unknown>>(
      `SELECT COALESCE(biz_scene, 'default') AS biz_scene, biz_task_id,
              MAX(bot_id) AS bot_id, MAX(user_id) AS owner_id,
              COUNT(DISTINCT trace_id) AS trace_count, MAX(start_time_ms) AS last_event_time_ms
       FROM ac_otel_log_trace
       WHERE ${traceConds.join(" AND ")}
       GROUP BY COALESCE(biz_scene, 'default'), biz_task_id
       ORDER BY MAX(start_time_ms) DESC
       LIMIT ?`,
      [...traceValues, params.limit],
    );
    tasks.push(...rows.map((row) => ({
      bizScene: col<string>(row, "biz_scene") ?? "default",
      taskId: col<string>(row, "biz_task_id") ?? "",
      botId: normalizeBotId(col<string | null>(row, "bot_id") ?? null),
      ownerId: col<string | null>(row, "owner_id") ?? null,
      source: "ocb_trace",
      refCount: 0,
      traceCount: asFiniteNumber(col(row, "trace_count")) ?? 0,
      workflowRunCount: 0,
      lastEventTimeMs: asNumberMs(col(row, "last_event_time_ms")),
    })));
  } catch (error) {
    console.warn(`[tclog] list ac_otel_log_trace tasks failed: ${error instanceof Error ? error.message : String(error)}`);
  }

  const flowConds = ["(user_id = ? OR triggered_by = ?)"];
  const flowValues: unknown[] = [params.ownerId, params.ownerId];
  if (params.botId) {
    flowConds.push("origin_bot_id IN (?, ?)");
    flowValues.push(params.botId, `${params.botId}:${params.ownerId}`);
  }
  if (params.taskId) {
    flowConds.push("(flow_id = ? OR identity_key = ?)");
    flowValues.push(params.taskId, params.taskId);
  }
  if (params.from) {
    flowConds.push("started_at >= ?");
    flowValues.push(Math.floor(params.from / 1000));
  }
  if (params.to) {
    flowConds.push("started_at <= ?");
    flowValues.push(Math.floor(params.to / 1000));
  }
  try {
    const rows = await db.query<Record<string, unknown>>(
      `SELECT COALESCE(identity_key, flow_id) AS task_id, MAX(origin_bot_id) AS bot_id,
              MAX(COALESCE(user_id, triggered_by)) AS owner_id, COUNT(*) AS workflow_run_count,
              MAX(started_at) * 1000 AS last_event_time_ms
       FROM flow_runs
       WHERE ${flowConds.join(" AND ")}
       GROUP BY COALESCE(identity_key, flow_id)
       ORDER BY MAX(started_at) DESC
       LIMIT ?`,
      [...flowValues, params.limit],
    );
    tasks.push(...rows.map((row) => ({
      bizScene: "clawmind_workflow",
      taskId: col<string>(row, "task_id") ?? "",
      botId: normalizeBotId(col<string | null>(row, "bot_id") ?? null),
      ownerId: col<string | null>(row, "owner_id") ?? null,
      source: "clawmind_workflow",
      refCount: 0,
      traceCount: 0,
      workflowRunCount: asFiniteNumber(col(row, "workflow_run_count")) ?? 0,
      lastEventTimeMs: asNumberMs(col(row, "last_event_time_ms")),
    })));
  } catch (error) {
    console.warn(`[tclog] list flow_runs tasks failed: ${error instanceof Error ? error.message : String(error)}`);
  }

  return mergeTaskSummaries(tasks, params.limit);
}

export function createTCLogRouter(
  db: IDatabase,
  flowRunRepo: FlowRunRepository | null,
  botPermRepo: BotWorkflowPermissionRepository | null,
): Router {
  const router = Router();

  router.use((req, res, next) => {
    delete req.headers["if-none-match"];
    delete req.headers["if-modified-since"];
    res.setHeader("Cache-Control", "no-store, no-cache, must-revalidate, proxy-revalidate");
    res.setHeader("Pragma", "no-cache");
    res.setHeader("Expires", "0");
    res.removeHeader("ETag");
    next();
  });

  router.get("/bots", asyncHandler(async (req: Request, res: Response) => {
    const owner = resolveOwner(req);
    if (owner.ok === "error") {
      res.status(owner.status).json({ error: "Bad Request", message: owner.message });
      return;
    }
    const status = req.query.status === "all" ? "all" : "active";
    const bots = db.dbType === "noop" ? [] : await listBots(db, owner.ownerId, status);
    res.json({ ownerId: owner.ownerId, bots });
  }));

  router.get("/query", asyncHandler(async (req: Request, res: Response) => {
    const isEmbed = req.query.embed === "true" || req.query.embed === "1";
    const sessionKey = ((req.query.sessionKey ?? req.query.session_key) as string | undefined)?.trim() || undefined;
    const sessionId = ((req.query.sessionId ?? req.query.session_id) as string | undefined)?.trim() || undefined;
    const traceId = ((req.query.traceId ?? req.query.trace_id) as string | undefined)?.trim() || undefined;
    const groupBy = req.query.groupBy === "session" ? "session" : "trace";
    const owner: OwnerResult = isEmbed ? { ok: "success", ownerId: "" } : resolveOwner(req);
    if (owner.ok === "error") {
      res.status(owner.status).json({ error: "Bad Request", message: owner.message });
      return;
    }
    if (db.dbType === "noop") {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    const limit = Math.min(parseInt(String(req.query.limit ?? "50"), 10) || 50, 200);
    const offset = parseInt(String(req.query.offset ?? "0"), 10) || 0;
    const botId = (req.query.botId as string | undefined)?.trim() || undefined;
    const dataSource = parseTraceDataSource(req.query.dataSource);
    if (isEmbed && !sessionKey && !sessionId) {
      res.status(400).json({ error: "Bad Request", message: "sessionKey or sessionId is required" });
      return;
    }
    if (!req.isLogAdmin && !isEmbed && !(await canAccessBot(db, owner.ownerId, botId))) {
      res.status(403).json({ error: "Forbidden", message: "Cannot query this bot" });
      return;
    }
    const traceParams = {
      ownerId: owner.ownerId,
      botId,
      traceId,
      sessionId,
      sessionKey,
      skipOwnerFilter: !!req.isLogAdmin && !!(traceId || sessionId || sessionKey),
      keyword: (req.query.keyword as string | undefined)?.trim() || undefined,
      from: req.query.from ? Number(req.query.from) : undefined,
      to: req.query.to ? Number(req.query.to) : undefined,
    };
    if (groupBy === "session") {
      const result = await querySessionsWithFallback(db, traceParams, limit, offset, dataSource);
      res.json({
        query: req.query,
        sessions: result.sessions,
        traces: [],
        summary: {
          sessionCount: result.sessions.length,
          traceCount: 0,
          totalTokens: 0,
          totalCost: 0,
        },
        dataSource: result.dataSource,
        fallbackUsed: result.fallbackUsed,
      });
      return;
    }

    const result = await queryTracesWithFallback(db, traceParams, limit, offset, dataSource);
    const sessions = groupSessions(result.traces);
    res.json({
      query: req.query,
      sessions,
      traces: result.traces,
      summary: {
        sessionCount: sessions.length,
        traceCount: result.traces.length,
        totalTokens: result.traces.reduce((sum, t) => sum + (t.totalTokens ?? 0), 0),
        totalCost: result.traces.reduce((sum, t) => sum + (t.totalCost ?? 0), 0),
      },
      dataSource: result.dataSource,
      fallbackUsed: result.fallbackUsed,
    });
  }));

  router.get("/tasks", asyncHandler(async (req: Request, res: Response) => {
    const owner = resolveOwner(req);
    if (owner.ok === "error") {
      res.status(owner.status).json({ error: "Bad Request", message: owner.message });
      return;
    }
    if (db.dbType === "noop") {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    const limit = Math.min(parseInt(String(req.query.limit ?? "100"), 10) || 100, 200);
    const botId = (req.query.botId as string | undefined)?.trim() || undefined;
    if (!req.isLogAdmin && !(await canAccessBot(db, owner.ownerId, botId))) {
      res.status(403).json({ error: "Forbidden", message: "Cannot query this bot" });
      return;
    }
    const tasks = await listBusinessTasks(db, {
      ownerId: owner.ownerId,
      botId,
      bizScene: (req.query.bizScene as string | undefined)?.trim() || undefined,
      taskId: (req.query.taskId as string | undefined)?.trim() || undefined,
      from: req.query.from ? Number(req.query.from) : undefined,
      to: req.query.to ? Number(req.query.to) : undefined,
      limit,
    });
    res.json({
      query: { ...req.query, ownerId: owner.ownerId },
      tasks,
      dataSource: "ocb_otel,clawmind_workflow",
    });
  }));

  router.get("/task-search", asyncHandler(async (req: Request, res: Response) => {
    const isEmbed = req.query.embed === "true" || req.query.embed === "1";
    const owner: OwnerResult = isEmbed ? { ok: "success", ownerId: "" } : resolveOwner(req);
    if (owner.ok === "error") {
      res.status(owner.status).json({ error: "Bad Request", message: owner.message });
      return;
    }
    if (db.dbType === "noop") {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    const taskId = (req.query.taskId as string | undefined)?.trim();
    const bizScene = (req.query.bizScene as string | undefined)?.trim();
    const botId = (req.query.botId as string | undefined)?.trim() || undefined;
    const dataSource = parseTraceDataSource(req.query.dataSource);
    if (isEmbed && (!taskId || !bizScene)) {
      res.status(400).json({ error: "Bad Request", message: "bizScene and taskId are required" });
      return;
    }
    if (!req.isLogAdmin && !isEmbed && !(await canAccessBot(db, owner.ownerId, botId))) {
      res.status(403).json({ error: "Forbidden", message: "Cannot query this bot" });
      return;
    }
    const from = parseTimeMs(req.query.from);
    const to = parseTimeMs(req.query.to);
    const requestedLimit = parseInt(String(req.query.limit ?? "100"), 10) || 100;
    const limit = Math.max(1, Math.min(requestedLimit, taskId ? 200 : MAX_EMPTY_TASK_SEARCH_LIMIT));
    if (!taskId) {
      const timeRangeError = validateTimeRange(from, to);
      if (timeRangeError) {
        res.status(400).json({ error: "Bad Request", message: timeRangeError });
        return;
      }
      if (!botId && !bizScene) {
        res.status(400).json({ error: "Bad Request", message: "botId or bizScene is required when taskId is empty" });
        return;
      }
    }
    const refs = taskId ? await queryBizRefs(db, bizScene, taskId) : [];
    const traceResult = await queryTaskTraces(db, {
      ownerId: owner.ownerId,
      botId,
      bizScene,
      taskId,
      from,
      to,
    }, refs, limit, dataSource);
    const traces = traceResult.traces;
    const workflowLookup = buildWorkflowLookupParams({ bizScene, taskId, traces, refs });
    const workflowRuns = await findWorkflowRuns(db, {
      ownerId: owner.ownerId,
      botId,
      taskId: workflowLookup.taskId,
      sessionIds: workflowLookup.sessionIds,
      sessionKeys: workflowLookup.sessionKeys,
      traceIds: traces.map((t) => t.traceId),
      from,
      to,
      limit,
    });
    const nodesByFlowId = await queryWorkflowNodes(db, workflowRuns.map((run) => run.flowId));
    const workflowRunsWithNodes = workflowRuns.map((run) => ({
      ...run,
      nodes: nodesByFlowId.get(run.flowId) ?? [],
    }));
    const nodeStepCount = workflowRunsWithNodes.reduce((sum, run) => sum + run.nodes.length, 0);
    const timeline = [
      ...traces.map((trace) => ({
        id: trace.traceId,
        source: "ocb_trace",
        eventTimeMs: trace.startTimeMs ?? 0,
        title: trace.name || trace.traceId,
        status: trace.status,
        traceId: trace.traceId,
        sessionId: trace.sessionId,
        sessionKey: trace.sessionKey,
      })),
      ...workflowRunsWithNodes.map((run) => ({
        id: run.flowId,
        source: "clawmind_workflow",
        eventTimeMs: typeof run.startedAt === "number" ? run.startedAt : 0,
        title: run.workflowTitle || run.workflowId,
        status: run.status,
        flowId: run.flowId,
        sessionId: run.originSessionId,
        sessionKey: run.originSessionKey,
      })),
    ].sort((a, b) => b.eventTimeMs - a.eventTimeMs);

    res.json({
      query: { ...req.query, ownerId: owner.ownerId },
      summary: {
        botCount: new Set([...traces.map((t) => t.botId), ...workflowRunsWithNodes.map((r) => r.botId)].filter(Boolean)).size,
        traceCount: traces.length,
        workflowRunCount: workflowRunsWithNodes.length,
        nodeStepCount,
        errorCount: traces.filter((t) => t.status && /error|fail/i.test(t.status)).length
          + workflowRunsWithNodes.filter((r) => /failed|blocked/i.test(r.status)).length
          + workflowRunsWithNodes.reduce((sum, run) => sum + run.nodes.filter((node) => node.status && /error|fail/i.test(node.status)).length, 0),
        totalTokens: traces.reduce((sum, t) => sum + (t.totalTokens ?? 0), 0),
        totalCost: traces.reduce((sum, t) => sum + (t.totalCost ?? 0), 0),
      },
      relations: [
        ...refs.map((ref) => ({ type: ref.ref_type, value: ref.ref_value, source: "ocb_biz_ref" })),
        ...workflowLookup.sessionKeys.map((value) => ({ type: "session_key", value, source: "trace" })),
        ...workflowLookup.sessionIds.map((value) => ({ type: "session_id", value, source: "trace" })),
      ],
      traces,
      workflowRuns: workflowRunsWithNodes,
      timeline,
      dataSource: traceResult.dataSource,
      fallbackUsed: traceResult.fallbackUsed,
    });
  }));

  router.get("/traces/:traceId", asyncHandler(async (req: Request, res: Response) => {
    const isEmbed = req.query.embed === "true" || req.query.embed === "1";
    const owner: OwnerResult = isEmbed ? { ok: "success", ownerId: "" } : resolveOwner(req);
    if (owner.ok === "error") {
      res.status(owner.status).json({ error: "Bad Request", message: owner.message });
      return;
    }
    const traceId = String(req.params.traceId);
    const botId = (req.query.botId as string | undefined)?.trim() || undefined;
    if (isEmbed) {
      const source = parseTraceDataSource(req.query.dataSource);
      const sources: Array<"ocb_otel" | "langfuse_legacy"> = source === "tc"
        ? ["ocb_otel"]
        : source === "langfuse"
          ? ["langfuse_legacy"]
          : ["ocb_otel", "langfuse_legacy"];
      for (const dataSource of sources) {
        const trace = await queryTraceById(db, traceId, dataSource);
        if (!trace) continue;
        trace.observations = await queryObservations(db, trace.traceId, dataSource);
        res.json({ trace, dataSource, fallbackUsed: source === "auto" && dataSource === "langfuse_legacy" });
        return;
      }
      res.status(404).json({ error: "Not Found", message: `Trace ${traceId} not found` });
      return;
    }
    const result = await queryTraceDetailWithAccess(db, {
      ownerId: owner.ownerId,
      requestedBotId: botId,
      traceId,
      dataSource: parseTraceDataSource(req.query.dataSource),
      isLogAdmin: req.isLogAdmin ?? false,
    });
    if (result.status !== 200) {
      res.status(result.status).json({ error: result.status === 403 ? "Forbidden" : "Not Found", message: result.message });
      return;
    }
    const trace = result.trace;
    trace.observations = await queryObservations(db, trace.traceId, result.dataSource);
    res.json({ trace, dataSource: result.dataSource, fallbackUsed: result.fallbackUsed });
  }));

  router.get("/workflows/:flowId", asyncHandler(async (req: Request, res: Response) => {
    if (!flowRunRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    const owner = resolveOwner(req);
    if (owner.ok === "error") {
      res.status(owner.status).json({ error: "Bad Request", message: owner.message });
      return;
    }
    const flowId = String(req.params.flowId);
    const run = await flowRunRepo.findFullByFlowId(flowId);
    if (!run) {
      res.status(404).json({ error: "Not Found", message: `Flow ${flowId} not found` });
      return;
    }
    if (!req.isLogAdmin && run.user_id !== owner.ownerId && run.triggered_by !== owner.ownerId) {
      const canView = botPermRepo ? await botPermRepo.hasEditPermission(run.workflow_id, owner.ownerId, normalizeBotId(run.origin_bot_id) ?? undefined) : false;
      if (!canView) {
        res.status(403).json({ error: "Forbidden", message: "Cannot query another owner's workflow" });
        return;
      }
    }
    res.json({ workflowRun: toWorkflowRun(run, ["flow_id"]) });
  }));

  return router;
}
