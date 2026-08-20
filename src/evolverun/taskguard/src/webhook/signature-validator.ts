/**
 * HMAC-SHA256 signature validation for webhook requests.
 *
 * Each trigger can optionally configure a `secret`. When configured,
 * requests must include an `X-Signature-256: sha256=<hex>` header.
 * Uses `crypto.timingSafeEqual` to prevent timing attacks.
 */
import crypto from "node:crypto";

/**
 * Verify the HMAC-SHA256 signature of a webhook request.
 *
 * @param rawBody - The raw request body string (before JSON parsing)
 * @param secret - The trigger's secret. If null/empty, validation is skipped (returns true)
 * @param signatureHeader - The value of the `X-Signature-256` header
 * @returns true if the signature is valid or validation is skipped
 */
export function verifySignature(
  rawBody: string,
  secret: string | null | undefined,
  signatureHeader: string | undefined,
): boolean {
  // No secret configured — skip signature validation
  if (!secret) return true;

  // Secret is configured but header is missing
  if (!signatureHeader) return false;

  // Validate header format: must be "sha256=<64-char-hex>"
  const match = signatureHeader.match(/^sha256=([0-9a-f]{64})$/);
  if (!match) return false;

  // Compute expected HMAC
  const expected = crypto.createHmac("sha256", secret).update(rawBody).digest("hex");

  // Timing-safe comparison
  try {
    return crypto.timingSafeEqual(
      Buffer.from(expected, "hex"),
      Buffer.from(match[1], "hex"),
    );
  } catch {
    return false;
  }
}