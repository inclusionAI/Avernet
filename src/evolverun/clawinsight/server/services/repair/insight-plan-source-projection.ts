import { digestJson } from "../evolve/plan-source-contract.js";
import { assertRepairAuditSecretFree } from "../../repositories/repair-repository.js";
import { REDACTED_SECRET_TEXT } from "./redaction.js";

const MAX_PROJECTION_DEPTH = 64;
const REDACTED_FIELD_COUNT_KEY = "__repair_redacted_auth_field_count";
const UNAVAILABLE_ERROR = "Insight 可选证据无法生成安全投影，本次 Repair 将继续但不使用该数据源。";

const AUTH_FIELD_NAMES = new Set([
  "apikey",
  "accesskey",
  "accesstoken",
  "authcode",
  "authorization",
  "authorizationcode",
  "bearertoken",
  "cfuseauthcode",
  "cookie",
  "cookies",
  "credential",
  "executionticket",
  "llmapikey",
  "modelapikey",
  "modelkey",
  "password",
  "proxyauthorization",
  "rawticket",
  "secret",
  "setcookie",
  "stepticket",
  "ticket",
  "token",
  "workloadticket",
]);

const AUTH_HEADER = /\b(?:authorization|proxy-authorization|cookie|set-cookie)\s*:\s*[^\r\n]*/giu;
const AUTH_ASSIGNMENT = /(^|[\s,{;])["']?(?:authorization|proxy[_-]?authorization|cookie|set[_-]?cookie|token|ctoken|refresh[_-]?token|secret|auth[_-]?code|authorization[_-]?code|api[_-]?key|access[_-]?(?:key|token)|model[_-]?key|password|credential|(?:raw|execution|step|workload)[_-]?ticket)["']?\s*[:=]\s*(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;]+)/gimu;
const AUTH_CLI_ARGUMENT = /--(?:authorization|cookie|token|secret|auth[_-]?code|api[_-]?key|access[_-]?(?:key|token)|model[_-]?key|password|credential|(?:raw|execution|step|workload)[_-]?ticket)(?:\s+|=)[^\s,;]+/giu;
const AUTH_SPACE_VALUE = /(?<![A-Za-z0-9_-])(?:(?:[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_)?(?:AUTHORIZATION|COOKIE|TOKEN|SECRET|API_KEY|ACCESS_KEY|ACCESS_TOKEN|PASSWORD|CREDENTIAL)|apiKey|accessKey|accessToken)\s+(?:sk-[A-Za-z0-9_-]{12,}|[A-Za-z0-9._~+/=-]{16,})\b/gu;
const BEARER_VALUE = /\bBearer\s+[A-Za-z0-9._~+/=-]+/giu;
const RAW_SECRET_LITERAL = /\b(?:sk-[A-Za-z0-9_-]{12,}|ce_repair_[A-Za-z0-9_-]{20,})\b/gu;
const SIGNED_QUERY_VALUE = /([?&](?:x[-_]?amz[-_]?(?:credential|signature)|x[-_]?oss[-_]?signature|x[-_]?goog[-_]?signature|ossaccesskeyid|signature|sig)=)(?:[^&#\s]+)/giu;

type ProjectionResult = {
  value: unknown;
  redactionCount: number;
};

function normalizedKey(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/gu, "");
}

function isAuthenticationField(key: string): boolean {
  const normalized = normalizedKey(key);
  return [...AUTH_FIELD_NAMES].some((name) => normalized === name || normalized.endsWith(name));
}

function redactAuthenticationText(value: string): ProjectionResult {
  let redactionCount = 0;
  const redact = () => {
    redactionCount += 1;
    return REDACTED_SECRET_TEXT;
  };
  const projected = value
    .replace(AUTH_HEADER, redact)
    .replace(AUTH_CLI_ARGUMENT, redact)
    .replace(AUTH_ASSIGNMENT, (_match, prefix: string) => `${prefix}${redact()}`)
    .replace(AUTH_SPACE_VALUE, redact)
    .replace(BEARER_VALUE, redact)
    .replace(RAW_SECRET_LITERAL, redact)
    .replace(SIGNED_QUERY_VALUE, (_match, prefix: string) => {
      redactionCount += 1;
      return `${prefix}${REDACTED_SECRET_TEXT}`;
    });
  return { value: projected, redactionCount };
}

function projectValue(value: unknown, seen: Set<object>, depth: number): ProjectionResult {
  if (depth > MAX_PROJECTION_DEPTH) throw new Error("Insight PlanSource exceeds projection depth");
  if (value == null || typeof value === "boolean") return { value, redactionCount: 0 };
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("Insight PlanSource contains a non-finite number");
    return { value, redactionCount: 0 };
  }
  if (typeof value === "string") return redactAuthenticationText(value);
  if (typeof value !== "object") throw new Error("Insight PlanSource is not JSON serializable");
  if (seen.has(value)) throw new Error("Insight PlanSource contains a circular reference");
  const prototype = Object.getPrototypeOf(value);
  if (!Array.isArray(value) && prototype !== Object.prototype && prototype !== null) {
    throw new Error("Insight PlanSource contains a non-JSON object");
  }
  seen.add(value);
  try {
    if (Array.isArray(value)) {
      const projected: unknown[] = [];
      let redactionCount = 0;
      for (const item of value) {
        const child = item === undefined
          ? { value: null, redactionCount: 0 }
          : projectValue(item, seen, depth + 1);
        projected.push(child.value);
        redactionCount += child.redactionCount;
      }
      return { value: projected, redactionCount };
    }

    const projected: Record<string, unknown> = {};
    let redactionCount = 0;
    let redactedFieldCount = 0;
    for (const [key, childValue] of Object.entries(value as Record<string, unknown>)) {
      if (childValue === undefined) continue;
      if (isAuthenticationField(key)) {
        redactedFieldCount += 1;
        redactionCount += 1;
        continue;
      }
      const child = projectValue(childValue, seen, depth + 1);
      projected[key] = child.value;
      redactionCount += child.redactionCount;
    }
    if (redactedFieldCount > 0) {
      let metadataKey = REDACTED_FIELD_COUNT_KEY;
      while (Object.hasOwn(projected, metadataKey)) metadataKey = `_${metadataKey}`;
      projected[metadataKey] = redactedFieldCount;
    }
    return { value: projected, redactionCount };
  } finally {
    seen.delete(value);
  }
}

function refreshProjectedDescriptorDigest(value: unknown, redactionCount: number): unknown {
  if (redactionCount === 0 || !value || typeof value !== "object" || Array.isArray(value)) return value;
  const descriptor = value as Record<string, unknown>;
  const delivery = descriptor.delivery;
  if (!delivery || typeof delivery !== "object" || Array.isArray(delivery)) return value;
  const content = (delivery as Record<string, unknown>).content;
  if (content == null || typeof descriptor.digest !== "string") return value;
  return { ...descriptor, digest: digestJson(content) };
}

export function unavailableInsightPlanSource(reason: "source_unavailable" | "unsafe_projection"): Record<string, unknown> {
  return {
    status: "unavailable",
    reason,
    error: UNAVAILABLE_ERROR,
  };
}

/**
 * Produces the Repair-only, secret-safe projection of optional Insight input.
 * It never weakens the repository guard: the projected value is checked with
 * the exact invariant used by immutable audit writes before it is returned.
 */
export function projectInsightPlanSourceForRepair(value: unknown): unknown {
  try {
    const projected = projectValue(value, new Set<object>(), 0);
    const descriptor = refreshProjectedDescriptorDigest(projected.value, projected.redactionCount);
    assertRepairAuditSecretFree(descriptor, "insightPlanSource");
    return descriptor;
  } catch {
    return unavailableInsightPlanSource("unsafe_projection");
  }
}
