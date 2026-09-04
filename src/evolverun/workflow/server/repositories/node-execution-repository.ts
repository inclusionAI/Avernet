/**
 * NodeExecutionRepository — reads and writes node_executions table via raw SQL.
 * No dependency on ClawFlow; shares the same database schema.
 */
import type { IDatabase } from "../db.js";

const MAX_ERROR_LENGTH = 4000;

function truncateError(errorText: string | null | undefined): string | null {
  if (!errorText) return null;
  if (errorText.length <= MAX_ERROR_LENGTH) return errorText;
  return errorText.slice(0, MAX_ERROR_LENGTH - 3) + "...";
}

export type NodeExecutionRow = {
  id: number;
  flow_id: string;
  workflow_id: string;
  node_id: string;
  executor_type: string | null;
  node_title: string | null;
  triggered_by: string | null;
  session_key: string | null;
  session_id: string | null;
  embedded_session_key: string | null;
  status: string;
  attempt: number;
  input_json: string | null;
  output_json: string | null;
  error_text: string | null;
  duration_ms: number | null;
  token_usage_json: string | null;
  system_context_json: string | null;
  // Template-resolved prompt text (agent-family nodes). Written by ClawMind.
  resolved_prompt: string | null;
  version: number;
  started_at: number;
  completed_at: number | null;
  progress_message: string | null;
  gmt_create: number;
};

export type NodeExecutionInsert = {
  flowId: string;
  workflowId: string;
  nodeId: string;
  executorType?: string | null;
  status: string;
  attempt: number;
  inputJson?: string | null;
  outputJson?: string | null;
  errorText?: string | null;
  durationMs?: number | null;
  tokenUsageJson?: string | null;
  nodeTitle?: string | null;
  progressMessage?: string | null;
  sessionKey?: string | null;
  sessionId?: string | null;
  embeddedSessionKey?: string | null;
  systemContextJson?: string | null;
  resolvedPrompt?: string | null;
  version?: number;
  startedAt: number;
  completedAt?: number | null;
};

export type NodeExecutionCompletion = {
  status: string;
  outputJson?: string | null;
  errorText?: string | null;
  durationMs?: number | null;
  tokenUsageJson?: string | null;
  embeddedSessionKey?: string | null;
  systemContextJson?: string | null;
  resolvedPrompt?: string | null;
  completedAt: number;
  /** Expected version for optimistic locking. When provided, the UPDATE will
   *  only succeed if the current version matches (WHERE version = ?). */
  expectedVersion?: number;
};

export type FindNodeExecutionsOptions = {
  nodeId?: string;
  status?: string;
  limit?: number;
  offset?: number;
};

export class NodeExecutionRepository {
  constructor(private db: IDatabase) {}

  // ── Read methods ──

  async findById(id: number): Promise<NodeExecutionRow | null> {
    try {
      const rows = await this.db.query<NodeExecutionRow>(
        "SELECT * FROM node_executions WHERE id = ?",
        [id],
      );
      return rows[0] ?? null;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] NodeExecutionRepository.findById failed: ${msg}`);
      return null;
    }
  }

  async findByFlowId(flowId: string, options: FindNodeExecutionsOptions = {}): Promise<NodeExecutionRow[]> {
    const limit = options.limit ?? 20;
    const offset = options.offset ?? 0;

    try {
      if (options.nodeId && options.status) {
        return await this.db.query<NodeExecutionRow>(
          "SELECT * FROM node_executions WHERE flow_id = ? AND node_id = ? AND status = ? ORDER BY started_at DESC LIMIT ? OFFSET ?",
          [flowId, options.nodeId, options.status, limit, offset],
        );
      }
      if (options.nodeId) {
        return await this.db.query<NodeExecutionRow>(
          "SELECT * FROM node_executions WHERE flow_id = ? AND node_id = ? ORDER BY started_at DESC LIMIT ? OFFSET ?",
          [flowId, options.nodeId, limit, offset],
        );
      }
      if (options.status) {
        return await this.db.query<NodeExecutionRow>(
          "SELECT * FROM node_executions WHERE flow_id = ? AND status = ? ORDER BY started_at DESC LIMIT ? OFFSET ?",
          [flowId, options.status, limit, offset],
        );
      }
      return await this.db.query<NodeExecutionRow>(
        "SELECT * FROM node_executions WHERE flow_id = ? ORDER BY started_at DESC LIMIT ? OFFSET ?",
        [flowId, limit, offset],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] NodeExecutionRepository.findByFlowId failed: ${msg}`);
      return [];
    }
  }

  async findLatestByFlowId(flowId: string): Promise<NodeExecutionRow[]> {
    try {
      return await this.db.query<NodeExecutionRow>(
        `SELECT ne.* FROM node_executions ne
         INNER JOIN (
           SELECT node_id, MAX(attempt) AS max_attempt, MAX(id) AS max_id
           FROM node_executions
           WHERE flow_id = ?
           GROUP BY node_id
         ) latest ON ne.node_id = latest.node_id AND ne.attempt = latest.max_attempt AND ne.id = latest.max_id AND ne.flow_id = ?
         ORDER BY ne.started_at ASC`,
        [flowId, flowId],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] NodeExecutionRepository.findLatestByFlowId failed: ${msg}`);
      return [];
    }
  }

  // ── Write methods (best-effort: catch errors, log, return false/-1) ──

  async insert(exec: NodeExecutionInsert): Promise<number> {
    try {
      const now = this.db.dialect.now();
      const result = await this.db.exec(
        `INSERT INTO node_executions (flow_id, workflow_id, node_id, executor_type, status, attempt, input_json, output_json, error_text, duration_ms, token_usage_json, node_title, progress_message, session_key, session_id, embedded_session_key, system_context_json, resolved_prompt, version, started_at, completed_at, gmt_create, gmt_modified)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        [
          exec.flowId,
          exec.workflowId,
          exec.nodeId,
          exec.executorType ?? null,
          exec.status,
          exec.attempt,
          exec.inputJson ?? null,
          exec.outputJson ?? null,
          truncateError(exec.errorText),
          exec.durationMs ?? null,
          exec.tokenUsageJson ?? null,
          exec.nodeTitle ?? null,
          exec.progressMessage ?? null,
          exec.sessionKey ?? null,
          exec.sessionId ?? null,
          exec.embeddedSessionKey ? exec.embeddedSessionKey.substring(0, 512) : null,
          exec.systemContextJson ?? null,
          exec.resolvedPrompt ?? null,
          exec.version ?? 1,
          exec.startedAt,
          exec.completedAt ?? null,
          now,
          now,
        ],
      );
      return result.insertId ?? -1;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] NodeExecutionRepository.insert failed: ${msg}`);
      return -1;
    }
  }

  async updateCompletion(id: number, completion: NodeExecutionCompletion): Promise<boolean> {
    try {
      const now = this.db.dialect.now();
      const embeddedKey = completion.embeddedSessionKey
        ? completion.embeddedSessionKey.substring(0, 512)
        : undefined;
      const result = await this.db.exec(
        `UPDATE node_executions SET status = ?, output_json = ?, error_text = ?, duration_ms = ?, token_usage_json = ?, embedded_session_key = COALESCE(?, embedded_session_key), system_context_json = COALESCE(?, system_context_json), resolved_prompt = COALESCE(?, resolved_prompt), completed_at = ?, gmt_modified = ? WHERE id = ?`,
        [
          completion.status,
          completion.outputJson ?? null,
          truncateError(completion.errorText),
          completion.durationMs ?? null,
          completion.tokenUsageJson ?? null,
          embeddedKey ?? null,
          completion.systemContextJson ?? null,
          completion.resolvedPrompt ?? null,
          completion.completedAt,
          now,
          id,
        ],
      );
      return result.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] NodeExecutionRepository.updateCompletion failed: ${msg}`);
      return false;
    }
  }

  async updateCompletionByFlowNode(flowId: string, nodeId: string, attempt: number, completion: NodeExecutionCompletion): Promise<boolean> {
    try {
      const now = this.db.dialect.now();
      // Use COALESCE for embedded_session_key: if the completion payload
      // doesn't include it (undefined/null), preserve the existing value
      // that was set during onNodeStart INSERT — otherwise the UPDATE
      // would overwrite it with NULL.
      const embeddedKey = completion.embeddedSessionKey
        ? completion.embeddedSessionKey.substring(0, 512)
        : undefined; // undefined → use COALESCE in SQL

      // Build WHERE clause dynamically — add version check when expectedVersion is provided
      const whereClause = completion.expectedVersion !== undefined
        ? `WHERE flow_id = ? AND node_id = ? AND attempt = ? AND status = 'running' AND version = ?`
        : `WHERE flow_id = ? AND node_id = ? AND attempt = ? AND status = 'running'`;

      const params: any[] = [
        completion.status,
        completion.outputJson ?? null,
        truncateError(completion.errorText),
        completion.durationMs ?? null,
        completion.tokenUsageJson ?? null,
        embeddedKey ?? null,
        completion.systemContextJson ?? null,
        completion.resolvedPrompt ?? null,
        completion.completedAt,
        now,
        flowId,
        nodeId,
        attempt,
      ];

      // Append version parameter when optimistic locking is active
      if (completion.expectedVersion !== undefined) {
        params.push(completion.expectedVersion);
      }

      const result = await this.db.exec(
        `UPDATE node_executions SET status = ?, output_json = ?, error_text = ?, duration_ms = ?, token_usage_json = ?, embedded_session_key = COALESCE(?, embedded_session_key), system_context_json = COALESCE(?, system_context_json), resolved_prompt = COALESCE(?, resolved_prompt), completed_at = ?, version = version + 1, gmt_modified = ? ${whereClause}`,
        params,
      );
      return result.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] NodeExecutionRepository.updateCompletionByFlowNode failed: ${msg}`);
      return false;
    }
  }

  /** Reset all failed node executions for a flow back to pending (for retry-failed). */
  async resetFailedByFlowId(flowId: string): Promise<number> {
    try {
      const now = this.db.dialect.now();
      const result = await this.db.exec(
        `UPDATE node_executions SET status = 'pending', error_text = NULL, duration_ms = NULL, completed_at = NULL, gmt_modified = ? WHERE flow_id = ? AND status = 'failed'`,
        [now, flowId],
      );
      return result.affectedRows;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] NodeExecutionRepository.resetFailedByFlowId failed: ${msg}`);
      return 0;
    }
  }

  async deleteByFlowId(flowId: string): Promise<number> {
    try {
      const result = await this.db.exec(
        "DELETE FROM node_executions WHERE flow_id = ?",
        [flowId],
      );
      return result.affectedRows;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] NodeExecutionRepository.deleteByFlowId failed: ${msg}`);
      return 0;
    }
  }

  async updateProgressMessage(flowId: string, nodeId: string, attempt: number, message: string): Promise<boolean> {
    try {
      const now = this.db.dialect.now();
      const result = await this.db.exec(
        "UPDATE node_executions SET progress_message = ?, gmt_modified = ? WHERE flow_id = ? AND node_id = ? AND attempt = ?",
        [message, now, flowId, nodeId, attempt],
      );
      return result.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] NodeExecutionRepository.updateProgressMessage failed: ${msg}`);
      return false;
    }
  }

  /** Reconcile stale "running" node_executions when a flow reaches a terminal state.
   *  Any node still in "running" status after the flow has completed is marked
   *  as "skipped" (if flow succeeded) or "failed" (if flow failed). */
  async reconcileStaleRunning(flowId: string, flowStatus: string): Promise<number> {
    try {
      const now = this.db.dialect.now();
      const completedAt = Math.floor(Date.now() / 1000);
      const targetStatus = flowStatus === "succeeded" ? "skipped" : "failed";
      const errorText = flowStatus === "succeeded"
        ? "Node still running when workflow succeeded — reconciled to skipped"
        : "Node still running when workflow failed — reconciled to failed";
      const result = await this.db.exec(
        `UPDATE node_executions SET status = ?, error_text = ?, completed_at = ?, gmt_modified = ? WHERE flow_id = ? AND status = 'running'`,
        [targetStatus, errorText, completedAt, now, flowId],
      );
      const changes = result.affectedRows ?? 0;
      if (changes > 0) {
        console.log(`[db] NodeExecutionRepository.reconcileStaleRunning: flowId=${flowId} flowStatus=${flowStatus} reconciled=${changes} rows -> ${targetStatus}`);
      }
      return changes;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] NodeExecutionRepository.reconcileStaleRunning failed: ${msg}`);
      return 0;
    }
  }
}