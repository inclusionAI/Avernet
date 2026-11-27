import { redactText } from "../services/redaction.js";

const TRUNCATED = "...[truncated]";

function prefix(text: string, length: number): string {
  let end = Math.max(0, length);
  if (end < text.length && end > 0 && /[\uD800-\uDBFF]/.test(text[end - 1]) && /[\uDC00-\uDFFF]/.test(text[end])) end--;
  return text.slice(0, end);
}

/** Bounded diagnostic text, including nested/percent-encoded capability URLs.
 * Decode at most four bounded ASCII-percent layers; fail closed on remaining
 * encoded tokens. Never expose a partially sampled URL/credential token.
 * Safe text (including console format tokens and stack whitespace) is unchanged.
 */
export function redactLogText(text: string, maxChars = 16 * 1024): string {
  const limit = Number.isSafeInteger(maxChars) && maxChars >= 4 ? maxChars : 16 * 1024;
  const clipped = text.length > limit;
  let sample = prefix(text, limit);
  if (clipped) sample = sample.replace(/\S+$/, "");
  for (let pass = 0; pass < 4; pass++) {
    const decoded = sample.replace(/%([0-7][0-9a-f])/gi, (_match, hex: string) => String.fromCharCode(parseInt(hex, 16)));
    if (decoded === sample) break;
    sample = decoded;
  }
  // Ambiguous/deeper encoding is not useful enough to justify leaking a token.
  sample = sample.replace(/\S*%[0-9a-f]{2}\S*/gi, "[REDACTED_ENCODED_TEXT]")
    .replace(/([/\\]evidence-access[/\\])[^?#\s"'<>]+/gi, "$1[REDACTED]");
  sample = redactText(sample, limit * 4);
  if (clipped || sample.length > limit) {
    const suffix = limit >= TRUNCATED.length ? TRUNCATED : "";
    sample = prefix(sample, limit - suffix.length) + suffix;
  }
  return sample;
}
