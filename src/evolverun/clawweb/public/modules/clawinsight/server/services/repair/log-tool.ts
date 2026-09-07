import type { AntLogsCollector } from "../antlogs-collector.js";
import type {
  RepairDiscoveredIdentifierCandidate,
  RepairDiscoveredIdentifierKind,
  RepairTaskContext,
  RepairLogIdentifier,
  RepairLogSearchInput,
} from "./contracts.js";
import { RepairError, repairUnavailable, repairValidation } from "./errors.js";
import { containsRepairSecret, redactPersistableText, redactText } from "./redaction.js";

const IDENTIFIERS = new Set<RepairLogIdentifier>(["botId", "ownerId", "traceId", "relatedTaskId", "errorText"]);
const DISCOVERED_KEYS = new Map<string, RepairDiscoveredIdentifierKind>([
  ["bindingid", "bindingId"],
  ["publishid", "publishId"],
  ["deviceuuid", "deviceUuid"],
  ["sessionid", "sessionId"],
  ["traceid", "traceId"],
  ["taskid", "taskId"],
]);
const MAX_INLINE_RESULT_BYTES = 192 * 1024;
const COMPLETE_QUERY_TOKEN = /^[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*$/;
const QUERY_TOKEN = /[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*/g;
const QUERY_OPERATORS = new Set(["and", "or", "not"]);
const MAX_QUERY_TOKEN_LENGTH = 256;
const MAX_IDENTIFIER_QUERY_TOKENS = 8;
const MAX_ERROR_TEXT_QUERY_TOKENS = 5;

function discoveredKind(key: string): RepairDiscoveredIdentifierKind | null {
  return DISCOVERED_KEYS.get(key.replaceAll(/[^A-Za-z0-9]/g, "").toLowerCase()) ?? null;
}

function safeDiscoveredValue(value: unknown): string | null {
  if (typeof value !== "string" && typeof value !== "number") return null;
  const normalized = String(value).trim();
  if (!normalized || normalized.length > 256 || !/^[A-Za-z0-9_.:@/-]+$/.test(normalized)) return null;
  if (containsRepairSecret(normalized)) return null;
  return normalized;
}

function extractDiscoveredIdentifiers(
  entries: Array<{ message?: unknown; metadata?: unknown }>,
): RepairDiscoveredIdentifierCandidate[] {
  const found = new Map<string, RepairDiscoveredIdentifierCandidate>();
  const add = (kind: RepairDiscoveredIdentifierKind | null, value: unknown) => {
    const normalized = safeDiscoveredValue(value);
    if (!kind || !normalized || found.size >= 20) return;
    found.set(`${kind}:${normalized}`, { kind, value: normalized });
  };
  const visit = (value: unknown, depth = 0) => {
    if (!value || typeof value !== "object" || depth > 5) return;
    if (Array.isArray(value)) {
      value.slice(0, 50).forEach((item) => visit(item, depth + 1));
      return;
    }
    Object.entries(value as Record<string, unknown>).slice(0, 100).forEach(([key, child]) => {
      add(discoveredKind(key), child);
      visit(child, depth + 1);
    });
  };
  for (const entry of entries.slice(0, 200)) {
    visit(entry.metadata);
    if (typeof entry.message !== "string") continue;
    try {
      visit(JSON.parse(entry.message) as unknown);
    } catch {
      const pattern = /\b(binding[_-]?id|publish[_-]?id|device[_-]?uuid|session[_-]?id|trace[_-]?id|task[_-]?id)\s*[:=]\s*["']?([A-Za-z0-9_.:@/-]{1,256})/gi;
      for (const match of entry.message.matchAll(pattern)) add(discoveredKind(match[1]), match[2]);
    }
  }
  return [...found.values()];
}

function identifierValue(input: RepairTaskContext, identifier: RepairLogIdentifier): string | null {
  if (identifier === "botId") return input.target.botId;
  if (identifier === "ownerId") return input.target.ownerId;
  if (identifier === "traceId") return input.issue.traceId;
  if (identifier === "relatedTaskId") return input.issue.relatedTaskId;
  return input.issue.errorText;
}

/**
 * AntLogs' Repair search endpoint accepts bare literal tokens joined by the
 * lowercase `and` operator. It does not accept JSON string literals. Build
 * those tokens from server-registered values only; arbitrary error text is
 * reduced to a small set of literal atoms so it cannot introduce arbitrary
 * LogQL syntax.
 */
function safeQueryTokens(value: string, errorText: boolean): string[] {
  if (containsRepairSecret(value)) {
    repairValidation("unsafe_log_identifier_value", "日志标识包含不可用于查询的敏感信息");
  }
  const normalized = value.trim();
  const candidates = !errorText
    && normalized.length <= MAX_QUERY_TOKEN_LENGTH
    && COMPLETE_QUERY_TOKEN.test(normalized)
    ? [normalized]
    : normalized.match(QUERY_TOKEN) ?? [];
  const tokens = [...new Set(candidates.filter((token) =>
    token.length <= MAX_QUERY_TOKEN_LENGTH
    && (!errorText || token.length >= 3)
    && !QUERY_OPERATORS.has(token.toLowerCase()),
  ))];
  const limit = errorText ? MAX_ERROR_TEXT_QUERY_TOKENS : MAX_IDENTIFIER_QUERY_TOKENS;
  if (tokens.length < 1) {
    repairValidation("invalid_log_identifier_value", "日志标识无法安全转换为查询词");
  }
  if (!errorText && tokens.length > limit) {
    repairValidation("invalid_log_identifier_value", "日志标识无法安全转换为查询词");
  }
  return tokens.slice(0, limit);
}

function safeEpoch(value: unknown, fallback: number, field: string): number {
  if (value == null) return fallback;
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) repairValidation("invalid_log_time", `${field} 必须是 unix 秒`);
  return parsed;
}

function safeLogMessage(value: unknown): string {
  if (typeof value === "string") return redactPersistableText(value, 1024);
  try {
    return redactPersistableText(JSON.stringify(value), 1024);
  } catch {
    return redactPersistableText(String(value ?? ""), 1024);
  }
}

function jsonBytes(value: unknown): number {
  return Buffer.byteLength(JSON.stringify(value), "utf8");
}

export type RepairLogSourceCoverage = {
  coveredSources: Array<{
    name: string;
    status: "success" | "partial";
    entriesCount: number;
  }>;
  unavailableSources: Array<{
    name: string;
    reasonCode: "read_acl_denied" | "access_denied" | "query_failed";
    reason: "READ 权限不足" | "访问权限不足" | "查询失败";
  }>;
  interpretation?: string;
};

export function selectRepairLogSources(sources: Array<{
  name: string;
  app?: string;
  defaultEnabled?: boolean;
}>): { allowedSourceNames: string[]; defaultSourceNames: string[] } {
  const sourceNameCounts = new Map<string, number>();
  for (const source of sources) {
    const key = source.name.trim().toLowerCase();
    sourceNameCounts.set(key, (sourceNameCounts.get(key) ?? 0) + 1);
  }
  const allowed = sources.filter((source) => {
    const name = source.name.trim().toLowerCase();
    const app = source.app?.trim().toLowerCase();
    return sourceNameCounts.get(name) === 1 && name !== "clawweb" && app !== "clawweb";
  });
  const defaultApps = new Set(["agentclaw", "agentclawscs"]);
  return {
    allowedSourceNames: [...new Set(allowed.map((source) => source.name))],
    defaultSourceNames: [...new Set(allowed
      .filter((source) => defaultApps.has(source.app?.trim().toLowerCase() ?? ""))
      .map((source) => source.name))],
  };
}

function safeSourceName(value: unknown, index: number): string {
  if (typeof value !== "string" || !value.trim()) return `日志源 ${index + 1}`;
  const redacted = redactText(value, 64).replaceAll(/[\r\n\0]/g, " ").trim();
  return redacted || `日志源 ${index + 1}`;
}

function sourceFailureReason(error: unknown): Pick<
  RepairLogSourceCoverage["unavailableSources"][number],
  "reasonCode" | "reason"
> {
  const text = typeof error === "string" ? error : "";
  const mentionsRead = /\bread\b|读取|读权限/iu.test(text);
  const explicitlyDenied = /denied|forbidden|unauthori[sz]ed|not\s+allowed|权限不足|无权限|无权|未授权|被拒|拒绝|不允许/iu.test(text);
  if (mentionsRead && explicitlyDenied) {
    return { reasonCode: "read_acl_denied", reason: "READ 权限不足" };
  }
  if (explicitlyDenied) {
    return { reasonCode: "access_denied", reason: "访问权限不足" };
  }
  return { reasonCode: "query_failed", reason: "查询失败" };
}

/**
 * Turn collector-specific source failures into a small evidence-coverage
 * contract. The raw error remains available to the Repair workload, while
 * browser and Agent summaries can reason about missing evidence without
 * treating the observed service as failed.
 */
export function deriveRepairLogSourceCoverage(sources: unknown): RepairLogSourceCoverage {
  const coveredSources: RepairLogSourceCoverage["coveredSources"] = [];
  const unavailableSources: RepairLogSourceCoverage["unavailableSources"] = [];
  if (Array.isArray(sources)) {
    sources.forEach((value, index) => {
      const source = value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
      const name = safeSourceName(source.name, index);
      const status = typeof source.status === "string" ? source.status.toLowerCase() : "failed";
      if (status === "success" || status === "partial") {
        coveredSources.push({
          name,
          status,
          entriesCount: Number.isSafeInteger(source.entriesCount) && Number(source.entriesCount) >= 0
            ? Number(source.entriesCount)
            : 0,
        });
        return;
      }
      unavailableSources.push({ name, ...sourceFailureReason(source.error) });
    });
  }
  return {
    coveredSources,
    unavailableSources,
    ...(unavailableSources.length > 0
      ? { interpretation: "未覆盖的日志源仅表示本次未取得该来源的证据，不代表对应服务异常。" }
      : {}),
  };
}

function boundedLogResult(input: {
  status: "success" | "partial" | "unknown";
  queryScope: Record<string, unknown>;
  entries: Array<{ timestamp: string; level: string; source: string; message: string; traceId: string | null }>;
  evidenceEntries: Array<{ message?: unknown; metadata?: unknown }>;
  sources: Array<Record<string, unknown>>;
  sourceCoverage: RepairLogSourceCoverage;
  durationMs: number;
}): Record<string, unknown> {
  const selected = [...input.entries];
  const build = () => {
    const truncated = selected.length < input.entries.length;
    return {
      status: truncated ? (selected.length > 0 ? "partial" : "unknown") : input.status,
      queryScope: input.queryScope,
      entries: selected,
      sources: input.sources,
      sourceCoverage: input.sourceCoverage,
      durationMs: input.durationMs,
      discoveredIdentifiers: extractDiscoveredIdentifiers(
        input.evidenceEntries.slice(0, selected.length),
      ),
      truncated,
      totalEntries: input.entries.length,
      returnedEntries: selected.length,
    };
  };
  let output = build();
  while (selected.length > 0 && jsonBytes(output) > MAX_INLINE_RESULT_BYTES) {
    selected.pop();
    output = build();
  }
  if (jsonBytes(output) > MAX_INLINE_RESULT_BYTES) {
    throw new RepairError(502, "repair_log_result_too_large", "AntLogs 元数据超过 Repair 内联结果上限");
  }
  return output;
}

export class RepairLogTool {
  readonly sourceNames: string[];
  private readonly sourceNameSet: Set<string>;
  private readonly defaultSourceNames: string[];

  constructor(
    private readonly collector: AntLogsCollector | null,
    sourceNames: string[],
    defaultSourceNames: string[] = sourceNames,
  ) {
    this.sourceNames = [...new Set(sourceNames)];
    this.sourceNameSet = new Set(this.sourceNames);
    this.defaultSourceNames = [...new Set(defaultSourceNames)].filter((source) => this.sourceNameSet.has(source));
  }

  async search(
    run: RepairTaskContext,
    request: RepairLogSearchInput,
    verifiedDiscovered: RepairDiscoveredIdentifierCandidate[] = [],
  ): Promise<Record<string, unknown>> {
    if (!this.collector) return repairUnavailable("repair_antlogs_not_configured", "Repair AntLogs OpenAPI 未配置");
    const unsafe = request as RepairLogSearchInput & { query?: unknown; logql?: unknown };
    if (unsafe.query != null || unsafe.logql != null) {
      repairValidation("raw_log_query_forbidden", "Repair 日志工具不接受原始 query 或 LogQL");
    }
    const requestedIdentifiers = request.identifiers ?? [];
    if (!Array.isArray(requestedIdentifiers) || !Array.isArray(request.discoveredIdentifiers ?? [])) {
      repairValidation("invalid_log_identifiers", "日志查询标识格式不合法");
    }
    const identifiers = [...new Set(requestedIdentifiers)];
    if (identifiers.length + verifiedDiscovered.length < 1 || identifiers.length + verifiedDiscovered.length > 5) {
      repairValidation("invalid_log_identifiers", "日志查询必须选择 1 到 5 个已登记或已发现标识");
    }
    if (identifiers.some((identifier) => !IDENTIFIERS.has(identifier))) {
      repairValidation("invalid_log_identifiers", "日志查询包含未允许的标识");
    }
    const values = identifiers.map((identifier) => ({
      identifier,
      value: identifierValue(run, identifier),
    }));
    const missing = values.filter((item) => !item.value).map((item) => item.identifier);
    if (missing.length > 0) {
      repairValidation("missing_log_identifiers", `Repair Task 未登记以下日志标识: ${missing.join(", ")}`);
    }
    const sources = request.sources?.length ? [...new Set(request.sources)] : [...this.defaultSourceNames];
    if (sources?.some((source) => !this.sourceNameSet.has(source))) {
      repairValidation("invalid_log_sources", "日志查询包含未配置的 source");
    }
    if (sources.length === 0) {
      return repairUnavailable("repair_antlogs_sources_not_configured", "Repair 没有可用的默认日志源");
    }
    const from = safeEpoch(request.from, run.issue.timeRange.from, "from");
    const to = safeEpoch(request.to, run.issue.timeRange.to, "to");
    if (from >= to) repairValidation("invalid_log_time_range", "日志查询 from 必须早于 to");
    const limit = request.limit == null ? 100 : Number(request.limit);
    if (!Number.isSafeInteger(limit) || limit < 1 || limit > 200) {
      repairValidation("invalid_log_limit", "日志查询 limit 必须在 1 到 200 之间");
    }
    const queryTokens = [
      ...values.flatMap((item) => safeQueryTokens(item.value as string, item.identifier === "errorText")),
      ...verifiedDiscovered.flatMap((item) => safeQueryTokens(item.value, false)),
    ];
    const query = [...new Set(queryTokens)].join(" and ");
    let result;
    try {
      result = await this.collector.search({
        keyword: query,
        sources,
        from,
        to,
        limit,
        suppressQueryLog: true,
      });
    } catch {
      throw new RepairError(
        502,
        "antlogs_query_failed",
        "AntLogs 查询失败",
      );
    }
    const entries = result.entries.slice(0, limit);
    const sourceResults = result.sourceResults.map((source) => ({
      name: source.source.name,
      status: source.status,
      entriesCount: source.entriesCount,
      totalAvailable: source.totalAvailable,
      durationMs: source.durationMs,
      error: source.error ? redactPersistableText(source.error, 512) : null,
    }));
    return boundedLogResult({
      status: result.allSourcesSucceeded ? "success" : result.entries.length > 0 ? "partial" : "unknown",
      queryScope: {
        identifiers,
        discoveredIdentifiers: verifiedDiscovered,
        sources,
        from,
        to,
        limit,
      },
      entries: entries.map((entry) => ({
        timestamp: redactPersistableText(entry.timestamp, 128),
        level: redactPersistableText(entry.level, 64),
        source: redactPersistableText(entry.source, 128),
        message: safeLogMessage(entry.message),
        traceId: typeof entry.metadata?.traceId === "string"
          ? redactPersistableText(entry.metadata.traceId, 256)
          : null,
      })),
      evidenceEntries: entries,
      sources: sourceResults,
      sourceCoverage: deriveRepairLogSourceCoverage(sourceResults),
      durationMs: result.durationMs,
    });
  }
}
