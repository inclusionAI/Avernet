/**
 * HTTP Callback HMAC-SHA256 Signing
 *
 * Signs callback payloads so receivers can verify message integrity
 * and authenticity. The signature covers `timestamp.body` to bind
 * the timestamp to the payload, preventing signature stripping and
 * replay attacks when the receiver checks timestamp freshness.
 *
 * This module has zero dependencies beyond Node.js crypto — it can
 * be used by both the ClawMind engine (sender) and external receivers.
 */
import { createHmac } from "node:crypto";

/**
 * Generate an HMAC-SHA256 signature for a callback payload.
 *
 * The signed content is `${timestamp}.${body}`, matching the convention
 * used by Stripe, GitHub, and other webhook platforms.
 *
 * @param secret - The signing secret (from HttpCallbackConfig.secret)
 * @param timestamp - Unix timestamp in milliseconds (as a string)
 * @param body - The raw JSON request body string
 * @returns Hex-encoded HMAC digest
 */
export function signPayload(secret: string, timestamp: string, body: string): string {
  return createHmac("sha256", secret)
    .update(`${timestamp}.${body}`)
    .digest("hex");
}

/**
 * Verify an HMAC-SHA256 signature for a callback payload.
 *
 * Uses timing-safe comparison to prevent timing attacks.
 *
 * @param secret - The signing secret
 * @param timestamp - Unix timestamp string from X-Callback-Timestamp header
 * @param body - The raw request body
 * @param signature - The signature from X-Callback-Signature-256 header (without "sha256=" prefix)
 * @returns true if the signature is valid
 */
export function verifySignature(
  secret: string,
  timestamp: string,
  body: string,
  signature: string,
): boolean {
  const expected = signPayload(secret, timestamp, body);
  // Timing-safe comparison: both values are hex strings of equal length
  // when derived from the same hash algorithm.
  if (expected.length !== signature.length) {
    return false;
  }
  const bufA = Buffer.from(expected);
  const bufB = Buffer.from(signature);
  return bufA.equals(bufB);
}

/**
 * Check whether a timestamp is within the acceptable tolerance window.
 *
 * Receiver-side utility: call this before verifySignature to reject
 * replayed requests with stale timestamps.
 *
 * @param timestamp - Unix timestamp string from X-Callback-Timestamp header
 * @param toleranceMs - Maximum age in milliseconds (default: 5 minutes)
 * @returns true if the timestamp is within tolerance
 */
export function isTimestampFresh(timestamp: string, toleranceMs: number = 5 * 60 * 1000): boolean {
  const ts = parseInt(timestamp, 10);
  if (Number.isNaN(ts)) return false;
  const now = Date.now();
  return Math.abs(now - ts) <= toleranceMs;
}