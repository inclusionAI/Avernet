import { redactLogText } from "./redact-log-text.js";

type ValueOptions = {
  maxChars?: number;
  maxDepth?: number;
  maxEntries?: number;
  redactKeys?: RegExp;
};

const SENSITIVE_KEY = /(?:authorization|cookie|token|secret|api[_-]?key|access[_-]?key|password|credential|signature|^sig$)/i;
const BUSINESS_KEY = /^(?:prompt|prompts|content|contents|messages?|instruction|instructions|user[_-]?input|assistant[_-]?output|conversation|documents?)$/i;
const TRUNCATED = "...[truncated]";

function limit(value: number | undefined, fallback: number, minimum: number): number {
  return value !== undefined && Number.isSafeInteger(value) && value >= minimum ? value : fallback;
}

/** Find a prefix whose JSON-escaped contents fit, without serializing the input tree. */
function jsonPrefix(text: string, available: number): string {
  let index = 0;
  let used = 0;
  while (index < text.length) {
    const code = text.charCodeAt(index);
    let width = 1;
    let cost = code < 32 ? (code === 8 || code === 9 || code === 10 || code === 12 || code === 13 ? 2 : 6)
      : code === 34 || code === 92 ? 2 : 1;
    if (code >= 0xd800 && code <= 0xdbff && text.charCodeAt(index + 1) >= 0xdc00 && text.charCodeAt(index + 1) <= 0xdfff) {
      width = 2;
      cost = 2;
    } else if (code >= 0xd800 && code <= 0xdfff) cost = 6;
    if (used + cost > available) break;
    used += cost;
    index += width;
  }
  return text.slice(0, index);
}

/**
 * Produces a JSON-safe, redacted tree, not a full clone followed by truncation.
 * maxChars bounds the final JSON (minimum 4), maxEntries is a GLOBAL visited-key
 * budget, and depth is capped at 64 to protect the stack. Accessors/toJSON are
 * never invoked; bigint/functions/symbols become markers. Keys over 256 chars
 * terminate that object's preview without reading their value. Returned objects
 * have null prototypes, including when the source has an own __proto__ key.
 */
export function boundedLogValue(value: unknown, options: ValueOptions = {}): unknown {
  const maxChars = limit(options.maxChars, 16 * 1024, 4);
  const maxDepth = Math.min(limit(options.maxDepth, 6, 0), 64);
  let entriesLeft = limit(options.maxEntries, 100, 0);
  let remaining = maxChars;
  const ancestors = new WeakSet<object>();
  // Do not mutate lastIndex on a caller's global/sticky expression.
  const extraRedaction = options.redactKeys
    ? new RegExp(options.redactKeys.source, options.redactKeys.flags.replace(/[gy]/g, "")) : undefined;

  function text(input: string, redact = false): string {
    let sample = redact ? redactLogText(input, maxChars) : input.slice(0, maxChars);
    const clipped = input.length > maxChars;
    const encoded = JSON.stringify(sample);
    let result = sample;
    if (clipped || encoded.length > remaining) {
      const suffix = remaining >= TRUNCATED.length + 2 ? TRUNCATED : "";
      result = jsonPrefix(sample, remaining - suffix.length - 2) + suffix;
    }
    remaining -= JSON.stringify(result).length;
    return result;
  }

  function primitive(input: null | boolean | number): unknown {
    const encoded = JSON.stringify(input);
    if (encoded.length > remaining) {
      remaining -= 4;
      return null;
    }
    remaining -= encoded.length;
    return input;
  }

  function visit(input: unknown, depth: number): unknown {
    if (input === null) return primitive(null);
    if (typeof input === "string") return text(input, true);
    if (typeof input === "boolean" || typeof input === "number") return primitive(input);
    if (typeof input !== "object") return text(`[${typeof input}]`);
    if (depth >= maxDepth) return text("[TRUNCATED_DEPTH]");
    if (ancestors.has(input)) return text("[Circular]");
    ancestors.add(input);
    try {
      const array = Array.isArray(input);
      const output: unknown[] | Record<string, unknown> = array ? [] : Object.create(null);
      remaining -= 2; // Reserve the container's closing delimiter up front.
      let count = 0;
      if (array) {
        const length = Object.getOwnPropertyDescriptor(input, "length")?.value;
        for (let index = 0; index < length && entriesLeft > 0 && remaining >= 5; index += 1) {
          entriesLeft -= 1;
          remaining -= count > 0 ? 1 : 0;
          const descriptor = Object.getOwnPropertyDescriptor(input, String(index));
          (output as unknown[]).push(descriptor && "value" in descriptor
            ? visit(descriptor.value, depth + 1) : text(descriptor ? "[Getter]" : "[empty]"));
          count += 1;
        }
      } else {
        // Stop enumerating after the shared budget, never Object.entries/map the body.
        for (const key in input) {
          if (entriesLeft <= 0 || remaining < 8 || key.length > 256) break;
          entriesLeft -= 1;
          const descriptor = Object.getOwnPropertyDescriptor(input, key);
          if (!descriptor?.enumerable) continue;
          const safeKey = redactLogText(key, 256);
          const overhead = JSON.stringify(safeKey).length + 1 + (count > 0 ? 1 : 0);
          if (remaining < overhead + 4) break;
          remaining -= overhead;
          // Redact using the original key BEFORE touching the child or truncating.
          const child = SENSITIVE_KEY.test(key) || extraRedaction?.test(key) ? text("[REDACTED]")
            : BUSINESS_KEY.test(key) ? text("[REDACTED_BUSINESS_BODY]")
              : "value" in descriptor ? visit(descriptor.value, depth + 1) : text("[Getter]");
          (output as Record<string, unknown>)[safeKey] = child;
          count += 1;
        }
      }
      return output;
    } catch {
      // A hostile/revoked Proxy may throw from enumeration or descriptor traps.
      // Discard partial work but keep its spent budget charged.
      return text("[Unserializable]");
    } finally {
      ancestors.delete(input);
    }
  }

  return visit(value, 0);
}
