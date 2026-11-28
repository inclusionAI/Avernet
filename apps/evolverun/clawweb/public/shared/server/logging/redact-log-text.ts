import { redactText } from "../services/redaction.js";

const TRUNCATED = "...[truncated]";
const ENCODED = "[REDACTED_ENCODED_TEXT]";
const MAX_SAMPLE = 16 * 1024;

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
export function redactLogText(text: string, maxChars = MAX_SAMPLE): string {
  const limit = Number.isSafeInteger(maxChars) && maxChars >= 4 ? Math.min(maxChars, MAX_SAMPLE) : MAX_SAMPLE;
  const clipped = text.length > limit;
  let sample = prefix(text, limit);
  if (clipped) {
    let end = sample.length;
    while (end > 0 && !/\s/.test(sample[end - 1])) end--;
    sample = sample.slice(0, end);
  }
  for (let pass = 0; pass < 4; pass++) {
    // Decoding a delimiter could expose the suffix of a single opaque value.
    let ambiguous = false;
    const decoded = sample.replace(/%([0-7][0-9a-f])/gi, (_match, hex: string) => {
      const char = String.fromCharCode(parseInt(hex, 16));
      if (/[\x00-\x20\x7f"'<>?#&,;]/.test(char)) ambiguous = true;
      return char;
    });
    if (ambiguous) return prefix(ENCODED, limit);
    if (decoded === sample) break;
    sample = decoded;
  }
  // Ambiguous/deeper encoding is not useful enough to justify leaking a token.
  if (/%[0-9a-f]{2}/i.test(sample)) return prefix(ENCODED, limit);
  sample = sample.replace(/([/\\]evidence-access[/\\])[^?#\s"'<>]+/gi, "$1[REDACTED]");
  sample = redactText(sample, limit * 4);
  if (clipped || sample.length > limit) {
    const suffix = limit >= TRUNCATED.length ? TRUNCATED : "";
    sample = prefix(sample, limit - suffix.length) + suffix;
  }
  return sample;
}
