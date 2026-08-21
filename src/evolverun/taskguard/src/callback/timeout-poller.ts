/**
 * Callback timeout poller — periodically scans for expired pending tokens
 * and marks the corresponding workflow nodes as failed (timeout).
 *
 * Follows the same pattern as `src/card/approval-card-web-poller.ts`.
 *
 * @module callback/timeout-poller
 */

import type { ControllerDeps } from "../controller.js";
import type { IDatabase } from "../db/types.js";
import type { AsyncCallbackConfig } from "../config/types.js";
import { createCallbackTokenRegistry, type CallbackTokenRecord } from "./token-registry.js";

// ── State ──

let pollTimer: ReturnType<typeof setInterval> | null = null;
const DEFAULT_POLL_INTERVAL_MS = 60_000;

/** Cache deps from the last command dispatch for use by the poll loop. */
let _latestDeps: ControllerDeps | null = null;

// ── Public API ──

/**
 * Capture ControllerDeps for the poll loop.
 * Called from the command dispatch path (same pattern as approval-card-web-poller).
 */
export function captureCallbackPollerDeps(deps: ControllerDeps): void {
  _latestDeps = deps;
}

/**
 * Start the timeout poller.
 * Safe to call multiple times — won't create duplicate timers.
 */
export function startCallbackTimeoutPoller(
  database: IDatabase,
  config: AsyncCallbackConfig,
): void {
  if (pollTimer) return; // Already running
  if (!config.enabled) return;

  const intervalMs = config.timeoutPollIntervalMs || DEFAULT_POLL_INTERVAL_MS;
  console.log(`[callback-timeout-poller] Starting with interval ${intervalMs}ms`);

  pollTimer = setInterval(() => {
    pollExpiredTokens(database, config).catch((err) => {
      console.error("[callback-timeout-poller] Error during poll:", err);
    });
  }, intervalMs);

  // Don't prevent process exit
  if (pollTimer.unref) {
    pollTimer.unref();
  }
}

/**
 * Stop the timeout poller.
 */
export function stopCallbackTimeoutPoller(): void {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
    console.log("[callback-timeout-poller] Stopped");
  }
}

// ── Internal ──

async function pollExpiredTokens(
  database: IDatabase,
  config: AsyncCallbackConfig,
): Promise<void> {
  if (!_latestDeps) return;

  const registry = createCallbackTokenRegistry(database);

  // Find expired pending tokens
  const expired = await registry.findExpiredPending();
  if (expired.length === 0) return;

  console.log(`[callback-timeout-poller] Found ${expired.length} expired token(s)`);

  // Process each expired token
  for (const record of expired) {
    try {
      await processExpiredToken(record, registry, config);
    } catch (err) {
      console.error(
        `[callback-timeout-poller] Error processing expired token ${record.token}:`,
        err,
      );
    }
  }

  // Cleanup old consumed/expired tokens
  try {
    const retentionDays = config.tokenRetentionDays ?? 7;
    const deleted = await registry.deleteOlderThan(retentionDays);
    if (deleted > 0) {
      console.log(`[callback-timeout-poller] Cleaned up ${deleted} old token(s)`);
    }
  } catch (err) {
    console.error("[callback-timeout-poller] Error cleaning up old tokens:", err);
  }
}

async function processExpiredToken(
  record: CallbackTokenRecord,
  registry: ReturnType<typeof createCallbackTokenRegistry>,
  _config: AsyncCallbackConfig,
): Promise<void> {
  // Mark token as expired in DB
  const expired = await registry.expire(record.token);
  if (!expired) {
    // Token was already consumed between the query and now — skip
    return;
  }

  console.log(
    `[callback-timeout-poller] Token ${record.token} expired (flowId=${record.flowId}, nodeId=${record.nodeId})`,
  );

  // If we have controller deps, notify the workflow that the node timed out
  if (!_latestDeps) return;

  try {
    // Import lazily to avoid circular dependency
    const { handleAsyncCallback } = await import("../controller.js");

    // Call the controller with a failed result indicating timeout
    await handleAsyncCallback(
      _latestDeps,
      record.flowId,
      record.nodeId,
      record.token,
      { status: "failed", error: "回调超时: 未在规定时间内收到外部回调" },
      undefined,
    );
  } catch (err) {
    // The node may have already been resolved by another path
    const message = err instanceof Error ? err.message : "Unknown error";
    console.warn(
      `[callback-timeout-poller] Could not notify flow for expired token ${record.token}: ${message}`,
    );
  }
}