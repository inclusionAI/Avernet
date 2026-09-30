const SECRET_KEY = /(?:authorization|cookie|token|secret|api[_-]?key|access[_-]?key|password|credential)/i;
const INLINE_SECRET = /(["']?(?:[A-Za-z0-9]+[_-])*(?:authorization|cookie|token|secret|api[_-]?key|access[_-]?(?:key|token)|password|credential)["']?)\s*[:=]\s*([^\s,;]+)/gi;
const CLI_SECRET = /--(?:authorization|cookie|token|secret|api[_-]?key|access[_-]?(?:key|token)|password|credential)(?:\s+|=)[^\s,;]+/gi;
const SPACE_SECRET_VALUE = /(?<![A-Za-z0-9_-])(?:(?:[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_)?(?:AUTHORIZATION|COOKIE|TOKEN|SECRET|API_KEY|ACCESS_KEY|ACCESS_TOKEN|PASSWORD|CREDENTIAL)|apiKey|accessKey|accessToken)\s+(?:sk-[A-Za-z0-9_-]{12,}|[A-Za-z0-9._~+/=-]{16,})\b/g;
const BEARER = /\bBearer\s+[A-Za-z0-9._~+/=-]+/gi;
const EMAIL = /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi;
const CN_PHONE = /(?<!\d)1[3-9]\d{9}(?!\d)/g;
const CN_ID = /(?<!\d)\d{17}[0-9Xx](?!\d)/g;
const BUSINESS_BODY_KEY = /^(?:prompt|prompts|content|contents|messages?|instruction|instructions|user[_-]?input|assistant[_-]?output|conversation|document|documents)$/i;
const INLINE_BUSINESS_BODY = /\b(prompt|content|instruction|input|output|user[_-]?input|assistant[_-]?output)\b\s*[:=]\s*(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\r\n]*)/gi;
const RAW_SECRET_LITERAL = /\b(?:sk-[A-Za-z0-9_-]{12,}|ce_repair_[A-Za-z0-9_-]{20,})\b/u;
const RAW_SECRET_LITERAL_GLOBAL = /\b(?:sk-[A-Za-z0-9_-]{12,}|ce_repair_[A-Za-z0-9_-]{20,})\b/gu;
const SIGNED_QUERY_SECRET = /[?&](?:x[-_]?amz[-_]?(?:credential|signature)|x[-_]?oss[-_]?signature|x[-_]?goog[-_]?signature|ossaccesskeyid|signature|sig)=(?:[^&#\s]+)/iu;
const SIGNED_QUERY_SECRET_GLOBAL = /([?&](?:x[-_]?amz[-_]?(?:credential|signature)|x[-_]?oss[-_]?signature|x[-_]?goog[-_]?signature|ossaccesskeyid|signature|sig)=)(?:[^&#\s]+)/giu;
const APPROVAL_SECRET_TEXT = /(?:\bBearer\s+\S+|--(?:authorization|cookie|token|secret|api[_-]?key|access[_-]?(?:key|token)|password|credential)(?:\s+|=)\S+|["']?(?:[A-Za-z0-9]+[_-])*(?:authorization|cookie|token|secret|auth[_-]?code|authorization[_-]?code|api[_-]?key|access[_-]?(?:key|token)|model[_-]?key|password|credential|(?:raw|execution|step|workload)[_-]?ticket)["']?\s*[:=]\s*["']?\S+|\b(?:sk-[A-Za-z0-9_-]{12,}|ce_repair_[A-Za-z0-9_-]{20,})\b)/iu;
const APPROVAL_SECRET_KEYS = new Set([
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
  "stepticket",
  "ticket",
  "token",
  "workloadticket",
]);

export const REDACTED_SECRET_TEXT = "[REDACTED_SECRET_TEXT]";

export function redactText(value: unknown, maxBytes = 32 * 1024): string {
  const input = String(value ?? "")
    .replace(BEARER, "Bearer [REDACTED]")
    .replace(CLI_SECRET, "[REDACTED]")
    .replace(SPACE_SECRET_VALUE, "[REDACTED]")
    .replace(INLINE_SECRET, (_match, key: string) => `${key}=[REDACTED]`)
    .replace(RAW_SECRET_LITERAL_GLOBAL, "[REDACTED]")
    .replace(SIGNED_QUERY_SECRET_GLOBAL, "$1[REDACTED]")
    .replace(EMAIL, "[REDACTED_EMAIL]")
    .replace(CN_PHONE, "[REDACTED_PHONE]")
    .replace(CN_ID, "[REDACTED_ID]")
    .replace(INLINE_BUSINESS_BODY, (_match, key: string) => `${key}=[REDACTED_BUSINESS_BODY]`);
  const encoded = Buffer.from(input, "utf8");
  if (encoded.byteLength <= maxBytes) return input;
  return `${encoded.subarray(0, maxBytes).toString("utf8")}\n[TRUNCATED]`;
}

export function redactPersistableText(value: unknown, maxBytes = 32 * 1024): string {
  const redacted = redactText(value, maxBytes)
    // The immutable ledger rejects authentication-shaped labels even after
    // their value has been removed. Replace only that assignment, preserving
    // the surrounding diagnostic text instead of discarding the whole entry.
    .replace(
      /["']?(?:[A-Za-z0-9]+[_-])*(?:authorization|cookie|token|secret|auth[_-]?code|authorization[_-]?code|api[_-]?key|access[_-]?(?:key|token)|model[_-]?key|password|credential|(?:raw|execution|step|workload)[_-]?ticket)["']?\s*[:=]\s*["']?\[REDACTED\]["']?/giu,
      REDACTED_SECRET_TEXT,
    )
    .replace(/\bBearer\s+\[REDACTED\]/giu, REDACTED_SECRET_TEXT)
    .replaceAll("[REDACTED]", REDACTED_SECRET_TEXT)
    .replace(/(?:\[REDACTED_SECRET_TEXT\]\s*){2,}/gu, REDACTED_SECRET_TEXT);
  return containsRepairSecret(redacted) || RAW_SECRET_LITERAL.test(redacted)
    ? REDACTED_SECRET_TEXT
    : redacted;
}

/**
 * Keeps non-sensitive diagnostic lines while replacing only lines that cannot
 * safely cross the immutable Repair ledger boundary. The repository remains
 * the final fail-closed guard; callers use this before persistence so one
 * credential-bearing line does not discard an otherwise useful tool result.
 */
export function redactPersistableLines(value: unknown, maxBytes = 32 * 1024): string {
  const redacted = String(value ?? "")
    .split("\n")
    .map((line) => redactPersistableText(line, maxBytes))
    .join("\n");
  const encoded = Buffer.from(redacted, "utf8");
  if (encoded.byteLength <= maxBytes) return redacted;
  return `${encoded.subarray(0, maxBytes).toString("utf8")}\n[TRUNCATED]`;
}

/** Executable Plan payloads are shown exactly to the owner, so reject secrets instead of mutating them. */
export function containsRepairSecret(value: unknown, seen = new Set<object>()): boolean {
  if (typeof value === "string") return APPROVAL_SECRET_TEXT.test(value) || SIGNED_QUERY_SECRET.test(value);
  if (value == null || typeof value !== "object") return false;
  if (seen.has(value)) return false;
  seen.add(value);
  if (Array.isArray(value)) {
    return value.some((item) => containsRepairSecret(item, seen));
  }
  return Object.entries(value as Record<string, unknown>).some(([key, child]) => {
    const normalized = key.toLowerCase().replace(/[^a-z0-9]/g, "");
    return [...APPROVAL_SECRET_KEYS].some((key) => normalized === key || normalized.endsWith(key))
      || containsRepairSecret(child, seen);
  });
}

export function redactValue(value: unknown, depth = 0): unknown {
  if (depth > 6) return "[TRUNCATED_DEPTH]";
  if (typeof value === "string") return redactText(value, 4 * 1024);
  if (Array.isArray(value)) return value.slice(0, 100).map((item) => redactValue(item, depth + 1));
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([key, child]) => [
    key,
    SECRET_KEY.test(key)
      ? "[REDACTED]"
      : BUSINESS_BODY_KEY.test(key)
        ? "[REDACTED_BUSINESS_BODY]"
        : redactValue(child, depth + 1),
  ]));
}
