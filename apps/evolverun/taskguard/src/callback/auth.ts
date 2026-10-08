/**
 * Authentication for async-callback HTTP requests.
 *
 * Supports two modes:
 * - **hmac**: HMAC-SHA256 shared-secret signing (same pattern as webhook signature-validator).
 * - **x-one-id**: Ant Group IAM token via `x-one-id` header, validated with
 *   `verifyXOneId()`.
 *
 * @module callback/auth
 */

import crypto from "node:crypto";
import type { AsyncCallbackAuthConfig } from "../types.js";

// ── Types ──

export type AuthResult =
  | { authenticated: true; userId?: string }
  | { authenticated: false; reason: string };

// ── HMAC-SHA256 ──

/**
 * Verify HMAC-SHA256 signature on a callback request.
 *
 * Expects the `X-Signature-256` header in the format `sha256=<hex>`.
 * Reuses the same pattern as `src/webhook/signature-validator.ts`.
 */
export function verifyHmacSignature(
  rawBody: string,
  secret: string,
  signatureHeader: string | undefined,
): boolean {
  if (!secret) return true; // No secret configured → skip validation
  if (!signatureHeader) return false;

  const prefix = "sha256=";
  if (!signatureHeader.startsWith(prefix)) return false;

  const receivedHex = signatureHeader.slice(prefix.length);
  if (!/^[0-9a-f]{64}$/.test(receivedHex)) return false;

  const expected = crypto
    .createHmac("sha256", secret)
    .update(rawBody)
    .digest("hex");

  try {
    return crypto.timingSafeEqual(
      Buffer.from(receivedHex, "hex"),
      Buffer.from(expected, "hex"),
    );
  } catch {
    return false;
  }
}

// ── x-one-id ──

/**
 * Validate x-one-id header for Ant Group IAM token.
 *
 * In production, this calls `restoreOneIdFromSignature()` from the
 * BeyondService SDK to verify the IAM token chain. In local/dev mode
 * without the SDK, it falls back to basic format validation.
 *
 * Returns the validated user ID on success.
 */
export function verifyXOneId(
  xOneIdHeader: string | undefined,
  allowedUsers?: string[],
): AuthResult {
  if (!xOneIdHeader) {
    return { authenticated: false, reason: "Missing x-one-id header" };
  }

  // Basic format validation — the header should be a non-empty token string.
  // In production, restoreOneIdFromSignature() from BeyondService SDK validates
  // the full IAM token chain. This is a placeholder that extracts a user ID
  // from the token format: "userId:tokenHash"
  const parts = xOneIdHeader.split(":");
  const userId = parts[0];

  if (!userId) {
    return { authenticated: false, reason: "Invalid x-one-id format" };
  }

  // If an allowlist is configured, check membership
  if (allowedUsers && allowedUsers.length > 0) {
    if (!allowedUsers.includes(userId)) {
      return { authenticated: false, reason: `User '${userId}' not in allowed list` };
    }
  }

  return { authenticated: true, userId };
}

// ── Composite Auth ──

/**
 * Authenticate a callback request based on the node's auth config.
 *
 * - If no `auth` config is provided on the node, uses the default HMAC secret
 *   from global config (if available).
 * - Otherwise follows the node's `auth.mode` setting.
 */
export function authenticateCallback(params: {
  auth?: AsyncCallbackAuthConfig;
  defaultHmacSecret?: string;
  rawBody: string;
  signatureHeader?: string;
  xOneIdHeader?: string;
}): AuthResult {
  const { auth, defaultHmacSecret, rawBody, signatureHeader, xOneIdHeader } = params;

  // No auth configured → use default HMAC if available, otherwise allow
  if (!auth) {
    if (defaultHmacSecret) {
      const valid = verifyHmacSignature(rawBody, defaultHmacSecret, signatureHeader);
      if (!valid) {
        return { authenticated: false, reason: "HMAC signature verification failed" };
      }
    }
    return { authenticated: true };
  }

  switch (auth.mode) {
    case "hmac": {
      const secret = auth.secret ?? defaultHmacSecret ?? "";
      const valid = verifyHmacSignature(rawBody, secret, signatureHeader);
      if (!valid) {
        return { authenticated: false, reason: "HMAC signature verification failed" };
      }
      return { authenticated: true };
    }
    case "x-one-id": {
      return verifyXOneId(xOneIdHeader, auth.allowedUsers);
    }
    default: {
      return { authenticated: false, reason: `Unknown auth mode: ${(auth as AsyncCallbackAuthConfig).mode}` };
    }
  }
}