import {
  CHECK_VERSION, DIAGNOSIS_VERSION, MonitoringError,
  type BotCheck, type DiagnosisEvent, type DiagnosisQuery,
} from "./contracts.js";

export function invalid(message = "上报字段不符合契约。"): never {
  throw new MonitoringError("INVALID_EVENT", message);
}
export function record(input: unknown, allowed: readonly string[]): Record<string, unknown> {
  if (!input || typeof input !== "object" || Array.isArray(input)) invalid();
  const value = input as Record<string, unknown>;
  if (Object.keys(value).some((key) => !allowed.includes(key))) invalid("包含未约定的字段。");
  return value;
}
export function id(value: unknown): string {
  if (typeof value !== "string" || !/^[A-Za-z0-9_.:-]{1,128}$/.test(value)) invalid("ID 格式错误。");
  return value;
}
export function oneOf<T extends string>(value: unknown, values: readonly T[]): T {
  if (typeof value !== "string" || !values.includes(value as T)) invalid();
  return value as T;
}
export function nullableText(value: unknown, max: number): string | null {
  if (value == null) return null;
  if (typeof value !== "string") invalid();
  if ([...value].length > max) throw new MonitoringError("PAYLOAD_TOO_LARGE", "字段长度超限。");
  // Unpaired UTF-16 surrogates cannot round-trip through a UTF-8 business database.
  if (/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value)) invalid();
  return value;
}
function calendar(value: string): boolean {
  const date = new Date(`${value}T00:00:00.000Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}
export function timestamp(value: unknown): string {
  if (typeof value !== "string") invalid("时间必须包含时区和秒。");
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?(Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!match || !calendar(match[1]) || +match[2] > 23 || +match[3] > 59 || +match[4] > 59) invalid("时间无效。");
  if (match[6] !== "Z" && (+match[6].slice(1, 3) > 23 || +match[6].slice(4) > 59)) invalid();
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) invalid();
  const iso = date.toISOString();
  if (!/^\d{4}-/.test(iso)) invalid();
  return iso;
}
const diagnosisKeys = ["schemaVersion", "eventId", "diagnosisId", "botId", "engine", "sessionKey", "sessionId", "traceId",
  "occurredAt", "diagnosedAt", "decision", "tcFaultLabel", "confidence", "businessProblemCategory",
  "businessProblemSubtype", "systemDiagnosis", "businessDiagnosis", "handlerName", "humanIntervention"];
export function parseDiagnosis(input: unknown, key: string | undefined): DiagnosisEvent {
  const v = record(input, diagnosisKeys);
  const eventId = id(v.eventId);
  if (v.schemaVersion !== DIAGNOSIS_VERSION || id(v.diagnosisId) !== eventId || key !== eventId) invalid();
  const engine = oneOf(v.engine, ["OC", "TE"]);
  const decision = oneOf(v.decision, ["ALERT", "PASS", "UNRESOLVED"]);
  const confidence = v.confidence ?? null;
  if (confidence !== null && (typeof confidence !== "number" || !Number.isFinite(confidence) || confidence < 0 || confidence > 1)) invalid();
  if (decision !== "ALERT" && confidence !== null) invalid();
  const tcFaultLabel = nullableText(v.tcFaultLabel, 128);
  if (decision === "PASS" && tcFaultLabel !== null) invalid();
  const sessionKey = nullableText(v.sessionKey, 1024);
  const sessionId = nullableText(v.sessionId, 255);
  const traceId = nullableText(v.traceId, 255);
  if ([sessionKey, sessionId, traceId].some((s) => s !== null && !s.trim()) || (engine === "TE" && !traceId)) invalid();
  if (typeof v.humanIntervention !== "boolean") invalid();
  return {
    schemaVersion: DIAGNOSIS_VERSION, eventId, diagnosisId: eventId, botId: id(v.botId), engine,
    sessionKey, sessionId, traceId, occurredAt: v.occurredAt == null ? null : timestamp(v.occurredAt),
    diagnosedAt: timestamp(v.diagnosedAt), decision, tcFaultLabel, confidence,
    businessProblemCategory: nullableText(v.businessProblemCategory, 128),
    businessProblemSubtype: nullableText(v.businessProblemSubtype, 128),
    systemDiagnosis: nullableText(v.systemDiagnosis, 8000), businessDiagnosis: nullableText(v.businessDiagnosis, 8000),
    handlerName: nullableText(v.handlerName, 128), humanIntervention: v.humanIntervention,
  };
}
export function parseCheck(input: unknown, now: number): BotCheck {
  const v = record(input, ["schemaVersion", "botId", "engine", "checkedAt", "lastSuccessfulCheckAt", "status"]);
  if (v.schemaVersion !== CHECK_VERSION || !("lastSuccessfulCheckAt" in v)) invalid();
  const checkedAt = timestamp(v.checkedAt);
  const lastSuccessfulCheckAt = v.lastSuccessfulCheckAt === null ? null : timestamp(v.lastSuccessfulCheckAt);
  if (Date.parse(checkedAt) > now + 300_000 || (lastSuccessfulCheckAt !== null && Date.parse(lastSuccessfulCheckAt) > Date.parse(checkedAt))) invalid();
  return { schemaVersion: CHECK_VERSION, botId: id(v.botId), engine: oneOf(v.engine, ["OC", "TE"]),
    checkedAt, lastSuccessfulCheckAt, status: oneOf(v.status, ["HEALTHY", "ERROR", "UNKNOWN", "PAUSED"]) };
}
export function parseQuery(input: Record<string, unknown>): DiagnosisQuery {
  const v = record(input, ["startDate", "endDate", "decision", "keyword", "page", "pageSize", "businessProblemCategory", "businessProblemSubtype"]);
  if (Object.values(v).some((x) => typeof x !== "string")) invalid("查询参数不能重复或嵌套。");
  const date = (value: unknown): string | null => {
    if (value === undefined) return null;
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value) || !calendar(value)) invalid("日期无效。");
    return value;
  };
  const start = date(v.startDate), end = date(v.endDate);
  if (start && end && start > end) invalid("起始日期晚于结束日期。");
  const positive = (x: unknown, fallback: number): number => {
    if (x === undefined) return fallback;
    if (typeof x !== "string" || !/^[1-9]\d*$/.test(x) || +x > 2147483647) invalid("页码无效。");
    return +x;
  };
  const pageSize = positive(v.pageSize, 10);
  if (![10, 20, 50].includes(pageSize)) invalid();
  const keyword = ((v.keyword ?? "") as string).trim();
  if ([...keyword].length > 200) invalid("关键词过长。");
  const category = ((v.businessProblemCategory ?? "") as string).trim();
  const subtype = ((v.businessProblemSubtype ?? "") as string).trim();
  if ([category, subtype].some(value => [...value].length > 128)) invalid("业务问题类型过长。");
  if (subtype && !category) invalid("选择业务问题子类型前请先选择类型。");
  return { ...(category ? { businessProblemCategory: category } : {}), ...(subtype ? { businessProblemSubtype: subtype } : {}), startMs: start ? Date.parse(`${start}T00:00:00+08:00`) : null,
    endMs: end ? Date.parse(`${end}T00:00:00+08:00`) + 86400000 : null,
    decision: oneOf(v.decision ?? "ALL", ["ALL", "ALERT", "PASS", "UNRESOLVED"]),
    keyword, page: positive(v.page, 1), pageSize };
}
