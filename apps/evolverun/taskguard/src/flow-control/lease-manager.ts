/**
 * LeaseManager — heartbeat-driven lease renewal for flow control slots.
 *
 * Each slot acquired by this instance carries a `lease_expires_at` TTL.
 * The LeaseManager periodically renews all active leases (heartbeat) and
 * cleans up expired leases from other instances that may have crashed.
 *
 * Key design principles:
 * - Flow control only manages resources (slots), NEVER modifies flow_runs.status.
 * - Expired lease = DELETE slot only. The owning flow may still be running;
 *   temporary concurrency over-limit is acceptable.
 * - `lease_expires_at = 0` means legacy data (never expires), managed by
 *   the existing `releaseOrphanedSlots` / `releaseStaleOrphanedSlots` path.
 *
 * Lifecycle:
 * 1. Plugin creates LeaseManager(repo, config, instanceId)
 * 2. Plugin calls start() after initialization
 * 3. Every HEARTBEAT_INTERVAL_MS, LeaseManager:
 *    a. Renews all non-expired leases for this instance (heartbeat)
 *    b. Cleans up expired leases from any instance (zombie reaping)
 * 4. Plugin calls stop() during shutdown
 */
import type { IFlowControlRepository } from "../db/repositories/types.js";
import type { FlowControlConfig } from "./types.js";
import { LEASE_TTL_SECS } from "./service.js";

/** How often the heartbeat runs (every 30 seconds). */
const HEARTBEAT_INTERVAL_MS = 30_000;

/**
 * LeaseManager handles two periodic tasks:
 *
 * 1. **Heartbeat (renewLeases)**: Extends `lease_expires_at` for all slots
 *    owned by this instance that have not yet expired. This proves liveness.
 *
 * 2. **Expired lease cleanup (releaseExpiredLeases)**: Deletes slots whose
 *    lease has expired (from any instance). The owning process is presumed
 *    dead or partitioned. Only slots and queue entries are deleted —
 *    flow_runs.status is intentionally NOT modified.
 */
export class LeaseManager {
  private heartbeatHandle: ReturnType<typeof setInterval> | null = null;
  private running = false;

  constructor(
    private readonly repo: IFlowControlRepository,
    private readonly config: FlowControlConfig,
    private readonly instanceId: string,
  ) {}

  /** Start the heartbeat loop. */
  start(): void {
    if (this.heartbeatHandle) {
      console.warn("[lease-manager] already started, ignoring duplicate start()");
      return;
    }
    // Run first tick immediately
    this.tick().catch((err) => {
      console.error("[lease-manager] initial tick failed:", err);
    });
    this.heartbeatHandle = setInterval(() => {
      this.tick().catch((err) => {
        console.error("[lease-manager] tick error:", err);
      });
    }, HEARTBEAT_INTERVAL_MS);
    console.log(
      `[lease-manager] started (heartbeat: ${HEARTBEAT_INTERVAL_MS}ms, ` +
      `lease TTL: ${LEASE_TTL_SECS}s, instance: ${this.instanceId})`,
    );
  }

  /** Stop the heartbeat loop. */
  stop(): void {
    if (this.heartbeatHandle) {
      clearInterval(this.heartbeatHandle);
      this.heartbeatHandle = null;
    }
    console.log("[lease-manager] stopped");
  }

  /** Whether the heartbeat loop is currently running. */
  isRunning(): boolean {
    return this.running;
  }

  /** Execute one heartbeat + cleanup cycle. */
  async tick(): Promise<{ renewed: number; releasedSlots: number; deletedQueue: number }> {
    if (this.running) {
      // Prevent overlapping ticks
      return { renewed: 0, releasedSlots: 0, deletedQueue: 0 };
    }
    this.running = true;
    try {
      return await this.tickInternal();
    } finally {
      this.running = false;
    }
  }

  private async tickInternal(): Promise<{ renewed: number; releasedSlots: number; deletedQueue: number }> {
    if (!this.config.enabled) {
      return { renewed: 0, releasedSlots: 0, deletedQueue: 0 };
    }

    const nowSec = Math.floor(Date.now() / 1000);
    const newExpiryAt = nowSec + LEASE_TTL_SECS;

    // 1. Renew all active leases for this instance
    let renewed = 0;
    try {
      renewed = await this.repo.renewLeases(this.instanceId, newExpiryAt);
      if (renewed > 0) {
        console.log(
          `[lease-manager] HEARTBEAT: renewed ${renewed} lease(s), ` +
          `new expiry=${newExpiryAt} (TTL=${LEASE_TTL_SECS}s)`,
        );
      }
    } catch (err) {
      console.error("[lease-manager] renewLeases failed:", err);
      // Don't abort — cleanup is still valuable even if heartbeat failed
    }

    // 2. Clean up expired leases from any instance (zombie reaping)
    let releasedSlots = 0;
    let deletedQueue = 0;
    try {
      const result = await this.repo.releaseExpiredLeases(this.instanceId);
      releasedSlots = result.releasedSlots;
      deletedQueue = result.deletedQueue;
      // Logging is already done inside releaseExpiredLeases when slots are released
    } catch (err) {
      console.error("[lease-manager] releaseExpiredLeases failed:", err);
    }

    return { renewed, releasedSlots, deletedQueue };
  }
}