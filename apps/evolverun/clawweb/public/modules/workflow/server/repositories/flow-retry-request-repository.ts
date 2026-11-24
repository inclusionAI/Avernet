/**
 * FlowRetryRequestRepository — mailbox table for cross-engine retry handoff.
 *
 * When a retry command lands on an engine instance that does not hold the
 * flow's TaskFlow state (different host), the requester posts a row here;
 * every engine polls, claims via CAS (exactly one winner), checks its LOCAL
 * openclaw registry for the flow, and either executes (state is local) or
 * releases for the next engine. A claimed row whose lease expires becomes
 * claimable again, so an engine dying mid-execution cannot wedge a request.
 */
import type { IDatabase } from "@avernet/clawweb-shared/server/db";

export type RetryExecutionOptions = { useCurrentDef?: boolean; debug?: boolean; inputOverrides?: Record<string, string> };

export type FlowRetryRequestRow = {
  options_json?: string | null;
  id: number;
  request_id: string;
  flow_id: string;
  node_id: string | null;
  reason: string | null;
  requester: string | null;
  status: string; // pending | claimed | completed | failed
  claim_engine: string | null;
  claimed_at: number | null;
  attempts: number;
  result_message: string | null;
  gmt_create: number;
  gmt_modified: number | null;
};

export type FlowRetryRequestInsert = {
  options?: RetryExecutionOptions;
  requestId: string;
  flowId: string;
  nodeId?: string | null;
  reason?: string | null;
  requester?: string | null;
};

export class RetryRequestCancelledError extends Error {
  constructor(flowId: string) {
    super(`Flow run "${flowId}" is cancelled and cannot be retried`);
    this.name = 'RetryRequestCancelledError';
  }
}

export class FlowRetryRequestRepository {
  constructor(private db: IDatabase) {}

  /**
   * Post a retry request. If the same flow already has an open (pending or
   * claimed) request, returns that one instead of duplicating — duplicate
   * retry commands for one flow must not multiply executions.
   */
  async create(input: FlowRetryRequestInsert): Promise<FlowRetryRequestRow | null> {
    try {
      return await this.db.transaction(async tx => {
        // Lock the same run row as abort and other creators before deduping.
        // Missing runs remain allowed; cancelled runs must not be touched.
        await tx.exec("UPDATE flow_runs SET flow_id = flow_id WHERE flow_id = ? AND status NOT IN ('cancelled', 'canceled')", [input.flowId]);
        const rejectCancelled = async () => {
          const cancelled = await tx.query<{ flow_id: string }>(
            "SELECT flow_id FROM flow_runs WHERE flow_id = ? AND status IN ('cancelled', 'canceled')",
            [input.flowId],
          );
          if (cancelled.length) throw new RetryRequestCancelledError(input.flowId);
        };
        await rejectCancelled();
        const open = await tx.query<FlowRetryRequestRow>(
          `SELECT * FROM flow_retry_requests WHERE flow_id = ? AND status IN ('pending','claimed')
           AND NOT EXISTS (SELECT 1 FROM flow_runs WHERE flow_id = ? AND status IN ('cancelled', 'canceled'))
           ORDER BY id ASC LIMIT 1`,
          [input.flowId, input.flowId],
        );
        if (open[0]) return open[0];
        const now = tx.dialect.now();
        const result = await tx.exec(
          `INSERT INTO flow_retry_requests (request_id, flow_id, node_id, reason, requester, options_json, status, attempts, gmt_create, gmt_modified)
           SELECT ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?
           WHERE NOT EXISTS (SELECT 1 FROM flow_runs WHERE flow_id = ? AND status IN ('cancelled', 'canceled'))`,
          [input.requestId, input.flowId, input.nodeId ?? null, input.reason ?? null, input.requester ?? null, input.options ? JSON.stringify(input.options) : null, now, now, input.flowId],
        );
        if (result.affectedRows === 0) {
          await rejectCancelled();
          throw new Error(`Failed to create retry request for flow ${input.flowId}`);
        }
        const rows = await tx.query<FlowRetryRequestRow>(
          "SELECT * FROM flow_retry_requests WHERE request_id = ?",
          [input.requestId],
        );
        return rows[0] ?? null;
      });
    } catch (error) {
      if (error instanceof RetryRequestCancelledError) throw error;
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRetryRequestRepository.create failed: ${msg} (dbType=${this.db.dbType}, flowId=${input.flowId})`);
      return null;
    }
  }

  /**
   * Claim open requests. Two-step: select candidates, then CAS each one.
   * Only rows where the CAS lands (affectedRows > 0) are returned, so
   * concurrent engine processes never execute the same request twice.
   * `leaseSecs` makes a dead claimer's row claimable again.
   */
  async claimPending(limit: number, leaseSecs: number, claimEngine: string, nowSecs: number, flowIds?: string[], supportsOptions = true): Promise<FlowRetryRequestRow[]> {
    try {
      const leaseCutoff = nowSecs - leaseSecs;
      const flowFilter = flowIds && flowIds.length > 0
        ? `flow_id IN (${flowIds.map(() => "?").join(", ")}) AND `
        : "";
      const candidates = await this.db.query<FlowRetryRequestRow>(
        `SELECT * FROM flow_retry_requests
         WHERE NOT EXISTS (SELECT 1 FROM flow_runs WHERE flow_runs.flow_id = flow_retry_requests.flow_id AND flow_runs.status IN ('cancelled', 'canceled'))
           AND ${supportsOptions ? "" : "(options_json IS NULL OR options_json = '{}') AND "}${flowFilter}(status = 'pending' OR (status = 'claimed' AND claimed_at IS NOT NULL AND claimed_at < ?))
         ORDER BY id ASC LIMIT ?`,
        [...(flowIds ?? []), leaseCutoff, limit],
      );
      const claimed: FlowRetryRequestRow[] = [];
      for (const row of candidates) {
        const result = await this.db.exec(
          `UPDATE flow_retry_requests SET status = 'claimed', claim_engine = ?, claimed_at = ?
           WHERE request_id = ? AND (status = 'pending' OR (status = 'claimed' AND claimed_at IS NOT NULL AND claimed_at < ?))
             AND NOT EXISTS (SELECT 1 FROM flow_runs WHERE flow_runs.flow_id = flow_retry_requests.flow_id AND flow_runs.status IN ('cancelled', 'canceled'))`,
          [claimEngine, nowSecs, row.request_id, leaseCutoff],
        );
        if (result.affectedRows > 0) claimed.push({ ...row, status: "claimed", claim_engine: claimEngine, claimed_at: nowSecs });
      }
      return claimed;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRetryRequestRepository.claimPending failed: ${msg} (dbType=${this.db.dbType})`);
      return [];
    }
  }

  /** Mark a claimed request finished (ok=true → completed, ok=false → failed). */
  async complete(requestId: string, ok: boolean, message: string): Promise<boolean> {
    try {
      const result = await this.db.exec(
        "UPDATE flow_retry_requests SET status = ?, result_message = ? WHERE request_id = ?",
        [ok ? "completed" : "failed", message, requestId],
      );
      return result.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRetryRequestRepository.complete failed: ${msg} (dbType=${this.db.dbType}, requestId=${requestId})`);
      return false;
    }
  }

  /** Hand a claimed request back to the pool (state not on this host). */
  async release(requestId: string): Promise<boolean> {
    try {
      const result = await this.db.exec(
        "UPDATE flow_retry_requests SET status = 'pending', attempts = attempts + 1, claim_engine = NULL, claimed_at = NULL WHERE request_id = ?",
        [requestId],
      );
      return result.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRetryRequestRepository.release failed: ${msg} (dbType=${this.db.dbType}, requestId=${requestId})`);
      return false;
    }
  }
}
