/**
 * FlowControlRepository — reads and writes flow_control_slots / flow_control_queue
 * tables via raw SQL. No dependency on ClawFlow; shares the same database schema.
 */
import type { IDatabase } from "@avernet/clawweb-shared/server/db";

export type FlowControlSlotRow = {
  id: number;
  instance_id: string;
  scope_key: string;
  flow_id: string;
  node_id: string | null;
  acquired_at: number;
  session_id: string | null;
  lease_expires_at: number;
  renew_count: number;
  gmt_create: number;
  gmt_modified: number;
};

export type FlowControlQueueRow = {
  id: number;
  instance_id: string;
  scope_key: string;
  flow_id: string;
  node_id: string | null;
  priority: number;
  status: string;
  enqueued_at: number;
  dispatch_after: number | null;
  expires_at: number | null;
  payload: string | null;
  gmt_create: number;
  gmt_modified: number;
};

export class FlowControlRepository {
  constructor(private db: IDatabase) {}

  // ── Slot operations ──

  /**
   * Atomically acquire a slot: check count < maxConcurrent, then insert.
   *
   * Race condition fix (BUG-10): Under MySQL REPEATABLE READ, a plain
   * SELECT COUNT + INSERT in a transaction is NOT atomic — concurrent
   * transactions read the same snapshot and both see count < max, causing
   * oversubscription. We fix this by:
   *
   * - MySQL: Using SELECT ... FOR UPDATE to acquire row-level locks on the
   *   scope's slots before counting, serializing concurrent acquire calls.
   * - SQLite: SELECT ... FOR UPDATE is not supported, but SQLite's
   *   serialized write access (single WAL writer) provides sufficient
   *   serialization for single-node deployments.
   */
  async acquireSlot(params: {
    instance_id: string;
    scope_key: string;
    flow_id: string;
    node_id: string | null;
    acquired_at: number;
    session_id?: string | null;
    lease_expires_at?: number;
    max_concurrent: number;
  }): Promise<boolean> {
    if (params.max_concurrent === 0) return true;

    try {
      return await this.db.transaction(async (txDb) => {
        const isMySql = txDb.dbType === "mysql" || txDb.dbType === "zdas";
        const lockClause = isMySql ? " FOR UPDATE" : "";
        // Use countActiveSlots logic: exclude slots whose lease has expired
        // (lease_expires_at = 0 means legacy slot, never auto-expires)
        const nowSec = Math.floor(Date.now() / 1000);
        const activeCondition = isMySql
          ? `(lease_expires_at = 0 OR lease_expires_at > ?)`
          : `(lease_expires_at = 0 OR lease_expires_at > ?)`;
        const rows = await txDb.query<{ cnt: number }>(
          `SELECT COUNT(*) as cnt FROM flow_control_slots
           WHERE instance_id = ? AND scope_key = ?
             AND ${activeCondition}${lockClause}`,
          [params.instance_id, params.scope_key, nowSec],
        );
        const count = rows[0]?.cnt ?? 0;
        if (count >= params.max_concurrent) {
          return false;
        }
        const now = this.db.dialect.now() as number;
        const leaseExpiresAt = params.lease_expires_at ?? 0;
        await txDb.exec(
          `INSERT INTO flow_control_slots (instance_id, scope_key, flow_id, node_id, acquired_at, session_id, lease_expires_at, renew_count, gmt_create, gmt_modified)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)`,
          [params.instance_id, params.scope_key, params.flow_id, params.node_id, params.acquired_at, params.session_id ?? null, leaseExpiresAt, now, now],
        );
        return true;
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.acquireSlot failed: ${msg}`);
      return false;
    }
  }

  async releaseSlot(params: {
    instance_id: string;
    scope_key: string;
    flow_id: string;
    node_id: string | null;
  }): Promise<boolean> {
    try {
      const result = await this.db.exec(
        "DELETE FROM flow_control_slots WHERE instance_id = ? AND scope_key = ? AND flow_id = ? AND node_id IS ?",
        [params.instance_id, params.scope_key, params.flow_id, params.node_id],
      );
      return result.affectedRows > 0;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.releaseSlot failed: ${msg}`);
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
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.releaseAllSlotsForFlow failed: ${msg}`);
      return 0;
    }
  }

  async countSlots(instanceId: string, scopeKey: string): Promise<number> {
    try {
      const rows = await this.db.query<{ cnt: number }>(
        "SELECT COUNT(*) as cnt FROM flow_control_slots WHERE instance_id = ? AND scope_key = ?",
        [instanceId, scopeKey],
      );
      return rows[0]?.cnt ?? 0;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.countSlots failed: ${msg}`);
      return 0;
    }
  }

  /**
   * Count active slots excluding those with expired leases.
   * Slots with lease_expires_at = 0 are legacy slots that never auto-expire,
   * so they are always counted as active.
   */
  async countActiveSlots(instanceId: string, scopeKey: string): Promise<number> {
    try {
      const nowSec = Math.floor(Date.now() / 1000);
      const rows = await this.db.query<{ cnt: number }>(
        `SELECT COUNT(*) as cnt FROM flow_control_slots
         WHERE instance_id = ? AND scope_key = ?
           AND (lease_expires_at = 0 OR lease_expires_at > ?)`,
        [instanceId, scopeKey, nowSec],
      );
      return rows[0]?.cnt ?? 0;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.countActiveSlots failed: ${msg}`);
      return 0;
    }
  }

  /**
   * Delete legacy scope entries (not starting with "workflow:").
   * In the old 3-scope model, scopes were "global" and "executor:xxx".
   * After simplification to perWorkflow-only, these are stale data that
   * should be cleaned up to prevent incorrect flow control behavior.
   */
  async deleteLegacyScopeEntries(instanceId: string): Promise<{ deletedSlots: number; deletedQueue: number }> {
    try {
      // Delete slots with legacy scopes (not starting with "workflow:")
      const slotResult = await this.db.exec(
        `DELETE FROM flow_control_slots
         WHERE instance_id = ?
           AND scope_key NOT LIKE 'workflow:%'`,
        [instanceId],
      );
      // Delete queue entries with legacy scopes (not starting with "workflow:")
      const queueResult = await this.db.exec(
        `DELETE FROM flow_control_queue
         WHERE instance_id = ?
           AND scope_key NOT LIKE 'workflow:%'`,
        [instanceId],
      );
      const deletedSlots = slotResult.affectedRows;
      const deletedQueue = queueResult.affectedRows;
      if (deletedSlots > 0 || deletedQueue > 0) {
        console.log(
          `[db] FlowControlRepository.deleteLegacyScopeEntries: cleaned up ${deletedSlots} legacy slots, ` +
          `${deletedQueue} legacy queue entries (scopes not matching "workflow:*")`,
        );
      }
      return { deletedSlots, deletedQueue };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.deleteLegacyScopeEntries failed: ${msg}`);
      return { deletedSlots: 0, deletedQueue: 0 };
    }
  }

  /**
   * Renew leases for all active (non-expired) slots owned by this instance.
   * Sets lease_expires_at to the given new_expiry_at (absolute unix timestamp).
   * Only renews slots whose current lease has not yet expired (prevents dead
   * processes from renewing). Returns the number of slots renewed.
   */
  async renewLeases(instanceId: string, newExpiryAt: number): Promise<number> {
    try {
      const nowSec = Math.floor(Date.now() / 1000);
      const result = await this.db.exec(
        `UPDATE flow_control_slots
         SET lease_expires_at = ?, renew_count = renew_count + 1, gmt_modified = ?
         WHERE instance_id = ?
           AND lease_expires_at > 0
           AND lease_expires_at > ?`,
        [newExpiryAt, this.db.dialect.now(), instanceId, nowSec],
      );
      return result.affectedRows;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.renewLeases failed: ${msg}`);
      return 0;
    }
  }

  /**
   * Release slots whose lease has expired.
   * Only processes slots with lease_expires_at > 0 (lease-based slots).
   * Legacy slots (lease_expires_at = 0) are never auto-expired by this method.
   *
   * IMPORTANT: This method ONLY releases slots (concurrency resources).
   * It does NOT delete queue entries — a flow whose lease expired may still
   * be queued waiting for capacity, and deleting its queue entry would cause
   * it to become permanently stuck (the dispatcher would never see it again).
   * Queue entries are only removed via explicit flow completion paths
   * (deleteQueueEntriesForFlow, deleteProcessedQueueEntries) or zombie escape.
   *
   * NOTE: Flow control only manages resources (slots), NOT flow_runs.status.
   * This method does NOT modify flow_runs.
   */
  async releaseExpiredLeases(instanceId: string): Promise<{ releasedSlots: number; deletedQueue: number }> {
    try {
      const nowSec = Math.floor(Date.now() / 1000);

      // Delete expired lease-based slots ONLY — never touch queue entries.
      // A flow with an expired lease may still have a queued entry waiting
      // for capacity; deleting it would make the flow permanently stuck.
      const slotResult = await this.db.exec(
        `DELETE FROM flow_control_slots
         WHERE instance_id = ?
           AND lease_expires_at > 0
           AND lease_expires_at < ?`,
        [instanceId, nowSec],
      );

      // deletedQueue is always 0 — we no longer delete queue entries here.
      // This is intentional: releasing a slot does NOT mean the flow is done.
      if (slotResult.affectedRows > 0) {
        console.warn(
          `[db] FlowControlRepository.releaseExpiredLeases: released ${slotResult.affectedRows} expired-lease slots ` +
          `(queue entries preserved for dispatcher re-dispatch)`,
        );
      }

      return { releasedSlots: slotResult.affectedRows, deletedQueue: 0 };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.releaseExpiredLeases failed: ${msg}`);
      return { releasedSlots: 0, deletedQueue: 0 };
    }
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
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.releaseOrphanedSlots failed: ${msg}`);
      return 0;
    }
  }

  /**
   * Release slots for flows that are stuck in an active status for too long.
   * A flow whose gmt_modified hasn't been updated in `staleSeconds` is
   * considered stale — the controller should have progressed it but hasn't
   * (BUG-25: boundTaskFlow.fail() threw and left the flow stuck forever).
   *
   * IMPORTANT: This method ONLY releases slots (concurrency resources).
   * It does NOT delete queue entries — a stale flow may still have queued nodes
   * waiting for the flow to recover, and deleting queue entries would cause
   * permanent stuckness. Queue entries are only removed via explicit flow
   * completion paths (deleteQueueEntriesForFlow, deleteProcessedQueueEntries)
   * or zombie escape (MAX_REENQUEUE_AGE_SECS).
   *
   * NOTE: Flow control only manages resources (slots), NOT flow_runs.status.
   * This method does NOT mark flows as failed.
   * Returns failedFlows=0 for interface compatibility (legacy slots with lease_expires_at=0).
   */
  async releaseStaleOrphanedSlots(instanceId: string, staleSeconds: number): Promise<{ releasedSlots: number; failedFlows: number }> {
    try {
      const isMySql = this.db.dbType === "mysql" || this.db.dbType === "zdas";

      // In MySQL/ZDAS, flow_runs.gmt_modified is a TIMESTAMP column ('YYYY-MM-DD HH:MM:SS'),
      // so we must use UNIX_TIMESTAMP() to compare with an integer cutoff.
      // In SQLite, gmt_modified is an INTEGER (unix epoch seconds), so direct comparison works.
      const staleCondition = isMySql
        ? `UNIX_TIMESTAMP(r.gmt_modified) < ?`
        : `r.gmt_modified < ?`;
      const cutoff = Math.floor(Date.now() / 1000) - staleSeconds;

      // Find flow_ids that both have slots and are stale in flow_runs
      const staleFlows = await this.db.query<{ flow_id: string }>(
        `SELECT DISTINCT s.flow_id
         FROM flow_control_slots s
         JOIN flow_runs r ON s.flow_id = r.flow_id
         WHERE s.instance_id = ?
           AND r.status IN ('running', 'queued', 'waiting', 'blocked')
           AND ${staleCondition}`,
        [instanceId, cutoff],
      );

      if (staleFlows.length === 0) {
        return { releasedSlots: 0, failedFlows: 0 };
      }

      const flowIds = staleFlows.map((r) => r.flow_id);
      const placeholders = flowIds.map(() => "?").join(", ");

      // Release slots for stale flows — flow control only manages resources, NOT flow_runs.status.
      // Do NOT delete queue entries: stale flows may still have queued nodes that need
      // to be re-dispatched once capacity is available.
      const slotResult = await this.db.exec(
        `DELETE FROM flow_control_slots
         WHERE instance_id = ?
           AND flow_id IN (${placeholders})`,
        [instanceId, ...flowIds],
      );

      // NOTE: We intentionally do NOT DELETE queue entries for stale flows.
      // Deleting queue entries would permanently stick the flow — the dispatcher
      // can no longer see it in the queue. The orphan recovery path in the
      // dispatcher (findOrphanedWaitingFlows) handles stuck flows properly
      // by re-enqueueing them.
      // NOTE: We also do NOT UPDATE flow_runs SET status='failed'.
      // Flow state is owned exclusively by the Controller, not by flow control.

      console.warn(
        `[db] FlowControlRepository.releaseStaleOrphanedSlots: released ${slotResult.affectedRows} slots (stale > ${staleSeconds}s), flowIds=[${flowIds.join(", ")}]. ` +
        `Queue entries preserved. flow_runs.status is NOT modified.`,
      );

      return { releasedSlots: slotResult.affectedRows, failedFlows: 0 };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.releaseStaleOrphanedSlots failed: ${msg}`);
      return { releasedSlots: 0, failedFlows: 0 };
    }
  }

  /**
   * Force-release all slots and queue entries for specific flow IDs.
   * Admin escape hatch for zombie flows.
   *
   * NOTE: Flow control only manages resources (slots), NOT flow_runs.status.
   * This method releases slots and queue entries but does NOT mark flows as failed.
   * Returns { releasedSlots, deletedQueue } — removed failedFlows (flow state is owned
   * by the Controller, not by flow control).
   */
  async forceReleaseForFlowIds(instanceId: string, flowIds: string[]): Promise<{ releasedSlots: number; deletedQueue: number }> {
    if (flowIds.length === 0) {
      return { releasedSlots: 0, deletedQueue: 0 };
    }

    try {
      const placeholders = flowIds.map(() => "?").join(", ");

      const slotResult = await this.db.exec(
        `DELETE FROM flow_control_slots
         WHERE instance_id = ?
           AND flow_id IN (${placeholders})`,
        [instanceId, ...flowIds],
      );

      const queueResult = await this.db.exec(
        `DELETE FROM flow_control_queue
         WHERE instance_id = ?
           AND flow_id IN (${placeholders})`,
        [instanceId, ...flowIds],
      );

      // NOTE: We intentionally do NOT UPDATE flow_runs SET status='failed'.
      // Flow state is owned exclusively by the Controller, not by flow control.

      console.warn(
        `[db] FlowControlRepository.forceReleaseForFlowIds: released ${slotResult.affectedRows} slots, deleted ${queueResult.affectedRows} queue entries, flowIds=[${flowIds.join(", ")}]. ` +
        `Note: flow_runs.status is NOT modified.`,
      );

      return {
        releasedSlots: slotResult.affectedRows,
        deletedQueue: queueResult.affectedRows,
      };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.forceReleaseForFlowIds failed: ${msg}`);
      return { releasedSlots: 0, deletedQueue: 0 };
    }
  }

  async getActiveScopeKeys(instanceId: string): Promise<string[]> {
    try {
      const rows = await this.db.query<{ scope_key: string }>(
        "SELECT DISTINCT scope_key FROM flow_control_slots WHERE instance_id = ?",
        [instanceId],
      );
      return rows.map((r) => r.scope_key);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.getActiveScopeKeys failed: ${msg}`);
      return [];
    }
  }

  async getSlots(instanceId: string, scopeKey?: string): Promise<FlowControlSlotRow[]> {
    try {
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
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.getSlots failed: ${msg}`);
      return [];
    }
  }

  // ── Queue operations ──

  async enqueue(params: {
    instance_id: string;
    scope_key: string;
    flow_id: string;
    node_id: string | null;
    priority: number;
    status: string;
    enqueued_at: number;
    dispatch_after: number | null;
    expires_at: number | null;
    payload: string | null;
  }): Promise<number> {
    try {
      const now = this.db.dialect.now() as number;
      const result = await this.db.exec(
        `INSERT INTO flow_control_queue (instance_id, scope_key, flow_id, node_id, priority, status, enqueued_at, dispatch_after, expires_at, payload, gmt_create, gmt_modified)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        [params.instance_id, params.scope_key, params.flow_id, params.node_id, params.priority,
         params.status, params.enqueued_at, params.dispatch_after, params.expires_at, params.payload,
         now, now],
      );
      return result.insertId ?? 0;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.enqueue failed: ${msg}`);
      return 0;
    }
  }

  async markDispatched(id: number): Promise<boolean> {
    // Legacy: kept for backward compatibility. New code uses deleteQueueEntryById.
    try {
      const result = await this.db.exec(
        "UPDATE flow_control_queue SET status = 'dispatched' WHERE id = ? AND status = 'queued'",
        [id],
      );
      return result.affectedRows > 0;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.markDispatched failed: ${msg}`);
      return false;
    }
  }

  async deleteQueueEntryById(id: number): Promise<boolean> {
    try {
      const result = await this.db.exec(
        "DELETE FROM flow_control_queue WHERE id = ? AND status = 'queued'",
        [id],
      );
      return result.affectedRows > 0;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.deleteQueueEntryById failed: ${msg}`);
      return false;
    }
  }

  async markExpired(id: number): Promise<boolean> {
    try {
      // BUG-15 fix: Only expire entries that are still 'queued'.
      // Without this guard, a 'dispatched' entry could be incorrectly
      // marked as 'expired' if markExpired races with markDispatched.
      const result = await this.db.exec(
        "UPDATE flow_control_queue SET status = 'expired' WHERE id = ? AND status = 'queued'",
        [id],
      );
      return result.affectedRows > 0;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.markExpired failed: ${msg}`);
      return false;
    }
  }

  async expireStaleEntries(instanceId: string): Promise<number> {
    try {
      const now = Math.floor(Date.now() / 1000);
      const result = await this.db.exec(
        "UPDATE flow_control_queue SET status = 'expired' WHERE instance_id = ? AND status = 'queued' AND expires_at IS NOT NULL AND expires_at < ?",
        [instanceId, now],
      );
      return result.affectedRows;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.expireStaleEntries failed: ${msg}`);
      return 0;
    }
  }

  async fetchExpiringItems(instanceId: string): Promise<FlowControlQueueRow[]> {
    try {
      const now = Math.floor(Date.now() / 1000);
      return this.db.query<FlowControlQueueRow>(
        `SELECT * FROM flow_control_queue
         WHERE instance_id = ? AND status = 'queued' AND expires_at IS NOT NULL AND expires_at < ?`,
        [instanceId, now],
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.fetchExpiringItems failed: ${msg}`);
      return [];
    }
  }

  async fetchQueuedItems(instanceId: string, scopeKey: string, limit: number): Promise<FlowControlQueueRow[]> {
    try {
      const now = Math.floor(Date.now() / 1000);
      return this.db.query<FlowControlQueueRow>(
        `SELECT * FROM flow_control_queue
         WHERE instance_id = ? AND scope_key = ? AND status = 'queued'
           AND (dispatch_after IS NULL OR dispatch_after <= ?)
         ORDER BY priority ASC, enqueued_at ASC
         LIMIT ?`,
        [instanceId, scopeKey, now, limit],
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.fetchQueuedItems failed: ${msg}`);
      return [];
    }
  }

  async getScopesWithQueuedItems(instanceId: string): Promise<string[]> {
    try {
      const rows = await this.db.query<{ scope_key: string }>(
        "SELECT DISTINCT scope_key FROM flow_control_queue WHERE instance_id = ? AND status = 'queued'",
        [instanceId],
      );
      return rows.map((r) => r.scope_key);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.getScopesWithQueuedItems failed: ${msg}`);
      return [];
    }
  }

  async deleteProcessedQueueEntries(instanceId: string, olderThan: number): Promise<number> {
    try {
      // H5 fix: In MySQL/ZDAS, flow_control_queue.gmt_modified is a TIMESTAMP column.
      // Comparing TIMESTAMP < integer silently fails (MySQL interprets the integer
      // as a date-like number, not Unix epoch). Use UNIX_TIMESTAMP() for MySQL/ZDAS.
      const isMySql = this.db.dbType === "mysql" || this.db.dbType === "zdas";
      const modifiedCondition = isMySql
        ? "UNIX_TIMESTAMP(gmt_modified) < ?"
        : "gmt_modified < ?";
      const result = await this.db.exec(
        `DELETE FROM flow_control_queue WHERE instance_id = ? AND status IN ('dispatched', 'expired') AND ${modifiedCondition}`,
        [instanceId, olderThan],
      );
      return result.affectedRows;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.deleteProcessedQueueEntries failed: ${msg}`);
      return 0;
    }
  }

  async deleteQueueEntriesForFlow(instanceId: string, flowId: string): Promise<number> {
    try {
      const result = await this.db.exec(
        "DELETE FROM flow_control_queue WHERE instance_id = ? AND flow_id = ?",
        [instanceId, flowId],
      );
      return result.affectedRows;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.deleteQueueEntriesForFlow failed: ${msg}`);
      return 0;
    }
  }

  /**
   * Release all slots for a flow across ALL instances.
   * Used by the UI delete endpoint which doesn't know the instanceId.
   * Flow IDs are globally unique (UUID), so this is safe.
   */
  async releaseAllSlotsForFlowByFlowId(flowId: string): Promise<number> {
    try {
      const result = await this.db.exec(
        "DELETE FROM flow_control_slots WHERE flow_id = ?",
        [flowId],
      );
      return result.affectedRows;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.releaseAllSlotsForFlowByFlowId failed: ${msg}`);
      return 0;
    }
  }

  /**
   * Find slots grouped by session_id for session-liveness zombie detection.
   * Returns an array of { session_id, flow_ids } for sessions that own active slots.
   * Only considers slots with non-null, non-empty session_id.
   */
  async findSlotsGroupedBySession(instanceId: string): Promise<Array<{ session_id: string; flow_ids: string[] }>> {
    try {
      // MySQL: GROUP_CONCAT has a default max length of 1024 — may truncate long lists.
      // SQLite: GROUP_CONCAT works the same way.
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
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.findSlotsGroupedBySession failed: ${msg}`);
      return [];
    }
  }

  /**
   * Delete all queue entries for a flow across ALL instances.
   * Used by the UI delete endpoint which doesn't know the instanceId.
   */
  async deleteQueueEntriesForFlowByFlowId(flowId: string): Promise<number> {
    try {
      const result = await this.db.exec(
        "DELETE FROM flow_control_queue WHERE flow_id = ?",
        [flowId],
      );
      return result.affectedRows;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.deleteQueueEntriesForFlowByFlowId failed: ${msg}`);
      return 0;
    }
  }

  async getScopeStatus(instanceId: string, scopeKey: string): Promise<{ running: number; queued: number }> {
    try {
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
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.getScopeStatus failed: ${msg}`);
      return { running: 0, queued: 0 };
    }
  }

  // ── Management (UI) operations — cross-instance queries ──

  /** Paginated slot listing with optional filters. Returns rows + total count. */
  async listSlots(options: {
    instanceId?: string;
    scopeKey?: string;
    flowId?: string;
    limit?: number;
    offset?: number;
  }): Promise<{ items: FlowControlSlotRow[]; total: number }> {
    try {
      const conditions: string[] = [];
      const params: unknown[] = [];
      if (options.instanceId) {
        conditions.push("instance_id = ?");
        params.push(options.instanceId);
      }
      if (options.scopeKey) {
        conditions.push("scope_key = ?");
        params.push(options.scopeKey);
      }
      if (options.flowId) {
        conditions.push("flow_id = ?");
        params.push(options.flowId);
      }
      const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";

      const countRows = await this.db.query<{ cnt: number }>(
        `SELECT COUNT(*) as cnt FROM flow_control_slots ${where}`,
        params,
      );
      const total = countRows[0]?.cnt ?? 0;

      const items = await this.db.query<FlowControlSlotRow>(
        `SELECT * FROM flow_control_slots ${where} ORDER BY acquired_at DESC LIMIT ? OFFSET ?`,
        [...params, options.limit ?? 50, options.offset ?? 0],
      );
      return { items, total };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.listSlots failed: ${msg}`);
      return { items: [], total: 0 };
    }
  }

  /** Get a single slot by id. Used for cascade delete lookups. */
  async getSlotById(id: number): Promise<FlowControlSlotRow | null> {
    try {
      const rows = await this.db.query<FlowControlSlotRow>(
        "SELECT * FROM flow_control_slots WHERE id = ?",
        [id],
      );
      return rows[0] ?? null;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.getSlotById failed: ${msg}`);
      return null;
    }
  }

  /** Delete a slot by its primary key id. */
  async deleteSlotById(id: number): Promise<boolean> {
    try {
      const result = await this.db.exec("DELETE FROM flow_control_slots WHERE id = ?", [id]);
      return result.affectedRows > 0;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.deleteSlotById failed: ${msg}`);
      return false;
    }
  }

  /** Delete multiple slots by their primary key ids. Returns number of deleted rows. */
  async deleteSlotsByIds(ids: number[]): Promise<number> {
    if (ids.length === 0) return 0;
    try {
      const placeholders = ids.map(() => "?").join(", ");
      const result = await this.db.exec(
        `DELETE FROM flow_control_slots WHERE id IN (${placeholders})`,
        ids,
      );
      return result.affectedRows;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.deleteSlotsByIds failed: ${msg}`);
      return 0;
    }
  }

  /** Delete ALL slots (optionally filtered by instance_id). Returns number of deleted rows. */
  async deleteAllSlots(instanceId?: string): Promise<number> {
    try {
      if (instanceId) {
        const result = await this.db.exec(
          "DELETE FROM flow_control_slots WHERE instance_id = ?",
          [instanceId],
        );
        return result.affectedRows;
      }
      const result = await this.db.exec("DELETE FROM flow_control_slots");
      return result.affectedRows;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.deleteAllSlots failed: ${msg}`);
      return 0;
    }
  }

  /** Count total slots (optionally filtered by instance_id). */
  async countAllSlots(instanceId?: string): Promise<number> {
    try {
      if (instanceId) {
        const rows = await this.db.query<{ cnt: number }>(
          "SELECT COUNT(*) as cnt FROM flow_control_slots WHERE instance_id = ?",
          [instanceId],
        );
        return rows[0]?.cnt ?? 0;
      }
      const rows = await this.db.query<{ cnt: number }>(
        "SELECT COUNT(*) as cnt FROM flow_control_slots",
      );
      return rows[0]?.cnt ?? 0;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.countAllSlots failed: ${msg}`);
      return 0;
    }
  }

  /** Get distinct instance IDs that have active slots. */
  async getSlotInstanceIds(): Promise<string[]> {
    try {
      const rows = await this.db.query<{ instance_id: string }>(
        "SELECT DISTINCT instance_id FROM flow_control_slots ORDER BY instance_id",
      );
      return rows.map((r) => r.instance_id);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.getSlotInstanceIds failed: ${msg}`);
      return [];
    }
  }

  /** Paginated queue listing with optional filters. Returns rows + total count. */
  async listQueueItems(options: {
    instanceId?: string;
    scopeKey?: string;
    flowId?: string;
    status?: string;
    limit?: number;
    offset?: number;
  }): Promise<{ items: FlowControlQueueRow[]; total: number }> {
    try {
      const conditions: string[] = [];
      const params: unknown[] = [];
      if (options.instanceId) {
        conditions.push("instance_id = ?");
        params.push(options.instanceId);
      }
      if (options.scopeKey) {
        conditions.push("scope_key = ?");
        params.push(options.scopeKey);
      }
      if (options.flowId) {
        conditions.push("flow_id = ?");
        params.push(options.flowId);
      }
      if (options.status) {
        conditions.push("status = ?");
        params.push(options.status);
      }
      const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";

      const countRows = await this.db.query<{ cnt: number }>(
        `SELECT COUNT(*) as cnt FROM flow_control_queue ${where}`,
        params,
      );
      const total = countRows[0]?.cnt ?? 0;

      const items = await this.db.query<FlowControlQueueRow>(
        `SELECT * FROM flow_control_queue ${where} ORDER BY enqueued_at DESC LIMIT ? OFFSET ?`,
        [...params, options.limit ?? 50, options.offset ?? 0],
      );
      return { items, total };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.listQueueItems failed: ${msg}`);
      return { items: [], total: 0 };
    }
  }

  /** Get a single queue item by id. Used for cascade delete lookups. */
  async getQueueItemById(id: number): Promise<FlowControlQueueRow | null> {
    try {
      const rows = await this.db.query<FlowControlQueueRow>(
        "SELECT * FROM flow_control_queue WHERE id = ?",
        [id],
      );
      return rows[0] ?? null;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.getQueueItemById failed: ${msg}`);
      return null;
    }
  }

  /** Delete a queue entry by its primary key id. */
  async deleteQueueItemById(id: number): Promise<boolean> {
    try {
      const result = await this.db.exec("DELETE FROM flow_control_queue WHERE id = ?", [id]);
      return result.affectedRows > 0;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.deleteQueueItemById failed: ${msg}`);
      return false;
    }
  }

  /** Delete multiple queue entries by their primary key ids. Returns number of deleted rows. */
  async deleteQueueItemsByIds(ids: number[]): Promise<number> {
    if (ids.length === 0) return 0;
    try {
      const placeholders = ids.map(() => "?").join(", ");
      const result = await this.db.exec(
        `DELETE FROM flow_control_queue WHERE id IN (${placeholders})`,
        ids,
      );
      return result.affectedRows;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.deleteQueueItemsByIds failed: ${msg}`);
      return 0;
    }
  }

  /** Delete ALL queue entries (optionally filtered by instance_id and/or status). Returns number of deleted rows. */
  async deleteAllQueueItems(instanceId?: string, status?: string): Promise<number> {
    try {
      const conditions: string[] = [];
      const params: unknown[] = [];
      if (instanceId) {
        conditions.push("instance_id = ?");
        params.push(instanceId);
      }
      if (status) {
        conditions.push("status = ?");
        params.push(status);
      }
      const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";
      const result = await this.db.exec(
        `DELETE FROM flow_control_queue ${where}`,
        params,
      );
      return result.affectedRows;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.deleteAllQueueItems failed: ${msg}`);
      return 0;
    }
  }

  /** Count queued entries for a specific flow+scope (dedup guard check). */
  async countQueuedByFlowAndScope(instanceId: string, scopeKey: string, flowId: string): Promise<number> {
    try {
      const rows = await this.db.query<{ cnt: number }>(
        "SELECT COUNT(*) as cnt FROM flow_control_queue WHERE instance_id = ? AND scope_key = ? AND flow_id = ? AND status = 'queued'",
        [instanceId, scopeKey, flowId],
      );
      return rows[0]?.cnt ?? 0;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.countQueuedByFlowAndScope failed: ${msg}`);
      return 0;
    }
  }

  /** Count all queued entries for a specific flow across all scopes (rate-limit guard check). */
  async countQueuedByFlowId(flowId: string): Promise<number> {
    try {
      const rows = await this.db.query<{ cnt: number }>(
        "SELECT COUNT(*) as cnt FROM flow_control_queue WHERE flow_id = ? AND status = 'queued'",
        [flowId],
      );
      return rows[0]?.cnt ?? 0;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.countQueuedByFlowId failed: ${msg}`);
      return 0;
    }
  }

  /** Count total queue items (optionally filtered by instance_id and/or status). */
  async countAllQueueItems(instanceId?: string, status?: string): Promise<number> {
    try {
      const conditions: string[] = [];
      const params: unknown[] = [];
      if (instanceId) {
        conditions.push("instance_id = ?");
        params.push(instanceId);
      }
      if (status) {
        conditions.push("status = ?");
        params.push(status);
      }
      const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";
      const rows = await this.db.query<{ cnt: number }>(
        `SELECT COUNT(*) as cnt FROM flow_control_queue ${where}`,
        params,
      );
      return rows[0]?.cnt ?? 0;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.countAllQueueItems failed: ${msg}`);
      return 0;
    }
  }

  /** Get distinct instance IDs that have queue entries. */
  async getQueueInstanceIds(): Promise<string[]> {
    try {
      const rows = await this.db.query<{ instance_id: string }>(
        "SELECT DISTINCT instance_id FROM flow_control_queue ORDER BY instance_id",
      );
      return rows.map((r) => r.instance_id);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.getQueueInstanceIds failed: ${msg}`);
      return [];
    }
  }

  async getQueueItems(instanceId: string, scopeKey?: string, limit?: number): Promise<FlowControlQueueRow[]> {
    try {
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
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[db] FlowControlRepository.getQueueItems failed: ${msg}`);
      return [];
    }
  }
}