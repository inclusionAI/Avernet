/**
 * FlowControlDispatcher — polls for available slots and dispatches queued items.
 *
 * Simplified: only perWorkflow scope. No global, no perExecutor.
 *
 * Responsibilities per tick:
 * 1. Cleanup: expire stale queue entries, release orphaned slots, release expired leases, delete old processed entries
 * 2. Dispatch: for each scope with queued items, acquire slots and invoke resume callbacks
 * 3. Orphan recovery: detect waiting flows with no queue entry and resume them
 * 4. (Loop): run tick() on a setInterval when started
 *
 * Key invariant: Flow control NEVER modifies flow_runs.status.
 */
import type { IFlowControlRepository } from "../db/repositories/types.js";
import type { FlowControlConfig } from "./types.js";
import { getMaxConcurrentForScope, getQueueTimeoutMsForScope } from "./config.js";
import { resolveSessionId } from "../session-resolver.js";

/**
 * Extract sessionId from a queued item's payload.
 * The payload is a JSON string that may contain a sessionKey field.
 * We parse it and resolve the sessionId from the sessionKey.
 */
function extractSessionIdFromPayload(payload: string | null): string | null {
  if (!payload) return null;
  try {
    const parsed = JSON.parse(payload);
    if (parsed.sessionKey && typeof parsed.sessionKey === "string") {
      return resolveSessionId(parsed.sessionKey);
    }
  } catch { /* payload is not JSON */ }
  return null;
}

/** Dispatch callbacks — provided by the caller to resume workflow execution. */
export interface DispatcherCallbacks {
  /** Resume a queued workflow. Payload may contain sessionKey for restoring the correct TaskFlow session. */
  onWorkflowResume: (flowId: string, payload: string | null) => Promise<void>;
  /**
   * A queued item has expired. The dispatcher has already cleaned up the queue entry
   * and released the slot. The callback should NOT modify flow_runs.status —
   * it should only perform resource cleanup (e.g., re-enqueue for retry).
   * Flow state transitions are the exclusive responsibility of the Controller.
   */
  onExpired?: (flowId: string, nodeId: string | null, scopeKey: string, payload: string | null) => void;
  /**
   * Find "orphaned" waiting flows — flows stuck in "waiting" state for
   * flow-control reasons but with no corresponding queue entry.
   * Called during each tick to recover flows that fell through the cracks
   * due to API failures during enqueue.
   * Returns an array of flow info including optional payload (e.g. sessionKey)
   * so the dispatcher can re-enqueue with the correct context.
   */
  findOrphanedWaitingFlows?: () => Promise<Array<{ flowId: string; workflowId: string; payload?: string | null }>>;
}

/**
 * Flow control dispatcher.
 * A single loop managing all scopes for the current instance.
 * Responsible for cleaning up expired entries, orphaned slots,
 * and dispatching queued requests.
 */
export class FlowControlDispatcher {
  private intervalHandle: ReturnType<typeof setInterval> | null = null;
  private running = false;

  /**
   * Cooldown map for orphan recovery: tracks recently enqueued flow IDs
   * to prevent TOCTOU duplicate enqueues between findOrphanedWaitingFlows
   * and the actual enqueue call. A flow enqueued within the last
   * ORPHAN_REENQUEUE_COOLDOWN_MS is skipped by orphan recovery.
   */
  private readonly recentlyEnqueuedFlows = new Map<string, number>();

  /** Cooldown period: skip orphan recovery for flows enqueued within this window. */
  private static readonly ORPHAN_REENQUEUE_COOLDOWN_MS = 30_000; // 30 seconds

  constructor(
    private repo: IFlowControlRepository,
    private config: FlowControlConfig,
    private instanceId: string,
    private callbacks: DispatcherCallbacks,
  ) {}

  /** Start the dispatcher loop. */
  start(): void {
    if (this.intervalHandle) return;
    const pollIntervalMs = this.config.dispatcher?.pollIntervalMs ?? 1000;
    this.intervalHandle = setInterval(() => {
      this.tick().catch((err) => {
        console.error("[flow-control] dispatcher tick error:", err);
      });
    }, pollIntervalMs);
    console.log(
      `[flow-control] dispatcher started (interval: ${pollIntervalMs}ms, instance: ${this.instanceId})`,
    );
  }

  /** Stop the dispatcher loop. */
  stop(): void {
    if (this.intervalHandle) {
      clearInterval(this.intervalHandle);
      this.intervalHandle = null;
    }
    // Clear cooldown state to avoid stale entries if dispatcher is restarted
    this.recentlyEnqueuedFlows.clear();
    console.log("[flow-control] dispatcher stopped");
  }

  /** Whether the dispatcher loop is currently running. */
  isRunning(): boolean {
    return this.running;
  }

  /** Manually trigger one tick (for testing). */
  async tick(): Promise<void> {
    if (this.running) return;
    this.running = true;
    const tickStart = Date.now();
    try {
      await this.tickInternal();
      const elapsed = Date.now() - tickStart;
      if (elapsed > 30_000) {
        console.warn(`[flow-control] tick took ${(elapsed / 1000).toFixed(1)}s (over 30s threshold) — investigate slow operations`);
      }
    } catch (err) {
      console.error("[flow-control] dispatcher tick error:", err);
    } finally {
      this.running = false;
    }
  }

  /** Internal tick logic. */
  private async tickInternal(): Promise<void> {
    // 1. Expire stale queue entries — fetch first for event emission
    const expiringItems = await this.repo.fetchExpiringItems(this.instanceId);
    if (expiringItems.length > 0) {
      const expiredCount = await this.repo.expireStaleEntries(this.instanceId);
      if (expiredCount > 0) {
        console.log(`[flow-control] EXPIRED ${expiredCount} stale queue entries: flows=[${expiringItems.map(i => i.flow_id).join(",")}]`);
        for (const item of expiringItems) {
          if (this.callbacks.onExpired) {
            this.callbacks.onExpired(item.flow_id, item.node_id, item.scope_key, item.payload);
          }
        }
      }
    }

    // 2. Release orphaned slots (flow has completed but slot was never released)
    const orphanCount = await this.repo.releaseOrphanedSlots(this.instanceId);
    if (orphanCount > 0) {
      console.log(`[flow-control] released ${orphanCount} orphaned slots`);
    }

    // 2b. Expired lease cleanup: release slots whose lease has expired.
    //     The LeaseManager renews leases for this instance every 30s.
    //     If a process crashes or partitions, its lease expires after LEASE_TTL_SECS (60s),
    //     and this cleanup on the next surviving instance's tick releases the slot.
    //     IMPORTANT: Only slots are deleted — queue entries are preserved so the
    //     dispatcher can re-dispatch them when capacity becomes available.
    //     Flow control manages resources (concurrency slots), not flow state.
    try {
      const expiredResult = await this.repo.releaseExpiredLeases(this.instanceId);
      if (expiredResult.releasedSlots > 0) {
        console.warn(
          `[flow-control] EXPIRED LEASE CLEANUP: released ${expiredResult.releasedSlots} expired slot(s) ` +
          `(queue entries preserved for re-dispatch)`,
        );
      }
    } catch (err) {
      console.error("[flow-control] expired lease cleanup failed:", err);
    }

    // NOTE: The old releaseStaleOrphanedSlots was previously REMOVED because it killed
    // active flow slots when gmt_modified hadn't been updated during long LLM calls
    // (5-15 min). However, legacy slots (lease_expires_at=0) created before the lease
    // feature was added are never cleaned up by releaseExpiredLeases (which skips
    // lease_expires_at=0). We re-add releaseStaleOrphanedSlots with a **1-hour**
    // threshold, which is safe because:
    // - LLM calls rarely take >1 hour (if they do, the flow has bigger problems)
    // - The lease mechanism (releaseExpiredLeases above) handles modern slots
    // - releaseStaleOrphanedSlots only targets flows still in active status where
    //   gmt_modified is stale — truly active flows update gmt_modified regularly
    // - releaseOrphanedSlots (step 2 above) handles flows that have already completed
    try {
      const LEGACY_STALE_THRESHOLD_SECS = 3600; // 1 hour — safe threshold for legacy slots
      const staleResult = await this.repo.releaseStaleOrphanedSlots(this.instanceId, LEGACY_STALE_THRESHOLD_SECS);
      if (staleResult.releasedSlots > 0) {
        console.warn(
          `[flow-control] LEGACY STALE SLOT CLEANUP: released ${staleResult.releasedSlots} stale legacy slot(s) ` +
          `(flows still in active status but gmt_modified > ${LEGACY_STALE_THRESHOLD_SECS}s old, ` +
          `${staleResult.failedFlows} flows failed)`,
        );
      }
    } catch (err) {
      console.error("[flow-control] legacy stale slot cleanup failed:", err);
    }

    // 2c. Clean up legacy scope entries (pre-simplification: "global", "executor:xxx").
    //     These stale entries from the old 3-scope model accumulate because
    //     the dispatcher skips non-workflow scopes and nothing else removes them.
    //     Deleting them prevents global-scoped queue entries from blocking
    //     the queue and confusing the UI.
    try {
      const legacyResult = await this.repo.deleteLegacyScopeEntries(this.instanceId);
      if (legacyResult.deletedSlots > 0 || legacyResult.deletedQueue > 0) {
        console.warn(
          `[flow-control] LEGACY SCOPE CLEANUP: deleted ${legacyResult.deletedSlots} slots + ` +
          `${legacyResult.deletedQueue} queue entries with non-workflow scopes (global/executor).`,
        );
      }
    } catch (err) {
      console.error("[flow-control] legacy scope cleanup failed:", err);
    }

    // 3. Delete old processed entries (7 days ago)
    const sevenDaysAgo = Math.floor(Date.now() / 1000) - 7 * 24 * 3600;
    await this.repo.deleteProcessedQueueEntries(this.instanceId, sevenDaysAgo);

    // 4. For each scope with queued items, try to dispatch
    const scopes = await this.repo.getScopesWithQueuedItems(this.instanceId);
    if (scopes.length > 0) {
      console.log(`[flow-control] tick: found ${scopes.length} scope(s) with queued items: ${scopes.join(", ")}`);
    }
    for (const scopeKey of scopes) {
      // Only dispatch workflow scopes — skip any legacy global/executor entries
      if (!scopeKey.startsWith("workflow:")) {
        console.warn(`[flow-control] tick: skipping non-workflow scope "${scopeKey}" (legacy entry, will expire)`);
        continue;
      }
      await this.tryDispatch(scopeKey);
    }

    // 5. Recover orphaned waiting flows (waiting but not in queue)
    // BUG FIX: Previously orphaned flows were re-enqueued with expiresAt=null
    // and enqueuedAt=now, which meant: (a) they never expire, and (b) zombie
    // escape (MAX_REENQUEUE_AGE_SECS=1h check on enqueuedAt) could never fire
    // because enqueuedAt was refreshed each tick. This caused infinite
    // re-enqueue loops when a flow was permanently stuck (e.g. session gone).
    // Fix: set expiresAt to 5 minutes and preserve the original enqueuedAt
    // from the orphan payload so zombie escape can break the loop.
    if (this.callbacks.findOrphanedWaitingFlows) {
      try {
        const orphanedFlows = await this.callbacks.findOrphanedWaitingFlows();
        if (orphanedFlows.length > 0) {
          console.log(`[flow-control] found ${orphanedFlows.length} orphaned waiting flows, re-enqueueing: ${orphanedFlows.map(f => f.flowId).join(", ")}`);
          const now = Math.floor(Date.now() / 1000);
          const nowMs = Date.now();
          // Purge expired cooldown entries to prevent unbounded memory growth
          for (const [fid, ts] of this.recentlyEnqueuedFlows) {
            if (nowMs - ts > FlowControlDispatcher.ORPHAN_REENQUEUE_COOLDOWN_MS) {
              this.recentlyEnqueuedFlows.delete(fid);
            }
          }
          for (const orphan of orphanedFlows) {
            // Cooldown check: skip flows that were recently enqueued to prevent
            // TOCTOU duplicates — between findOrphanedWaitingFlows checking the
            // queue and our enqueue call, another tick may have already enqueued
            // this flow. The cooldown window prevents the same flow from being
            // enqueued twice within a 30-second window.
            const lastEnqueued = this.recentlyEnqueuedFlows.get(orphan.flowId);
            if (lastEnqueued !== undefined && (nowMs - lastEnqueued) < FlowControlDispatcher.ORPHAN_REENQUEUE_COOLDOWN_MS) {
              console.log(
                `[flow-control] orphan recovery: skipping flow ${orphan.flowId} — enqueued ${((nowMs - lastEnqueued) / 1000).toFixed(1)}s ago ` +
                `(cooldown ${FlowControlDispatcher.ORPHAN_REENQUEUE_COOLDOWN_MS / 1000}s)`,
              );
              continue;
            }
            try {
              // Re-enqueue at the correct workflow scope so tryDispatch can pick
              // it up with proper slot acquisition on the next tick(s).
              // Preserve payload (containing sessionKey) so the dispatcher
              // can restore the correct TaskFlow session when resuming.
              // Use the workflow-specific scope key to respect per-workflow
              // concurrency limits. Previously this was hardcoded to
              // "workflow:default" which bypassed per-workflow limits —
              // see BUG: orphaned flows acquired slots under a different
              // scope key than new flows, allowing parallel execution
              // beyond maxConcurrent.
              const scopeKey = `workflow:${orphan.workflowId}`;
              // Preserve original enqueuedAt from payload if available,
              // so zombie escape (MAX_REENQUEUE_AGE_SECS) can eventually
              // break infinite re-enqueue loops for permanently stuck flows.
              // BUG FIX: When no enqueuedAt is available in the payload, use
              // (now - MAX_REENQUEUE_AGE_SECS + 60) instead of `now`, so the
              // flow ages out in ~1 minute on the next tick rather than being
              // immortal (age=0 on each re-enqueue defeats zombie escape).
              let preservedEnqueuedAt: number = now - FlowControlDispatcher.MAX_REENQUEUE_AGE_SECS + 60;
              if (orphan.payload) {
                try {
                  const parsed = JSON.parse(orphan.payload);
                  if (typeof parsed.enqueuedAt === "number" && Number.isFinite(parsed.enqueuedAt)) {
                    preservedEnqueuedAt = parsed.enqueuedAt;
                  }
                } catch { /* payload not JSON, use fallback */ }
              }
              // Use the configured queueTimeoutMs for expiry, falling back to 5 minutes.
              // Aligns with reenqueueOnFailure which also uses getQueueTimeoutMsForScope.
              // If the flow can't be dispatched within the timeout, the queue entry
              // expires and is cleaned up by expireStaleEntries, preventing unbounded
              // queue growth.
              const orphanQueueTimeoutMs = getQueueTimeoutMsForScope(this.config, scopeKey);
              const ORPHAN_REENQUEUE_EXPIRY_SECS = orphanQueueTimeoutMs > 0
                ? Math.floor(orphanQueueTimeoutMs / 1000)
                : 300; // default 5 minutes
              const enqueueResult = await this.repo.enqueue({
                instanceId: this.instanceId,
                scopeKey,
                flowId: orphan.flowId,
                nodeId: null,
                priority: 0,
                status: "queued",
                enqueuedAt: preservedEnqueuedAt,
                dispatchAfter: null,
                expiresAt: now + ORPHAN_REENQUEUE_EXPIRY_SECS,
                // Embed original enqueuedAt in payload so future orphan
                // recovery rounds can preserve it for zombie escape.
                payload: JSON.stringify({
                  ...(orphan.payload ? JSON.parse(orphan.payload) : {}),
                  enqueuedAt: preservedEnqueuedAt,
                }),
              });
              // Log enqueue result for operational debugging.
              // enqueue() returns: >0 = success (row ID), 0 = failure, <0 = duplicate.
              if (enqueueResult > 0) {
                console.log(`[flow-control] orphan recovery: enqueued flow ${orphan.flowId} (id=${enqueueResult}, expiresIn=${ORPHAN_REENQUEUE_EXPIRY_SECS}s)`);
              } else if (enqueueResult < 0) {
                console.log(`[flow-control] orphan recovery: flow ${orphan.flowId} already queued (duplicate)`);
              } else {
                console.warn(`[flow-control] orphan recovery: enqueue FAILED for flow ${orphan.flowId} — will retry on next tick`);
              }
              // Track this flow in the cooldown map so subsequent orphan
              // recovery ticks within the cooldown window skip it.
              this.recentlyEnqueuedFlows.set(orphan.flowId, nowMs);
            } catch (err) {
              console.error(`[flow-control] failed to re-enqueue orphaned flow ${orphan.flowId}:`, err);
            }
          }
          // Re-run dispatch for all scopes to pick up the newly enqueued items
          const scopesAfterRequeue = await this.repo.getScopesWithQueuedItems(this.instanceId);
          for (const scopeKey of scopesAfterRequeue) {
            if (!scopeKey.startsWith("workflow:")) continue;
            await this.tryDispatch(scopeKey);
          }
        }
      } catch (err) {
        console.error("[flow-control] orphaned flow detection failed:", err);
      }
    }
  }

  /** Try to dispatch queued items for the specified scope. */
  private async tryDispatch(scopeKey: string): Promise<void> {
    const maxConcurrent = getMaxConcurrentForScope(this.config, scopeKey);
    if (maxConcurrent === 0) {
      // Unlimited scope — dispatch all
      const queued = await this.repo.fetchQueuedItems(this.instanceId, scopeKey, 100);
      console.log(`[flow-control] tryDispatch(${scopeKey}): unlimited scope, ${queued.length} queued items`);
      for (const item of queued) {
        // Zombie escape: check if this entry has been in the queue too long before dispatching
        const now = Math.floor(Date.now() / 1000);
        const enqueuedAt = item.enqueued_at;
        if (enqueuedAt != null && Number.isFinite(enqueuedAt) && (now - enqueuedAt) > FlowControlDispatcher.MAX_REENQUEUE_AGE_SECS) {
          console.warn(
            `[flow-control] tryDispatch(${scopeKey}): ZOMBIE ESCAPE for ${item.flow_id}/${item.node_id ?? "workflow"} — ` +
            `queued for ${now - enqueuedAt}s, expiring instead of dispatching`,
          );
          await this.repo.deleteQueueEntriesForFlow(this.instanceId, item.flow_id);
          // onExpired only releases resources — does NOT modify flow_runs.status
          if (this.callbacks.onExpired) {
            try {
              this.callbacks.onExpired(item.flow_id, item.node_id, item.scope_key, item.payload);
            } catch (expiredErr) {
              console.error(`[flow-control] onExpired callback threw for zombie ${item.flow_id}:`, expiredErr);
            }
          }
          continue;
        }
        // Atomic DELETE to prevent duplicate dispatch by another instance
        const deleted = await this.repo.deleteQueueEntryById(item.id);
        if (!deleted) continue; // Another dispatcher already dispatched this item
        await this.dispatchItem(item);
      }
      return;
    }

    const currentCount = await this.repo.countActiveSlots(this.instanceId, scopeKey);
    // M5 fix: Guard against API failures. countActiveSlots returns -1 on failure,
    // meaning the count is unknown. In this case, skip dispatching for this scope
    // to avoid over-admission. If the count is NaN (legacy safeguard), also skip.
    if (currentCount < 0 || !Number.isFinite(currentCount)) {
      console.warn(`[flow-control] tryDispatch(${scopeKey}): countActiveSlots returned ${currentCount} (API failure or invalid), skipping dispatch to avoid over-admission`);
      return;
    }
    const available = maxConcurrent - currentCount;
    console.log(`[flow-control] tryDispatch(${scopeKey}): maxConcurrent=${maxConcurrent}, currentSlots=${currentCount}, available=${available}`);
    if (available <= 0) return;

    const queued = await this.repo.fetchQueuedItems(this.instanceId, scopeKey, available);
    console.log(`[flow-control] tryDispatch(${scopeKey}): fetched ${queued.length} queued items to dispatch`);
    for (const item of queued) {
      // Zombie escape: check if this entry has been in the queue too long before dispatching
      const now = Math.floor(Date.now() / 1000);
      const enqueuedAt = item.enqueued_at;
      if (enqueuedAt != null && Number.isFinite(enqueuedAt) && (now - enqueuedAt) > FlowControlDispatcher.MAX_REENQUEUE_AGE_SECS) {
        console.warn(
          `[flow-control] tryDispatch(${scopeKey}): ZOMBIE ESCAPE for ${item.flow_id}/${item.node_id ?? "workflow"} — ` +
          `queued for ${now - enqueuedAt}s, expiring instead of dispatching`,
        );
        // Don't acquire a slot — just clean up the queue entry and expire the flow
        try {
          await this.repo.deleteQueueEntriesForFlow(this.instanceId, item.flow_id);
        } catch (delErr) {
          console.error(`[flow-control] failed to delete zombie queue entry for ${item.flow_id}:`, delErr);
        }
        // onExpired only releases resources — does NOT modify flow_runs.status
        if (this.callbacks.onExpired) {
          try {
            this.callbacks.onExpired(item.flow_id, item.node_id, item.scope_key, item.payload);
          } catch (expiredErr) {
            console.error(`[flow-control] onExpired callback threw for zombie ${item.flow_id}:`, expiredErr);
          }
        }
        continue;
      }

      // Acquire a slot FIRST — before deleting the queue entry.
      const nowSec = Math.floor(Date.now() / 1000);
      const acquired = await this.repo.acquireSlot(
        {
          instanceId: this.instanceId,
          scopeKey: item.scope_key,
          flowId: item.flow_id,
          nodeId: item.node_id,
          acquiredAt: nowSec,
          sessionId: extractSessionIdFromPayload(item.payload),
          leaseExpiresAt: nowSec + 60, // LEASE_TTL_SECS = 60
        },
        maxConcurrent,
      );

      if (!acquired) {
        console.warn(
          `[flow-control] slot acquisition failed for ${scopeKey}, flow=${item.flow_id} — skipping, will retry on next tick`,
        );
        // Don't delete the queue entry — leave it as "queued" so it gets picked up next tick.
        // But stop processing more items since we've hit the concurrency limit.
        break;
      }

      // Now that we have a slot, atomically delete the queue entry to prevent duplicate dispatch.
      // Using DELETE WHERE id=? AND status='queued' as an atomic lock — if another dispatcher
      // already dispatched this item, the DELETE will affect 0 rows and we release our slot.
      const deleted = await this.repo.deleteQueueEntryById(item.id);
      if (!deleted) {
        // Another dispatcher instance already dispatched this item — release the slot we acquired.
        console.warn(`[flow-control] deleteQueueEntry race lost for item ${item.id}, releasing slot`);
        await this.repo.releaseSlot(this.instanceId, item.scope_key, item.flow_id, item.node_id);
        continue;
      }

      await this.dispatchItem(item);
    }
  }

  /**
   * Maximum age (in seconds) a queue entry can be re-enqueued.
   * If the original enqueue time is older than this, the entry is expired
   * instead of re-enqueued, breaking the zombie re-enqueue loop.
   * Set to 1 hour — flows stuck in the queue for an hour are unrecoverable.
   */
  private static readonly MAX_REENQUEUE_AGE_SECS = 3600;

  /** Dispatch a single queued item. */
  private async dispatchItem(item: {
    flow_id: string;
    node_id: string | null;
    payload: string | null;
    scope_key: string;
    /** Original enqueue timestamp (unix seconds). Used to detect zombie re-enqueue loops. */
    enqueued_at: number | null;
  }): Promise<void> {
    // Simplified: only workflow-level dispatch (no node-level dispatch)
    const dispatchKey = item.node_id ? `${item.flow_id}/${item.node_id}` : item.flow_id;
    console.log(`[flow-control] DISPATCH_START flowId=${item.flow_id} scope=${item.scope_key}`);
    try {
      await this.callbacks.onWorkflowResume(item.flow_id, item.payload);
      console.log(`[flow-control] DISPATCH_SUCCEEDED flowId=${item.flow_id} key=${dispatchKey}`);
    } catch (err) {
      console.error(
        `[flow-control] dispatch callback threw for ${dispatchKey}:`,
        err,
      );
      // Callback threw — release the slot and re-enqueue so it can be retried.
      await this.reenqueueOnFailure(item, "callback threw");
    }
  }

  /**
   * When dispatch fails (callback threw or returned soft-failure), release the slot
   * and re-enqueue the item so it gets retried on a future tick.
   *
   * Zombie escape: If the original enqueue time is older than MAX_REENQUEUE_AGE_SECS,
   * the item is expired instead of re-enqueued. This breaks the infinite loop where
   * a flow keeps failing dispatch and getting re-enqueued forever.
   *
   * INVARIANT: This method does NOT modify flow_runs.status.
   */
  private async reenqueueOnFailure(
    item: { flow_id: string; node_id: string | null; payload: string | null; scope_key: string; enqueued_at?: number | null; priority?: number | null },
    reason: string,
  ): Promise<void> {
    const dispatchKey = item.node_id ? `${item.flow_id}/${item.node_id}` : item.flow_id;
    console.warn(`[flow-control] reenqueueOnFailure for ${dispatchKey}: ${reason}`);

    // Release all slots for this flow (workflow-level only now)
    try {
      const count = await this.repo.releaseAllSlotsForFlow(this.instanceId, item.flow_id);
      console.log(`[flow-control] released all slots for flow ${item.flow_id}: ${count}`);
    } catch (releaseErr) {
      console.error(`[flow-control] failed to release slots for ${dispatchKey}:`, releaseErr);
    }

    // Clean up old queue entries for this flow
    try {
      await this.repo.deleteQueueEntriesForFlow(this.instanceId, item.flow_id);
    } catch (deleteErr) {
      console.error(`[flow-control] failed to delete old queue entry for ${dispatchKey}:`, deleteErr);
    }

    // Zombie escape: if this entry has been in the queue for too long, expire it
    // instead of re-enqueueing. This prevents infinite dispatch→fail→re-enqueue loops.
    const now = Math.floor(Date.now() / 1000);
    const enqueuedAt = item.enqueued_at;
    if (enqueuedAt != null && Number.isFinite(enqueuedAt) && (now - enqueuedAt) > FlowControlDispatcher.MAX_REENQUEUE_AGE_SECS) {
      const ageSecs = now - enqueuedAt;
      console.warn(
        `[flow-control] ZOMBIE ESCAPE: ${dispatchKey} has been in queue for ${ageSecs}s (max ${FlowControlDispatcher.MAX_REENQUEUE_AGE_SECS}s). ` +
        `Expiring instead of re-enqueueing. Resources released, flow state unchanged.`,
      );
      // Only release resources — do NOT modify flow_runs.status
      // The Controller's orphan recovery or timeout mechanism will handle the flow
      if (this.callbacks.onExpired) {
        try {
          this.callbacks.onExpired(item.flow_id, item.node_id, item.scope_key, item.payload);
        } catch (expiredErr) {
          console.error(`[flow-control] onExpired callback threw for zombie ${dispatchKey}:`, expiredErr);
        }
      }
      return;
    }

    try {
      // Re-enqueue with "queued" status so the dispatcher picks it up again.
      // Normalize legacy scope keys (e.g., "global", "executor:xxx") to the workflow scope.
      // Legacy keys from the old 3-scope model must not be preserved — they would
      // create queue entries that the dispatcher skips (non-workflow scopes are ignored)
      // and that accumulate indefinitely as shown in the flow-control UI.
      let scopeKey = item.scope_key;
      if (!scopeKey.startsWith("workflow:")) {
        console.warn(
          `[flow-control] reenqueueOnFailure: normalizing legacy scope "${scopeKey}" for flow ${item.flow_id}. ` +
          `Legacy global/executor scopes are no longer valid in the simplified model.`,
        );
        // Drop the item — legacy scope entries cannot be re-enqueued meaningfully
        // because we don't know the correct workflowId. The orphan recovery path
        // will handle the flow if it's still in waiting state.
        await this.repo.deleteQueueEntriesForFlow(this.instanceId, item.flow_id);
        console.log(
          `[flow-control] reenqueueOnFailure: deleted legacy-scope queue entries for flow ${item.flow_id}. ` +
          `Orphan recovery will re-enqueue with correct scope if needed.`,
        );
        return;
      }
      // Preserve the original priority and enqueuedAt timestamp.
      const preservedPriority = item.priority ?? 0;
      // BUG FIX: When enqueuedAt is null/undefined, use a timestamp just under
      // the zombie escape threshold instead of `now`. Using `now` would reset
      // the age to 0 on each re-enqueue, making zombie escape unreachable for
      // flows whose original enqueuedAt was lost. Setting it to
      // (now - MAX_REENQUEUE_AGE_SECS + 60) means the flow has ~1 minute
      // before zombie escape kicks in on the NEXT re-enqueue cycle, breaking
      // the infinite loop.
      const preservedEnqueuedAt = enqueuedAt ?? (now - FlowControlDispatcher.MAX_REENQUEUE_AGE_SECS + 60);
      // BUG FIX: Set expiresAt so re-enqueued entries don't persist forever.
      // Previously expiresAt=null meant the entry would never expire, causing
      // infinite re-enqueue loops when dispatch keeps failing (e.g. session gone).
      // Use queueTimeoutMs from config if available; fallback to 5 minutes.
      const queueTimeoutMs = getQueueTimeoutMsForScope(this.config, scopeKey);
      const reenqueueExpirySecs = queueTimeoutMs > 0
        ? Math.floor(queueTimeoutMs / 1000)
        : 300; // default 5 minutes
      await this.repo.enqueue({
        instanceId: this.instanceId,
        scopeKey,
        flowId: item.flow_id,
        nodeId: item.node_id,
        priority: preservedPriority,
        status: "queued",
        enqueuedAt: preservedEnqueuedAt,
        dispatchAfter: now + 5, // 5-second backoff before retry
        expiresAt: now + reenqueueExpirySecs,
        // Preserve enqueuedAt in payload so future orphan recovery rounds
        // can track the original enqueue time for zombie escape.
        payload: (() => {
          try {
            const parsed = item.payload ? JSON.parse(item.payload) : {};
            return JSON.stringify({ ...parsed, enqueuedAt: preservedEnqueuedAt });
          } catch {
            return JSON.stringify({ enqueuedAt: preservedEnqueuedAt });
          }
        })(),
      });
      console.log(`[flow-control] re-enqueued ${dispatchKey} with 5s backoff (scope=${scopeKey}, priority=${preservedPriority}, expiresIn=${reenqueueExpirySecs}s)`);
    } catch (enqueueErr) {
      console.error(`[flow-control] failed to re-enqueue ${dispatchKey}:`, enqueueErr);
      // As a last resort, the orphan recovery path should catch this flow
      // on the next tick (if it's still in "waiting" state with no queue entry).
    }
  }
}