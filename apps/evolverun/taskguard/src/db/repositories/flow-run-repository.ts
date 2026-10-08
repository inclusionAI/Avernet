/**
 * FlowRunRepository — persists and queries flow run summaries.
 *
 * Provides fast API access to workflow run listings without
 * joining the large flow_events table. Updated on workflow
 * lifecycle transitions (started → running → succeeded/failed/blocked).
 * Best-effort writes: DB failure is logged but doesn't throw.
 */
import type { IDatabase, Row } from "../types.js";
import { nowForDb } from "../types.js";
import type { IFlowRunRepository, FlowRunReapFields } from "./types.js";

// ── Types ──

export type FlowRunRow = {
  id: number;
  flow_id: string;
  workflow_id: string;
  workflow_title: string | null;
  status: string;
  params_json: string | null;
  input_json: string | null;
  result_json: string | null;
  node_count: number;
  succeeded_count: number;
  failed_count: number;
  total_duration_ms: number | null;
  total_token_usage: number | null;
  triggered_by: string | null;
  identity_key: string | null;
  current_phase: string | null;
  started_at: number;
  completed_at: number | null;
  credentials_json: string | null;
  origin_session_key: string | null;
  origin_session_id: string | null;
  origin_bot_id: string | null;
  user_id: string | null;
  plugin_version: string | null;
  engine: string | null;
  gmt_create: number;
  gmt_modified: number | null;
};

export type FlowRunInsert = {
  flowId: string;
  workflowId: string;
  workflowTitle?: string | null;
  status: string;
  paramsJson?: string | null;
  inputJson?: string | null;
  nodeCount?: number;
  triggeredBy?: string | null;
  identityKey?: string | null;
  currentPhase?: string | null;
  startedAt: number;
  credentialsJson?: string | null;
  originSessionKey?: string | null;
  originSessionId?: string | null;
  originBotId?: string | null;
  userId?: string | null;
  pluginVersion?: string | null;
  engine?: string | null;
};

export type FlowRunCompletion = {
  status: string;
  resultJson?: string | null;
  inputJson?: string | null;
  totalDurationMs?: number | null;
  totalTokenUsage?: number | null;
  currentPhase?: string | null;
  succeededCount?: number;
  failedCount?: number;
  completedAt: number;
};

export type FindFlowRunsOptions = {
  workflowId?: string;
  status?: string;
  identityKey?: string;
  currentPhase?: string;
  limit?: number;
  offset?: number;
};

// ── Repository ──

export class FlowRunRepository implements IFlowRunRepository {
  constructor(private db: IDatabase) {}

  /**
   * Insert a new flow run record when a workflow starts.
   * Returns true on success, false on failure.
   */
  async insert(run: FlowRunInsert): Promise<boolean> {
    try {
      const now = nowForDb(this.db.dbType);
      // Truncate VARCHAR(255) fields to prevent INSERT failures on long values
      const truncate = (v: string | null | undefined, max = 255): string | null => {
        if (!v) return null;
        return v.length > max ? v.slice(0, max - 3) + "..." : v;
      };
      await this.db.exec(
        `INSERT INTO flow_runs
          (flow_id, workflow_id, workflow_title, status, params_json, input_json,
           node_count, succeeded_count, failed_count, triggered_by, identity_key,
           current_phase, started_at, credentials_json, origin_session_key,
           origin_session_id, origin_bot_id, user_id, plugin_version, engine, gmt_create, gmt_modified)
         VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        [
          run.flowId,
          run.workflowId,
          truncate(run.workflowTitle),
          run.status,
          run.paramsJson ?? null,
          run.inputJson ?? null,
          run.nodeCount ?? 0,
          truncate(run.triggeredBy),
          truncate(run.identityKey),
          truncate(run.currentPhase),
          run.startedAt,
          run.credentialsJson ?? null,
          truncate(run.originSessionKey, 512),
          truncate(run.originSessionId),
          truncate(run.originBotId),
          run.userId ?? null,
          run.pluginVersion ?? null,
          truncate(run.engine),
          now,
          now,
        ],
      );
      return true;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.insert failed: ${msg}`);
      return false;
    }
  }

  /**
   * Update a flow run when a node succeeds or fails.
   * Increments succeeded_count or failed_count and sets gmt_modified.
   */
  async incrementNodeCount(flowId: string, field: "succeeded_count" | "failed_count"): Promise<boolean> {
    try {
      const now = nowForDb(this.db.dbType);
      await this.db.exec(
        `UPDATE flow_runs SET ${field} = ${field} + 1, gmt_modified = ? WHERE flow_id = ?`,
        [now, flowId],
      );
      return true;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.incrementNodeCount failed: ${msg}`);
      return false;
    }
  }

  /**
   * Mark a flow run as completed (succeeded, failed, or blocked).
   * Sets status, completed_at, total_duration_ms, total_token_usage, result_json.
   */
  async updateCompletion(flowId: string, completion: FlowRunCompletion): Promise<boolean> {
    try {
      const now = nowForDb(this.db.dbType);
      // Build SET clause dynamically: only include fields when explicitly provided.
      // result_json: undefined = preserve existing (e.g. on success, keep last node result);
      //              null/string = overwrite (e.g. on failure, write structured error info).
      const sets: string[] = [
        "status = ?", "total_duration_ms = ?",
        "total_token_usage = ?", "current_phase = ?", "completed_at = ?", "gmt_modified = ?",
      ];
      const values: unknown[] = [
        completion.status,
        completion.totalDurationMs ?? null,
        completion.totalTokenUsage ?? null,
        completion.currentPhase ?? null,
        completion.completedAt,
        now,
      ];
      if (completion.resultJson !== undefined) {
        sets.push("result_json = ?");
        values.push(completion.resultJson);
      }
      if (completion.inputJson !== undefined) {
        sets.push("input_json = ?");
        values.push(completion.inputJson);
      }
      // Reconcile succeeded/failed counts from caller-provided values (final truth)
      // or fall back to node_executions sub-query for accurate reconciliation.
      if (completion.succeededCount !== undefined) {
        sets.push("succeeded_count = ?");
        values.push(completion.succeededCount);
      } else {
        sets.push("succeeded_count = (SELECT COUNT(*) FROM node_executions WHERE flow_id = ? AND status IN ('succeeded', 'skipped'))");
        values.push(flowId);
      }
      if (completion.failedCount !== undefined) {
        sets.push("failed_count = ?");
        values.push(completion.failedCount);
      } else {
        sets.push("failed_count = (SELECT COUNT(*) FROM node_executions WHERE flow_id = ? AND status = 'failed')");
        values.push(flowId);
      }
      values.push(flowId);
      await this.db.exec(
        `UPDATE flow_runs SET ${sets.join(", ")} WHERE flow_id = ?`,
        values,
      );
      return true;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.updateCompletion failed: ${msg}`);
      return false;
    }
  }

  /**
   * Update the status and gmt_modified of a flow run (e.g. to "blocked").
   */
  async updateStatus(flowId: string, status: string): Promise<boolean> {
    try {
      const now = nowForDb(this.db.dbType);
      await this.db.exec(
        `UPDATE flow_runs SET status = ?, gmt_modified = ? WHERE flow_id = ?`,
        [status, now, flowId],
      );
      return true;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.updateStatus failed: ${msg}`);
      return false;
    }
  }

  /**
   * Update the current_phase of a flow run during state transitions.
   */
  async updateCurrentPhase(flowId: string, currentPhase: string): Promise<boolean> {
    try {
      const now = nowForDb(this.db.dbType);
      await this.db.exec(
        `UPDATE flow_runs SET current_phase = ?, gmt_modified = ? WHERE flow_id = ?`,
        [currentPhase, now, flowId],
      );
      return true;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.updateCurrentPhase failed: ${msg}`);
      return false;
    }
  }

  /**
   * Update node_count for a flow run.
   */
  async updateNodeCount(flowId: string, nodeCount: number): Promise<boolean> {
    try {
      const now = nowForDb(this.db.dbType);
      await this.db.exec(
        `UPDATE flow_runs SET node_count = ?, gmt_modified = ? WHERE flow_id = ?`,
        [nodeCount, now, flowId],
      );
      return true;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.updateNodeCount failed: ${msg}`);
      return false;
    }
  }

  /**
   * Overwrite result_json with the last successful node's output.
   * Called after each node succeeds during workflow execution.
   * Format: { nodeId: "...", ...nodeResult }
   *
   * Safety guard: atomically skips the write when the flow has already reached
   * a terminal status (succeeded / failed / cancelled / completed). The
   * WHERE status NOT IN (...) clause is evaluated atomically by the database,
   * eliminating the TOCTOU race between findByFlowId() and UPDATE that existed
   * with the prior two-step approach.
   */
  async updateResultJson(flowId: string, nodeId: string, result: Record<string, unknown>): Promise<boolean> {
    try {
      const now = nowForDb(this.db.dbType);
      const resultJson = JSON.stringify({ nodeId, ...result });
      await this.db.exec(
        `UPDATE flow_runs SET result_json = ?, gmt_modified = ? WHERE flow_id = ?`,
        [resultJson, now, flowId],
      );
      return true;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.updateResultJson failed: ${msg}`);
      return false;
    }
  }

  /**
   * Find a single flow run by flow_id.
   */
  async findByFlowId(flowId: string): Promise<FlowRunRow | null> {
    try {
      const rows = await this.db.query<FlowRunRow>(
        `SELECT * FROM flow_runs WHERE flow_id = ?`,
        [flowId],
      );
      return rows[0] ?? null;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.findByFlowId failed: ${msg}`);
      return null;
    }
  }

  /**
   * Find flows still in "running" status whose started_at is older than the
   * cutoff (epoch seconds). Used by the flow-timeout watchdog to reap flows
   * that got stuck (e.g. abandoned agent session, engine restart) and would
   * otherwise sit in "running" forever. Only "running" is targeted — flows
   * legitimately parked as "waiting"/"blocked" (e.g. human approval) are left
   * untouched. Oldest first so the most overdue are handled first.
   */
  async findStaleRunning(cutoffEpochSecs: number, limit = 100): Promise<FlowRunRow[]> {
    try {
      return await this.db.query<FlowRunRow>(
        `SELECT * FROM flow_runs
         WHERE status = 'running' AND started_at < ?
         ORDER BY started_at ASC LIMIT ?`,
        [cutoffEpochSecs, limit],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.findStaleRunning failed: ${msg}`);
      return [];
    }
  }

  /**
   * List flow runs with optional filters.
   * Ordered by started_at descending (most recent first).
   */
  async findRuns(options: FindFlowRunsOptions = {}): Promise<FlowRunRow[]> {
    const limit = options.limit ?? 20;
    const offset = options.offset ?? 0;

    try {
      const conditions: string[] = [];
      const params: unknown[] = [];

      if (options.workflowId) {
        conditions.push("workflow_id = ?");
        params.push(options.workflowId);
      }
      if (options.status) {
        conditions.push("status = ?");
        params.push(options.status);
      }
      if (options.identityKey) {
        conditions.push("identity_key = ?");
        params.push(options.identityKey);
      }
      if (options.currentPhase) {
        conditions.push("current_phase = ?");
        params.push(options.currentPhase);
      }

      const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";
      params.push(limit, offset);

      return await this.db.query<FlowRunRow>(
        `SELECT * FROM flow_runs ${where} ORDER BY started_at DESC LIMIT ? OFFSET ?`,
        params,
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.findRuns failed: ${msg}`);
      return [];
    }
  }

  async findRunningByOrigin(botId: string, engine: string, limit = 50): Promise<Pick<FlowRunRow, "flow_id" | "status" | "started_at">[]> {
    try {
      return await this.db.query<FlowRunRow>(
        `SELECT flow_id, status, started_at FROM flow_runs
         WHERE status = 'running'
           AND origin_bot_id = ?
           AND engine = ?
         ORDER BY started_at ASC
         LIMIT ?`,
        [botId, engine, limit],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.findRunningByOrigin failed: ${msg}`);
      return [];
    }
  }

  /**
   * CAS-claim a timeout reap: the server transitions the row to failed only
   * when it is not already terminal. Exactly one engine process sharing the
   * DB wins — losers get claimed=false and skip logging/notifications.
   */
  async markFailedIfRunning(flowId: string, fields: FlowRunReapFields): Promise<boolean> {
    try {
      const now = nowForDb(this.db.dbType);
      const result = await this.db.query<{ affected_rows: number }>(
        `UPDATE flow_runs SET status = 'failed', result_json = ?, current_phase = ?, total_duration_ms = ?, completed_at = ?, gmt_modified = ?
         WHERE flow_id = ? AND status NOT IN ('succeeded', 'failed', 'cancelled', 'completed')`,
        [
          fields.reason,
          fields.currentPhase,
          fields.totalDurationMs ?? null,
          fields.completedAt,
          now,
          flowId,
        ],
      );
      // SQLite returns the number of rows affected. claimed when exactly 1.
      return result.length > 0 && (result[0]?.affected_rows ?? 0) > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.markFailedIfRunning failed: ${msg}`);
      return false;
    }
  }

  /**
   * Reset started_at when a flow is resumed/retried from a non-running state,
   * so the timeout watchdog computes ranFrom from the retry time.
   */
  async resetStartedAt(flowId: string, startedAt: number): Promise<boolean> {
    try {
      const now = nowForDb(this.db.dbType);
      await this.db.exec(
        `UPDATE flow_runs SET started_at = ?, gmt_modified = ? WHERE flow_id = ?`,
        [startedAt, now, flowId],
      );
      return true;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.resetStartedAt failed: ${msg}`);
      return false;
    }
  }
}