/**
 * NodeExecutionRepository — persists and queries node execution records.
 *
 * Each row represents a single attempt (start → completion) of a node.
 * Retries create new rows with incremented attempt numbers.
 * Best-effort writes: DB failure is logged but doesn't throw.
 */
import type { IDatabase, Row } from "../types.js";
import { nowForDb } from "../types.js";
import type { INodeExecutionRepository } from "./types.js";

// ── Types ──

export type NodeExecutionRow = {
  id: number;
  flow_id: string;
  workflow_id: string;
  node_id: string;
  executor_type: string | null;
  status: string;
  attempt: number;
  input_json: string | null;
  output_json: string | null;
  error_text: string | null;
  duration_ms: number | null;
  token_usage_json: string | null;
  node_title: string | null;
  progress_message: string | null;
  session_key: string | null;
  session_id: string | null;
  branch_id: string | null;
  embedded_session_key: string | null;
  triggered_by: string | null;
  system_context_json: string | null;
  resolved_prompt: string | null;
  version: number;
  started_at: number;
  completed_at: number | null;
  gmt_create: number;
  gmt_modified: number | null;
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
  /** When provided (manual retry reset), overwrites started_at. */
  startedAt?: number | null;
  /** Expected version for optimistic locking. */
  expectedVersion?: number;
};

export type FindNodeExecutionsOptions = {
  nodeId?: string;
  status?: string;
  limit?: number;
  offset?: number;
};

// ── Truncation helper ──

const MAX_ERROR_LENGTH = 4000;

/**
 * Truncate a JSON string to at most `maxBytes` (UTF-8) while keeping it valid
 * JSON, adding `_truncated` / `_originalSize` markers on the top-level object.
 * Returns the original string unchanged when it already fits.
 *
 * Why not slice the raw string: the previous implementation cut the serialized
 * text by a byte budget and closed it with a single `}`. When the cut landed
 * inside a nested value (e.g. `{meta, data:{result:{detail:"<long>"}}}`) the
 * N-1 still-open `{` were never closed, so the stored value was missing `}}`
 * and failed to parse. It also mixed a byte budget with char-based
 * `substring`, emitting oversized strings for multibyte (Chinese) content.
 *
 * This implementation parses the value and trims oversized string leaves
 * (falling back to dropping trailing members for structure-only oversize),
 * so the re-serialized result is always valid JSON ≤ maxBytes.
 */
export function truncateJson(json: string | null | undefined, maxBytes: number): string | null {
  if (!json) return null;
  const originalBytes = Buffer.byteLength(json, "utf-8");
  if (originalBytes <= maxBytes) return json;

  const firstChar = json.trimStart()[0];
  // Only object/array containers can carry a truncation marker and be
  // re-balanced; leave other (non-JSON-container) text untouched.
  if (firstChar !== "{" && firstChar !== "[") return json;

  let parsed: unknown;
  try {
    parsed = JSON.parse(json);
  } catch {
    // Input wasn't valid JSON (unexpected for JSON.stringify output). Wrap a
    // preview rather than emit malformed text.
    const previewBudget = Math.max(0, maxBytes - 96);
    return JSON.stringify({
      _truncated: true,
      _originalSize: originalBytes,
      _preview: json.slice(0, previewBudget),
    });
  }

  const isArray = Array.isArray(parsed);
  const isObject = !isArray && parsed !== null && typeof parsed === "object";
  if (isObject) {
    const obj = parsed as Record<string, unknown>;
    obj._truncated = true;
    obj._originalSize = originalBytes;
  }

  // Iteratively shrink: first by trimming long string leaves (halving the cap
  // each pass, which also fixes the multibyte budget issue since we re-measure
  // actual bytes after each pass), then by dropping trailing members.
  let cap = Math.max(64, Math.floor(maxBytes / 2));
  let result = JSON.stringify(trimStrings(parsed, cap));
  let guard = 0;
  while (Buffer.byteLength(result, "utf-8") > maxBytes && guard < 80) {
    guard++;
    const nextCap = Math.floor(cap / 2);
    if (nextCap >= 1) {
      cap = nextCap;
    } else if (dropTail(parsed, isObject)) {
      // No further string shrinkage possible; shed a trailing member instead.
      cap = 1;
    } else {
      break; // Nothing left to trim/drop.
    }
    result = JSON.stringify(trimStrings(parsed, cap));
  }

  if (Buffer.byteLength(result, "utf-8") > maxBytes) {
    // Absolute last resort: a minimal valid sentinel.
    return JSON.stringify({ _truncated: true, _originalSize: originalBytes });
  }
  return result;
}

/**
 * Return a copy of `value` with string leaves longer than `cap` replaced by a
 * truncated prefix plus a marker. Non-string values are returned as-is.
 */
function trimStrings(value: unknown, cap: number): unknown {
  if (typeof value === "string") {
    if (value.length > cap) return value.slice(0, cap) + "…[trunc]";
    return value;
  }
  if (Array.isArray(value)) return value.map((item) => trimStrings(item, cap));
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(value as Record<string, unknown>)) {
      out[key] = trimStrings((value as Record<string, unknown>)[key], cap);
    }
    return out;
  }
  return value;
}

/**
 * Drop roughly half the trailing elements of an array (or non-marker keys of an
 * object), in place. Halving (rather than one-at-a-time) lets arrays-of-many-
 * small-items converge inside the loop's iteration guard while still keeping a
 * real prefix rather than falling back to the empty sentinel.
 */
function dropTail(value: unknown, isObject: boolean): boolean {
  if (Array.isArray(value)) {
    if (value.length === 0) return false;
    const dropCount = Math.max(1, Math.floor(value.length / 2));
    value.length = value.length - dropCount;
    return true;
  }
  if (isObject && value !== null && typeof value === "object") {
    const keys = Object.keys(value as Record<string, unknown>);
    const droppable = keys.filter((k) => k !== "_truncated" && k !== "_originalSize");
    if (droppable.length === 0) return false;
    const dropCount = Math.max(1, Math.floor(droppable.length / 2));
    for (let i = 0; i < dropCount; i++) {
      delete (value as Record<string, unknown>)[droppable[droppable.length - 1 - i]!];
    }
    return true;
  }
  return false;
}

/**
 * Truncate error text to MAX_ERROR_LENGTH with a suffix indicator.
 */
export function truncateError(text: string | null | undefined): string | null {
  if (!text) return null;
  if (text.length <= MAX_ERROR_LENGTH) return text;
  return text.substring(0, MAX_ERROR_LENGTH - 14) + "... [truncated]";
}

// ── Repository ──

export class NodeExecutionRepository implements INodeExecutionRepository {
  private maxIoBytes: number;

  constructor(
    private db: IDatabase,
    maxIoSizeKb: number = 10,
  ) {
    this.maxIoBytes = maxIoSizeKb * 1024;
  }

  /**
   * Insert a new node execution record (at node start or retry).
   * Returns the insert ID on success, or -1 on failure.
   */
  async insert(exec: NodeExecutionInsert): Promise<{ insertId: number; affectedRows: number }> {
    try {
      const inputJson = truncateJson(exec.inputJson, this.maxIoBytes);
      const outputJson = truncateJson(exec.outputJson, this.maxIoBytes);
      const errorText = truncateError(exec.errorText);
      const now = nowForDb(this.db.dbType);

      // Use INSERT OR IGNORE (SQLite) / INSERT IGNORE (MySQL) to avoid
      // duplicate rows when a manual retry re-executes a node with the same
      // attempt number as a prior failed execution.
      const insertIgnoreSql = this.db.dbType === "sqlite"
        ? `INSERT OR IGNORE INTO node_executions`
        : `INSERT IGNORE INTO node_executions`;

      const result = await this.db.exec(
        `${insertIgnoreSql}
          (flow_id, workflow_id, node_id, executor_type, status, attempt,
           input_json, output_json, error_text, duration_ms, token_usage_json,
           node_title, progress_message, session_key, session_id, embedded_session_key, system_context_json,
           resolved_prompt, started_at, completed_at, gmt_create)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        [
          exec.flowId,
          exec.workflowId,
          exec.nodeId,
          exec.executorType ?? null,
          exec.status,
          exec.attempt,
          inputJson,
          outputJson,
          errorText,
          exec.durationMs ?? null,
          exec.tokenUsageJson ?? null,
          exec.nodeTitle ?? null,
          exec.progressMessage ?? null,
          exec.sessionKey ?? null,
          exec.sessionId ?? null,
          exec.embeddedSessionKey ? exec.embeddedSessionKey.substring(0, 512) : null,
          truncateJson(exec.systemContextJson, this.maxIoBytes),
          exec.resolvedPrompt ?? null,
          exec.startedAt,
          exec.completedAt ?? null,
          now,
        ],
      );
      return { insertId: result.insertId ?? -1, affectedRows: result.affectedRows ?? 0 };
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] NodeExecutionRepository.insert failed: ${msg}`);
      return { insertId: -1, affectedRows: 0 };
    }
  }

  /**
   * Update an existing node execution row on completion (success or failure).
   * Returns true on success, false on failure.
   */
  async updateCompletion(id: number, completion: NodeExecutionCompletion): Promise<boolean> {
    try {
      const outputJson = truncateJson(completion.outputJson, this.maxIoBytes);
      const errorText = truncateError(completion.errorText);
      const systemContextJson = truncateJson(completion.systemContextJson, this.maxIoBytes);

      const embeddedKey = completion.embeddedSessionKey
        ? completion.embeddedSessionKey.substring(0, 512)
        : undefined;
      const result = await this.db.exec(
        `UPDATE node_executions
         SET status = ?, output_json = ?, error_text = ?, duration_ms = ?,
             token_usage_json = ?, embedded_session_key = COALESCE(?, embedded_session_key), system_context_json = ?,
             resolved_prompt = COALESCE(?, resolved_prompt), completed_at = ?
         WHERE id = ?`,
        [
          completion.status,
          outputJson,
          errorText,
          completion.durationMs ?? null,
          completion.tokenUsageJson ?? null,
          embeddedKey ?? null,
          systemContextJson,
          completion.resolvedPrompt ?? null,
          completion.completedAt,
          id,
        ],
      );
      const changes = result.affectedRows ?? 0;
      if (changes === 0) {
        console.warn(`[db] NodeExecutionRepository.updateCompletion matched 0 rows: id=${id}`);
      }
      return changes > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] NodeExecutionRepository.updateCompletion failed: ${msg}`);
      return false;
    }
  }

  /**
   * Update a node execution row on completion, matched by flow+node+attempt instead of row ID.
   * This avoids the race condition where insert hasn't resolved yet when completion fires.
   * Returns true on success, false on failure.
   */
  async updateCompletionByFlowNode(
    flowId: string,
    nodeId: string,
    attempt: number,
    completion: NodeExecutionCompletion,
  ): Promise<boolean> {
    try {
      const outputJson = truncateJson(completion.outputJson, this.maxIoBytes);
      const errorText = truncateError(completion.errorText);
      const systemContextJson = truncateJson(completion.systemContextJson, this.maxIoBytes);

      const embeddedKey = completion.embeddedSessionKey
        ? completion.embeddedSessionKey.substring(0, 512)
        : undefined;

      // Build the WHERE clause — if expectedVersion is provided, use
      // optimistic locking: only update when version matches.
      let whereClause = `WHERE flow_id = ? AND node_id = ? AND attempt = ?`;
      const params: any[] = [];

      if (completion.expectedVersion !== undefined) {
        whereClause += ` AND version = ?`;
      }

      const result = await this.db.exec(
        `UPDATE node_executions
         SET status = ?, output_json = ?, error_text = ?, duration_ms = ?,
             token_usage_json = ?, embedded_session_key = COALESCE(?, embedded_session_key), system_context_json = ?,
             resolved_prompt = COALESCE(?, resolved_prompt), completed_at = ?,
             started_at = COALESCE(?, started_at),
             version = version + 1
         ${whereClause}`,
        [
          completion.status,
          outputJson,
          errorText,
          completion.durationMs ?? null,
          completion.tokenUsageJson ?? null,
          embeddedKey ?? null,
          systemContextJson,
          completion.resolvedPrompt ?? null,
          completion.completedAt,
          completion.startedAt ?? null,
          flowId,
          nodeId,
          attempt,
          ...(completion.expectedVersion !== undefined ? [completion.expectedVersion] : []),
        ],
      );
      const changes = result.affectedRows ?? 0;
      if (changes === 0) {
        console.warn(`[db] NodeExecutionRepository.updateCompletionByFlowNode matched 0 rows: flowId=${flowId} nodeId=${nodeId} attempt=${attempt}${completion.expectedVersion !== undefined ? ` expectedVersion=${completion.expectedVersion}` : ''}`);
      }
      return changes > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] NodeExecutionRepository.updateCompletionByFlowNode failed: ${msg}`);
      return false;
    }
  }

  /**
   * Update the progress_message of a node execution row.
   * Returns true on success, false on failure.
   */
  async updateProgressMessage(flowId: string, nodeId: string, attempt: number, message: string): Promise<boolean> {
    try {
      const now = nowForDb(this.db.dbType);
      const result = await this.db.exec(
        `UPDATE node_executions SET progress_message = ?, gmt_modified = ?
         WHERE flow_id = ? AND node_id = ? AND attempt = ?`,
        [message, now, flowId, nodeId, attempt],
      );
      const changes = result.affectedRows ?? 0;
      if (changes === 0) {
        console.warn(`[db] NodeExecutionRepository.updateProgressMessage matched 0 rows: flowId=${flowId} nodeId=${nodeId} attempt=${attempt}`);
      }
      return changes > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] NodeExecutionRepository.updateProgressMessage failed: ${msg}`);
      return false;
    }
  }

  /**
   * Find all node execution records for a flow, ordered by started_at desc.
   */
  async findByFlowId(flowId: string, options: FindNodeExecutionsOptions = {}): Promise<NodeExecutionRow[]> {
    const limit = options.limit ?? 20;
    const offset = options.offset ?? 0;

    try {
      if (options.nodeId && options.status) {
        return await this.db.query<NodeExecutionRow>(
          `SELECT * FROM node_executions
           WHERE flow_id = ? AND node_id = ? AND status = ?
           ORDER BY started_at DESC LIMIT ? OFFSET ?`,
          [flowId, options.nodeId, options.status, limit, offset],
        );
      }
      if (options.nodeId) {
        return await this.db.query<NodeExecutionRow>(
          `SELECT * FROM node_executions
           WHERE flow_id = ? AND node_id = ?
           ORDER BY started_at DESC LIMIT ? OFFSET ?`,
          [flowId, options.nodeId, limit, offset],
        );
      }
      if (options.status) {
        return await this.db.query<NodeExecutionRow>(
          `SELECT * FROM node_executions
           WHERE flow_id = ? AND status = ?
           ORDER BY started_at DESC LIMIT ? OFFSET ?`,
          [flowId, options.status, limit, offset],
        );
      }
      return await this.db.query<NodeExecutionRow>(
        `SELECT * FROM node_executions
         WHERE flow_id = ?
         ORDER BY started_at DESC LIMIT ? OFFSET ?`,
        [flowId, limit, offset],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] NodeExecutionRepository.findByFlowId failed: ${msg}`);
      return [];
    }
  }

  /**
   * Find node execution records for a specific node within a flow.
   * Returns attempts ordered by attempt number ascending (oldest first).
   */
  async findByFlowAndNode(flowId: string, nodeId: string, limit: number = 50): Promise<NodeExecutionRow[]> {
    try {
      return await this.db.query<NodeExecutionRow>(
        `SELECT * FROM node_executions
         WHERE flow_id = ? AND node_id = ?
         ORDER BY attempt ASC LIMIT ?`,
        [flowId, nodeId, limit],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] NodeExecutionRepository.findByFlowAndNode failed: ${msg}`);
      return [];
    }
  }

  /**
   * Find the latest execution attempt for each node in a flow.
   * Useful for getting current node states.
   */
  async findLatestByFlowId(flowId: string): Promise<NodeExecutionRow[]> {
    try {
      return await this.db.query<NodeExecutionRow>(
        `SELECT ne.* FROM node_executions ne
         INNER JOIN (
           SELECT node_id, MAX(id) AS max_id
           FROM node_executions
           WHERE flow_id = ?
           GROUP BY node_id
         ) latest ON ne.id = latest.max_id AND ne.flow_id = ?
         ORDER BY ne.started_at ASC`,
        [flowId, flowId],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] NodeExecutionRepository.findLatestByFlowId failed: ${msg}`);
      return [];
    }
  }

  /**
   * Reconcile stale "running" node_executions when a flow reaches a terminal state.
   * Any node still in "running" status after the flow has completed should be
   * marked as "failed" with an appropriate error message (e.g. orphaned due to
   * crash, timeout, or race condition). Returns the number of reconciled rows.
   */
  async reconcileStaleRunning(flowId: string, flowStatus: string): Promise<number> {
    try {
      const now = nowForDb(this.db.dbType);
      const completedAt = Math.floor(Date.now() / 1000);
      // Determine the target status for orphaned running nodes:
      // - If the flow succeeded (or completed, legacy value), these nodes were likely
      //   part of a skipped branch or a race condition — mark them as "skipped" so they
      //   don't inflate failed_count.
      // - If the flow failed, mark them as "failed" since they didn't complete.
      // - "completed" is the legacy TaskFlow value, mapped to "succeeded" here.
      const normalizedFlowStatus = flowStatus === "completed" ? "succeeded" : flowStatus;
      if (flowStatus === "completed") {
        console.warn(`[db] NodeExecutionRepository.reconcileStaleRunning: flowStatus is legacy "completed", treating as "succeeded" for flowId=${flowId}`);
      }
      const targetStatus = normalizedFlowStatus === "succeeded" ? "skipped" : "failed";
      const errorText = normalizedFlowStatus === "succeeded"
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