/**
 * Payload validation for async-callback HTTP requests.
 *
 * Validates the incoming JSON payload structure: enforces size limits,
 * requires a top-level object, and checks for required fields.
 *
 * @module callback/payload-validator
 */

export type PayloadValidationResult =
  | { valid: true; data: Record<string, unknown> }
  | { valid: false; reason: string };

/** Default maximum payload size in KB. */
const DEFAULT_MAX_PAYLOAD_KB = 256;

/**
 * Validate a raw callback payload.
 *
 * Checks:
 * 1. Body is not empty
 * 2. Body is valid JSON
 * 3. Parsed JSON is a top-level object (not array/primitive)
 * 4. Body size is within limits
 */
export function validateCallbackPayload(params: {
  rawBody: string;
  maxPayloadKb?: number;
}): PayloadValidationResult {
  const { rawBody, maxPayloadKb = DEFAULT_MAX_PAYLOAD_KB } = params;

  // Check body is not empty
  if (!rawBody || rawBody.trim().length === 0) {
    return { valid: false, reason: "Request body is empty" };
  }

  // Check size limit
  const bodyBytes = Buffer.byteLength(rawBody, "utf-8");
  const maxBytes = maxPayloadKb * 1024;
  if (bodyBytes > maxBytes) {
    return {
      valid: false,
      reason: `Payload exceeds size limit: ${Math.round(bodyBytes / 1024)}KB > ${maxPayloadKb}KB`,
    };
  }

  // Parse JSON
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawBody);
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown parse error";
    return { valid: false, reason: `Invalid JSON: ${message}` };
  }

  // Must be a top-level object
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return { valid: false, reason: "Payload must be a JSON object (not array or primitive)" };
  }

  return { valid: true, data: parsed as Record<string, unknown> };
}

/**
 * Build the result object that gets passed to the controller.
 *
 * The callback payload should contain a `status` field ("succeeded" or "failed")
 * and an optional `result` object. If `status` is missing, defaults to "succeeded".
 */
export function buildCallbackResult(payload: Record<string, unknown>): {
  status: "succeeded" | "failed";
  result: Record<string, unknown>;
} {
  const rawStatus = payload.status;
  const status: "succeeded" | "failed" =
    rawStatus === "failed" ? "failed" : "succeeded";

  // Separate meta fields from the result data
  const { status: _s, ...resultData } = payload;
  return { status, result: resultData };
}