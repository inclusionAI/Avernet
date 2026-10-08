/**
 * SQLite-backed implementation of IFlowControlRepository.
 *
 * Uses IDatabase.transaction() for atomic check-then-insert in acquireSlot.
 * All timestamps are unix seconds (INTEGER).
 */
import type { IDatabase } from "../db/types.js";
import type {
  IFlowControlRepository,
  FlowControlSlotInsert,
  FlowControlQueueInsert,
  FlowControlQueueRow,
  FlowControlSlotRow,
} from "../db/repositories/types.js";

export class SqliteFlowControlRepository implements IFlowControlRepository {
  constructor(private db: IDatabase) {}

  async acquireSlot(insert: FlowControlSlotInsert, maxConcurrent: number): Promise<boolean> {
    // maxConcurrent = 0 means unlimited
    if (maxConcurrent === 0) return true;

    try {
      return await this.db.transaction(async (txDb) => {
        const now = Math.floor(Date.now() / 1000);
        // Count only active (non-expired) slots. lease_expires_at = 0 means legacy row (never expires).
        const rows = await txDb.query<{ cnt: number }>(
          `SELECT COUNT(*) as cnt FROM flow_control_slots
           WHERE instance_id = ? AND scope_key = ?
             AND (lease_expires_at = 0 OR lease_expires_at > ?)`,
          [insert.instanceId, insert.scopeKey, now],
        );
        const count = rows[0]?.cnt ?? 0;
        if (count >= maxConcurrent) {
          return false;
        }
        const leaseExpiresAt = insert.leaseExpiresAt ?? 0;
        await txDb.exec(
          `INSERT INTO flow_control_slots (instance_id, scope_key, flow_id, node_id, acquired_at, session_id, lease_expires_at, renew_count, gmt_create, gmt_modified)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)`,
          [insert.instanceId, insert.scopeKey, insert.flowId, insert.nodeId, insert.acquiredAt, insert.sessionId ?? null, leaseExpiresAt, now, now],
        );
        return true;
      });
    } catch (err) {
      console.error("[flow-control] acquireSlot failed:", err);
      throw err;
    }
  }

  async releaseSlot(instanceId: string, scopeKey: string, flowId: string, nodeId: string | null): Promise<boolean> {
    try {
      const result = await this.db.exec(
        "DELETE FROM flow_control_slots WHERE instance_id = ? AND scope_key = ? AND flow_id = ? AND node_id IS ?",
        [instanceId, scopeKey, flowId, nodeId],
      );
      return result.affectedRows > 0;
    } catch (err) {
      console.error("[flow-control] releaseSlot failed:", err);
      return false;
    }
  }

  async releaseAllSlotsForFlow(instanceId: string, flowId: string): Promise<number> {
    try {
      const result = await this.db.exec(
        "DELETE FROM flow_control_slots WHERE instance_id = ? AND flow_id = ?",
        [instanceId, flowId],
      );
      return result.affectedRows;
    } catch (err) {
      console.error("[flow-control] releaseAllSlotsForFlow failed:", err);
      return 0;
    }
  }

  async countSlots(instanceId: string, scopeKey: string): Promise<number> {
    const rows = await this.db.query<{ cnt: number }>(
      "SELECT COUNT(*) as cnt FROM flow_control_slots WHERE instance_id = ? AND scope_key = ?",
      [instanceId, scopeKey],
    );
    return rows[0]?.cnt ?? 0;
  }

  async enqueue(insert: FlowControlQueueInsert): Promise<number> {
    const now = Math.floor(Date.now() / 1000);
    const result = await this.db.exec(
      `INSERT INTO flow_control_queue (instance_id, scope_key, flow_id, node_id, priority, status, enqueued_at, dispatch_after, expires_at, payload, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [insert.instanceId, insert.scopeKey, insert.flowId, insert.nodeId, insert.priority,
       insert.status, insert.enqueuedAt, insert.dispatchAfter, insert.expiresAt, insert.payload,
       now, now],
    );
    return result.insertId ?? 0;
  }

  async markDispatched(id: number): Promise<boolean> {
    // Legacy: kept for backward compatibility. New code uses deleteQueueEntryById.
    const result = await this.db.exec(
      "UPDATE flow_control_queue SET status = 'dispatched' WHERE id = ? AND status = 'queued'",
      [id],
    );
    return result.affectedRows > 0;
  }

  async deleteQueueEntryById(id: number): Promise<boolean> {
    try {
      const result = await this.db.exec(
        "DELETE FROM flow_control_queue WHERE id = ? AND status = 'queued'",
        [id],
      );
      return result.affectedRows > 0;
    } catch (err) {
      console.error("[flow-control] deleteQueueEntryById failed:", err);
      return false;
    }
  }

  async markExpired(id: number): Promise<boolean> {
    const result = await this.db.exec(
      "UPDATE flow_control_queue SET status = 'expired' WHERE id = ?",
      [id],
    );
    return result.affectedRows > 0;
  }

  async expireStaleEntries(instanceId: string): Promise<number> {
    const now = Math.floor(Date.now() / 1000);
    const result = await this.db.exec(
      "UPDATE flow_control_queue SET status = 'expired' WHERE instance_id = ? AND status = 'queued' AND expires_at IS NOT NULL AND expires_at < ?",
      [instanceId, now],
    );
    return result.affectedRows;
  }

  async fetchExpiringItems(instanceId: string): Promise<FlowControlQueueRow[]> {
    const now = Math.floor(Date.now() / 1000);
    return this.db.query<FlowControlQueueRow>(
      `SELECT * FROM flow_control_queue
       WHERE instance_id = ? AND status = 'queued' AND expires_at IS NOT NULL AND expires_at < ?`,
      [instanceId, now],
    );
  }

  async fetchQueuedItems(instanceId: string, scopeKey: string, limit: number): Promise<FlowControlQueueRow[]> {
    const now = Math.floor(Date.now() / 1000);
    return this.db.query<FlowControlQueueRow>(
      `SELECT * FROM flow_control_queue
       WHERE instance_id = ? AND scope_key = ? AND status = 'queued'
         AND (dispatch_after IS NULL OR dispatch_after <= ?)
       ORDER BY priority ASC, enqueued_at ASC
       LIMIT ?`,
      [instanceId, scopeKey, now, limit],
    );
  }

  async getScopesWithQueuedItems(instanceId: string): Promise<string[]> {
    const rows = await this.db.query<{ scope_key: string }>(
      "SELECT DISTINCT scope_key FROM flow_control_queue WHERE instance_id = ? AND status = 'queued'",
      [instanceId],
    );
    return rows.map((r) => r.scope_key);
  }

  async deleteProcessedQueueEntries(instanceId: string, olderThan: number): Promise<number> {
    const result = await this.db.exec(
      "DELETE FROM flow_control_queue WHERE instance_id = ? AND status IN ('dispatched', 'expired') AND gmt_modified < ?",
      [instanceId, olderThan],
    );
    return result.affectedRows;
  }

  async releaseOrphanedSlots(instanceId: string): Promise<number> {
    try {
      const result = await this.db.exec(
        `DELETE FROM flow_control_slots
         WHERE instance_id = ?
           AND flow_id NOT IN (
             SELECT flow_id FROM flow_runs
             WHERE status IN ('running', 'queued', 'waiting', 'blocked')
           )`,
        [instanceId],
      );
      return result.affectedRows;
    } catch (err) {
      console.error("[flow-control] releaseOrphanedSlots failed:", err);
      return 0;
    }
  }

  async releaseStaleOrphanedSlots(instanceId: string, staleSeconds: number): Promise<{ releasedSlots: number; failedFlows: number }> {
    try {
      const now = Math.floor(Date.now() / 1000);
      const cutoff = now - staleSeconds;

      return await this.db.transaction(async (txDb) => {
        // Find flow_ids that both have slots and are stale in flow_runs
        const staleFlows = await txDb.query<{ flow_id: string }>(
          `SELECT DISTINCT s.flow_id
           FROM flow_control_slots s
           JOIN flow_runs r ON s.flow_id = r.flow_id
           WHERE s.instance_id = ?
             AND r.status IN ('running', 'queued', 'waiting', 'blocked')
             AND r.gmt_modified < ?`,
          [instanceId, cutoff],
        );

        if (staleFlows.length === 0) {
          return { releasedSlots: 0, failedFlows: 0 };
        }

        const flowIds = staleFlows.map((r) => r.flow_id);
        const placeholders = flowIds.map(() => "?").join(", ");

        // Release slots for stale flows — flow control only manages resources, NOT flow_runs.status.
        // Do NOT delete queue entries: stale flows may still have queued nodes that need
        // to be re-dispatched once capacity is available. Deleting queue entries would
        // permanently stick the flow because the dispatcher can no longer see it.
        const slotResult = await txDb.exec(
          `DELETE FROM flow_control_slots
           WHERE instance_id = ?
             AND flow_id IN (${placeholders})`,
          [instanceId, ...flowIds],
        );

        // NOTE: We intentionally do NOT DELETE queue entries for stale flows.
        // The orphan recovery path (findOrphanedWaitingFlows) handles stuck flows
        // properly by re-enqueueing them. Queue entries are only removed via
        // explicit flow completion paths or zombie escape (MAX_REENQUEUE_AGE_SECS).
        // NOTE: We also do NOT UPDATE flow_runs SET status='failed'.
        // Flow state is owned exclusively by the Controller, not by flow control.

        console.log(
          `[flow-control] releaseStaleOrphanedSlots: released ${slotResult.affectedRows} slots ` +
          `(stale > ${staleSeconds}s), flowIds=[${flowIds.join(", ")}]. Queue entries preserved.`,
        );

        return { releasedSlots: slotResult.affectedRows, failedFlows: 0 };
      });
    } catch (err) {
      console.error("[flow-control] releaseStaleOrphanedSlots failed:", err);
      return { releasedSlots: 0, failedFlows: 0 };
    }
  }

  async deleteQueueEntriesForFlow(instanceId: string, flowId: string): Promise<number> {
    const result = await this.db.exec(
      "DELETE FROM flow_control_queue WHERE instance_id = ? AND flow_id = ?",
      [instanceId, flowId],
    );
    return result.affectedRows;
  }

  async getScopeStatus(instanceId: string, scopeKey: string): Promise<{ running: number; queued: number }> {
    const nowSec = Math.floor(Date.now() / 1000);
    // Exclude expired lease slots from running count (lease_expires_at = 0 means legacy, always active)
    const slotRows = await this.db.query<{ cnt: number }>(
      `SELECT COUNT(*) as cnt FROM flow_control_slots
       WHERE instance_id = ? AND scope_key = ?
         AND (lease_expires_at = 0 OR lease_expires_at > ?)`,
      [instanceId, scopeKey, nowSec],
    );
    const queueRows = await this.db.query<{ cnt: number }>(
      "SELECT COUNT(*) as cnt FROM flow_control_queue WHERE instance_id = ? AND scope_key = ? AND status = 'queued'",
      [instanceId, scopeKey],
    );
    return {
      running: slotRows[0]?.cnt ?? 0,
      queued: queueRows[0]?.cnt ?? 0,
    };
  }

  async getActiveScopeKeys(instanceId: string): Promise<string[]> {
    const rows = await this.db.query<{ scope_key: string }>(
      "SELECT DISTINCT scope_key FROM flow_control_slots WHERE instance_id = ?",
      [instanceId],
    );
    return rows.map((r) => r.scope_key);
  }

  async getQueueItems(instanceId: string, scopeKey?: string, limit?: number): Promise<FlowControlQueueRow[]> {
    if (scopeKey) {
      return this.db.query<FlowControlQueueRow>(
        `SELECT * FROM flow_control_queue
         WHERE instance_id = ? AND scope_key = ? AND status = 'queued'
         ORDER BY priority ASC, enqueued_at ASC
         LIMIT ?`,
        [instanceId, scopeKey, limit ?? 100],
      );
    }
    return this.db.query<FlowControlQueueRow>(
      `SELECT * FROM flow_control_queue
       WHERE instance_id = ? AND status = 'queued'
       ORDER BY priority ASC, enqueued_at ASC
       LIMIT ?`,
      [instanceId, limit ?? 100],
    );
  }

  async getSlots(instanceId: string, scopeKey?: string): Promise<FlowControlSlotRow[]> {
    if (scopeKey) {
      return this.db.query<FlowControlSlotRow>(
        "SELECT * FROM flow_control_slots WHERE instance_id = ? AND scope_key = ? ORDER BY acquired_at ASC",
        [instanceId, scopeKey],
      );
    }
    return this.db.query<FlowControlSlotRow>(
      "SELECT * FROM flow_control_slots WHERE instance_id = ? ORDER BY acquired_at ASC",
      [instanceId],
    );
  }

  async forceReleaseSlotsForFlows(instanceId: string, flowIds: string[]): Promise<{ releasedSlots: number; deletedQueue: number }> {
    if (flowIds.length === 0) {
      return { releasedSlots: 0, deletedQueue: 0 };
    }
    try {
      return await this.db.transaction(async (txDb) => {
        const placeholders = flowIds.map(() => "?").join(", ");

        // Release slots — flow control only manages resources, NOT flow_runs.status
        const slotResult = await txDb.exec(
          `DELETE FROM flow_control_slots WHERE instance_id = ? AND flow_id IN (${placeholders})`,
          [instanceId, ...flowIds],
        );

        // Delete queue entries for these flows
        const queueResult = await txDb.exec(
          `DELETE FROM flow_control_queue WHERE instance_id = ? AND flow_id IN (${placeholders})`,
          [instanceId, ...flowIds],
        );

      // NOTE: We intentionally do NOT UPDATE flow_runs SET status='failed'.
      // Flow state is owned exclusively by the Controller, not by flow control.
      // Releasing a slot only frees the concurrency resource.

      console.log(
        `[flow-control] forceReleaseSlotsForFlows: released ${slotResult.affectedRows} slots, ` +
        `deleted ${queueResult.affectedRows} queue entries, ` +
        `flowIds=[${flowIds.join(", ")}]`,
      );

      return {
        releasedSlots: slotResult.affectedRows,
        deletedQueue: queueResult.affectedRows,
      };
      });
    } catch (err) {
      console.error("[flow-control] forceReleaseSlotsForFlows failed:", err);
      return { releasedSlots: 0, deletedQueue: 0 };
    }
  }

  async findSlotsGroupedBySession(instanceId: string): Promise<Array<{ session_id: string; flow_ids: string[] }>> {
    try {
      // Find distinct session_ids with their associated flow_ids.
      // Only consider slots that have a non-null, non-empty session_id.
      const rows = await this.db.query<{ session_id: string; flow_ids: string }>(
        `SELECT session_id, GROUP_CONCAT(DISTINCT flow_id) AS flow_ids
         FROM flow_control_slots
         WHERE instance_id = ?
           AND session_id IS NOT NULL
           AND session_id != ''
         GROUP BY session_id`,
        [instanceId],
      );
      return rows.map((r) => ({
        session_id: r.session_id,
        flow_ids: r.flow_ids.split(","),
      }));
    } catch (err) {
      console.error("[flow-control] findSlotsGroupedBySession failed:", err);
      return [];
    }
  }

  async renewLeases(instanceId: string, newExpiryAt: number): Promise<number> {
    try {
      const now = Math.floor(Date.now() / 1000);
      // Only renew leases that are currently active (not expired).
      // lease_expires_at = 0 means legacy row (not lease-based), skip those.
      const result = await this.db.exec(
        `UPDATE flow_control_slots
         SET lease_expires_at = ?,
             renew_count = renew_count + 1,
             gmt_modified = ?
         WHERE instance_id = ?
           AND lease_expires_at > 0
           AND lease_expires_at > ?`,
        [newExpiryAt, now, instanceId, now],
      );
      return result.affectedRows;
    } catch (err) {
      console.error("[flow-control] renewLeases failed:", err);
      throw err;
    }
  }

  async releaseExpiredLeases(instanceId: string): Promise<{ releasedSlots: number; deletedQueue: number }> {
    try {
      const now = Math.floor(Date.now() / 1000);

      // Delete expired lease-based slots ONLY — never touch queue entries.
      // A flow with an expired lease may still have a queued entry waiting
      // for capacity; deleting it would make the flow permanently stuck.
      // Queue entries are managed by their own lifecycle:
      //   - markDispatched when dispatcher picks them up
      //   - deleteProcessedQueueEntries after 7 days
      //   - deleteQueueEntriesForFlow on explicit flow completion
      //   - ZOMBIE ESCAPE (MAX_REENQUEUE_AGE_SECS) for stale entries
      const slotResult = await this.db.exec(
        `DELETE FROM flow_control_slots
         WHERE instance_id = ?
           AND lease_expires_at > 0
           AND lease_expires_at < ?`,
        [instanceId, now],
      );

      // deletedQueue is always 0 — we no longer delete queue entries here.
      // Releasing a slot does NOT mean the flow is done; its queue entry
      // is the dispatcher's only way to re-dispatch it when capacity frees up.
      if (slotResult.affectedRows > 0) {
        console.warn(
          `[flow-control] EXPIRED LEASE CLEANUP: released ${slotResult.affectedRows} slot(s) ` +
          `(queue entries preserved for dispatcher re-dispatch)`,
        );
      }

      return {
        releasedSlots: slotResult.affectedRows,
        deletedQueue: 0,
      };
    } catch (err) {
      console.error("[flow-control] releaseExpiredLeases failed:", err);
      return { releasedSlots: 0, deletedQueue: 0 };
    }
  }

  async countActiveSlots(instanceId: string, scopeKey: string): Promise<number> {
    const now = Math.floor(Date.now() / 1000);
    const rows = await this.db.query<{ cnt: number }>(
      `SELECT COUNT(*) as cnt FROM flow_control_slots
       WHERE instance_id = ? AND scope_key = ?
         AND (lease_expires_at = 0 OR lease_expires_at > ?)`,
      [instanceId, scopeKey, now],
    );
    return rows[0]?.cnt ?? 0;
  }

  async deleteLegacyScopeEntries(instanceId: string): Promise<{ deletedSlots: number; deletedQueue: number }> {
    try {
      return await this.db.transaction(async (txDb) => {
        // Delete slots with legacy scopes (not starting with "workflow:")
        const slotResult = await txDb.exec(
          `DELETE FROM flow_control_slots
           WHERE instance_id = ?
             AND scope_key NOT LIKE 'workflow:%'`,
          [instanceId],
        );
        // Delete queue entries with legacy scopes (not starting with "workflow:")
        const queueResult = await txDb.exec(
          `DELETE FROM flow_control_queue
           WHERE instance_id = ?
             AND scope_key NOT LIKE 'workflow:%'`,
          [instanceId],
        );
        const deletedSlots = slotResult.affectedRows;
        const deletedQueue = queueResult.affectedRows;
        if (deletedSlots > 0 || deletedQueue > 0) {
          console.log(
            `[flow-control] deleteLegacyScopeEntries: cleaned up ${deletedSlots} legacy slots, ` +
            `${deletedQueue} legacy queue entries (scopes not matching "workflow:*")`,
          );
        }
        return { deletedSlots, deletedQueue };
      });
    } catch (err) {
      console.error("[flow-control] deleteLegacyScopeEntries failed:", err);
      return { deletedSlots: 0, deletedQueue: 0 };
    }
  }
}