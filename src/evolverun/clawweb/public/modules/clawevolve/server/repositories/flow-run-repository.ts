/**
 * FlowRunRepository — reads and writes flow_runs table via raw SQL.
 * No dependency on ClawFlow; shares the same database schema.
 */
import type { IDatabase } from "@avernet/clawweb-shared/server/db";

function isMissingEvolutionAnalysisStatusColumn(message: string): boolean {
  const normalized = message.toLowerCase();
  return normalized.includes("unknown column 'evolution_analysis_status'")
    || normalized.includes("no such column: evolution_analysis_status");
}

export type FlowRunRow = {
  id: number;
  flow_id: string;
  workflow_id: string;
  workflow_title: string | null;
  status: string;
  triggered_by: string | null;
  params_json: string | null;
  input_json: string | null;
  result_json: string | null;
  node_count: number;
  succeeded_count: number;
  failed_count: number;
  total_duration_ms: number | null;
  total_token_usage: number | null;
  started_at: number;
  completed_at: number | null;
  identity_key: string | null;
  current_phase: string | null;
  credentials_json: string | null;
  origin_session_key: string | null;
  origin_session_id: string | null;
  origin_bot_id: string | null;
  user_id: string | null;
  plugin_version: string | null;
  engine: string | null;
  state_json: string | null;
  workflow_version: number | null;
  workflow_deploy_number: number | null;
  gmt_create: number;
  gmt_modified: number | null;
  evolution_analysis_status: string | null;
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
  workflowVersion?: number | null;
  workflowDeployNumber?: number | null;
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
  statuses?: string[];
  from?: number;
  to?: number;
  limit?: number;
  offset?: number;
  /** Fuzzy-match against input_json (LIKE %value%) */
  inputQuery?: string;
  /** Filter by origin_bot_id owner part (format: botId:botOwnerId) */
  originBotOwnerId?: string;
  /** Filter by origin_bot_id bot part; requires originBotOwnerId */
  originBotId?: string;
};

export type WorkflowTypeRow = {
  workflow_id: string;
  workflow_title: string | null;
  run_count: number;
  last_status: string | null;
  last_run_at: number | null;
};

export type FindWorkflowTypesPageOptions = {
  limit?: number;
  offset?: number;
  status?: string;
  workflowIds?: string[];
};

export type FindWorkflowTypesPageResult = {
  items: WorkflowTypeRow[];
  total: number;
};

export class FlowRunRepository {
  constructor(private db: IDatabase) {}

  // ── Read methods ──

  /**
   * Find a flow run by flow_id, returning only lightweight columns (no TEXT blobs).
   * Prefer this method for status checks, UI listings, and metadata-only access.
   */
  async findByFlowId(flowId: string): Promise<FlowRunRow | null> {
    const tryQuery = async (legacy: boolean): Promise<FlowRunRow[]> => {
      const cols = FlowRunRepository.listColumns(legacy).join(", ");
      return this.db.query<FlowRunRow>(
        `SELECT ${cols} FROM flow_runs WHERE flow_id = ?`,
        [flowId],
      );
    };
    try {
      const rows = await tryQuery(false);
      return rows[0] ?? null;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      if (isMissingEvolutionAnalysisStatusColumn(msg)) {
        try {
          const rows = await tryQuery(true);
          return rows[0] ?? null;
        } catch (error2) {
          const msg2 = error2 instanceof Error ? error2.message : String(error2);
          console.warn(`[db] FlowRunRepository.findByFlowId failed: ${msg2}`);
          return null;
        }
      }
      console.warn(`[db] FlowRunRepository.findByFlowId failed: ${msg}`);
      return null;
    }
  }

  /**
   * Find a flow run by flow_id, including large TEXT columns (params_json, input_json,
   * result_json, credentials_json). Use only when you need the full row data.
   */
  async findFullByFlowId(flowId: string): Promise<FlowRunRow | null> {
    try {
      const rows = await this.db.query<FlowRunRow>(
        "SELECT * FROM flow_runs WHERE flow_id = ?",
        [flowId],
      );
      return rows[0] ?? null;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.findFullByFlowId failed: ${msg}`);
      return null;
    }
  }

  private _buildWhere(
    options: FindFlowRunsOptions,
  ): { sql: string; params: unknown[] } {
    const conds: string[] = [];
    const params: unknown[] = [];
    if (options.workflowId) { conds.push("workflow_id = ?"); params.push(options.workflowId); }
    if (options.statuses?.length) {
      conds.push(`status IN (${options.statuses.map(() => "?").join(",")})`);
      params.push(...options.statuses);
    } else if (options.status) {
      conds.push("status = ?");
      params.push(options.status);
    }
    if (options.from) { conds.push("started_at >= ?"); params.push(options.from); }
    if (options.to) { conds.push("started_at <= ?"); params.push(options.to); }
    if (options.inputQuery) { conds.push("input_json LIKE ?"); params.push(`%${options.inputQuery}%`); }
    // origin_bot_id filtering: format is "botId:botOwnerId" (e.g. "default:461514")
    // NULL/empty origin_bot_id rows are always included (no filtering on them)
    if (options.originBotOwnerId) {
      if (options.originBotId) {
        conds.push("(origin_bot_id = ? OR origin_bot_id = ? OR origin_bot_id IS NULL OR origin_bot_id = '')");
        params.push(`${options.originBotId}:${options.originBotOwnerId}`, options.originBotId);
      } else {
        conds.push("(origin_bot_id LIKE ? OR origin_bot_id = ? OR origin_bot_id IS NULL OR origin_bot_id = '')");
        params.push(`%:${options.originBotOwnerId}`, options.originBotOwnerId);
      }
    }
    const sql = conds.length > 0 ? ` WHERE ${conds.join(" AND ")}` : "";
    return { sql, params };
  }

  /** Columns returned by list queries (excludes large TEXT columns not needed in list views). */
  private static readonly LIST_COLUMNS = [
    "id", "flow_id", "workflow_id", "workflow_title", "status",
    "node_count", "succeeded_count", "failed_count",
    "total_duration_ms", "total_token_usage",
    "started_at", "completed_at",
    "triggered_by", "identity_key", "current_phase",
    "origin_session_key", "origin_session_id", "origin_bot_id",
    "user_id", "plugin_version", "engine",
    "workflow_version", "workflow_deploy_number",
    "gmt_create", "gmt_modified",
    "evolution_analysis_status",
  ] as const;

  /**
   * Return list columns, optionally excluding evolution_analysis_status for legacy DBs
   * where the column has not yet been added by the DBA.
   */
  private static listColumns(legacy = false): readonly string[] {
    if (legacy) {
      return FlowRunRepository.LIST_COLUMNS.filter((c) => c !== "evolution_analysis_status");
    }
    return FlowRunRepository.LIST_COLUMNS;
  }

  async findRuns(options: FindFlowRunsOptions = {}): Promise<FlowRunRow[]> {
    const limit = options.limit ?? 30;
    const offset = options.offset ?? 0;
    const { sql, params } = this._buildWhere(options);

    const tryQuery = async (legacy: boolean): Promise<FlowRunRow[]> => {
      const cols = FlowRunRepository.listColumns(legacy).join(", ");
      return this.db.query<FlowRunRow>(
        `SELECT ${cols} FROM flow_runs${sql} ORDER BY started_at DESC LIMIT ? OFFSET ?`,
        [...params, limit, offset],
      );
    };

    try {
      return await tryQuery(false);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      if (isMissingEvolutionAnalysisStatusColumn(msg)) {
        try {
          return await tryQuery(true);
        } catch (error2) {
          const msg2 = error2 instanceof Error ? error2.message : String(error2);
          console.warn(`[db] FlowRunRepository.findRuns failed: ${msg2}`);
          return [];
        }
      }
      console.warn(`[db] FlowRunRepository.findRuns failed: ${msg}`);
      return [];
    }
  }

  async countRuns(options: FindFlowRunsOptions = {}): Promise<number> {
    const { sql, params } = this._buildWhere(options);

    try {
      const rows = await this.db.query<{ cnt: number }>(
        `SELECT COUNT(*) AS cnt FROM flow_runs${sql}`,
        params,
      );
      return rows[0]?.cnt ?? 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.countRuns failed: ${msg}`);
      return 0;
    }
  }

  // ── Write methods (best-effort: catch errors, log, return false) ──

  async insert(run: FlowRunInsert): Promise<boolean> {
    try {
      const now = this.db.dialect.now();
      await this.db.exec(
        `INSERT INTO flow_runs (flow_id, workflow_id, workflow_title, status, params_json, input_json, node_count, triggered_by, identity_key, current_phase, started_at, credentials_json, origin_session_key, origin_session_id, origin_bot_id, user_id, plugin_version, engine, workflow_version, workflow_deploy_number, gmt_create, gmt_modified)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        [
          run.flowId,
          run.workflowId,
          run.workflowTitle ?? null,
          run.status,
          run.paramsJson ?? null,
          run.inputJson ?? null,
          run.nodeCount ?? 0,
          run.triggeredBy ?? null,
          run.identityKey ?? null,
          run.currentPhase ?? null,
          run.startedAt,
          run.credentialsJson ?? null,
          run.originSessionKey ?? null,
          run.originSessionId ?? null,
          run.originBotId ?? null,
          run.userId ?? null,
          run.pluginVersion ?? null,
          run.engine ?? null,
          run.workflowVersion ?? null,
          run.workflowDeployNumber ?? null,
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

  async incrementNodeCount(flowId: string, field: "succeeded_count" | "failed_count"): Promise<boolean> {
    try {
      const now = this.db.dialect.now();
      const result = await this.db.exec(
        `UPDATE flow_runs SET ${field} = ${field} + 1, gmt_modified = ? WHERE flow_id = ?`,
        [now, flowId],
      );
      return result.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.incrementNodeCount failed: ${msg}`);
      return false;
    }
  }

  async updateCompletion(flowId: string, completion: FlowRunCompletion): Promise<boolean> {
    try {
      const now = this.db.dialect.now();
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
      const result = await this.db.exec(
        `UPDATE flow_runs SET ${sets.join(", ")} WHERE flow_id = ?`,
        values,
      );
      return result.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.updateCompletion failed: ${msg}`);
      return false;
    }
  }

  /**
   * Atomically mark a flow failed only when it is not already terminal.
   * Backs PUT /runs/:flowId/fail-if-running — the engine's timeout watchdog
   * calls this before reaping so that exactly one engine process sharing
   * this DB wins the transition (affectedRows > 0). gmt_modified is left to
   * the DB (ON UPDATE CURRENT_TIMESTAMP / trg_flow_runs_update).
   */
  async markFailedIfRunning(flowId: string, fields: {
    reason: string;
    currentPhase: string;
    totalDurationMs?: number | null;
    completedAt: number;
  }): Promise<boolean> {
    try {
      const result = await this.db.exec(
        `UPDATE flow_runs SET status = 'failed', result_json = ?, current_phase = ?, total_duration_ms = ?, completed_at = ?
         WHERE flow_id = ? AND status NOT IN ('succeeded', 'failed', 'cancelled')`,
        [fields.reason, fields.currentPhase, fields.totalDurationMs ?? null, fields.completedAt, flowId],
      );
      return result.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.markFailedIfRunning failed: ${msg} (dbType=${this.db.dbType}, flowId=${flowId})`);
      return false;
    }
  }

  async updateStatus(flowId: string, status: string): Promise<boolean> {
    try {
      const now = this.db.dialect.now();
      const result = await this.db.exec(
        "UPDATE flow_runs SET status = ?, gmt_modified = ? WHERE flow_id = ?",
        [status, now, flowId],
      );
      return result.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.updateStatus failed: ${msg}`);
      return false;
    }
  }

  async updateAnalysisStatus(flowId: string, status: string): Promise<boolean> {
    try {
      const now = this.db.dialect.now();
      const analyzedAt = ["completed", "analyzing"].includes(status) ? now : null;
      const result = await this.db.exec(
        "UPDATE flow_runs SET evolution_analysis_status = ?, evolution_analyzed_at = ?, gmt_modified = ? WHERE flow_id = ?",
        [status, analyzedAt, now, flowId],
      );
      return result.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.updateAnalysisStatus failed: ${msg}`);
      return false;
    }
  }

  async updateCurrentPhase(flowId: string, currentPhase: string): Promise<boolean> {
    try {
      const now = this.db.dialect.now();
      const result = await this.db.exec(
        "UPDATE flow_runs SET current_phase = ?, gmt_modified = ? WHERE flow_id = ?",
        [currentPhase, now, flowId],
      );
      return result.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.updateCurrentPhase failed: ${msg}`);
      return false;
    }
  }

  async resetStartedAt(flowId: string, startedAt: number): Promise<boolean> {
    try {
      // Do NOT set gmt_modified explicitly — MySQL/ZDAS maintains it via
      // ON UPDATE CURRENT_TIMESTAMP and SQLite via trg_flow_runs_update.
      // Every manually assigned variant (epoch int, ISO/UTC string, local
      // string) was rejected on pre with "Incorrect datetime value for
      // column 'gmt_modified'"; letting the DB maintain it removes the
      // whole failure class.
      const result = await this.db.exec(
        "UPDATE flow_runs SET started_at = ?, completed_at = NULL WHERE flow_id = ?",
        [startedAt, flowId],
      );
      return result.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      // Include full context so any recurrence is diagnosable from the log alone.
      console.warn(`[db] FlowRunRepository.resetStartedAt failed: ${msg} (dbType=${this.db.dbType}, flowId=${flowId}, startedAt=${startedAt})`);
      return false;
    }
  }

  /**
   * Mirror the engine's TaskFlow stateJson (written at flow boundaries:
   * start / waiting / terminal). Same gmt_modified policy as resetStartedAt:
   * the DB maintains it (manual assignment was rejected on pre).
   */
  async updateStateJson(flowId: string, stateJson: string): Promise<boolean> {
    try {
      const result = await this.db.exec(
        "UPDATE flow_runs SET state_json = ? WHERE flow_id = ?",
        [stateJson, flowId],
      );
      return result.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.updateStateJson failed: ${msg} (dbType=${this.db.dbType}, flowId=${flowId}, bytes=${stateJson.length})`);
      return false;
    }
  }

  /** Read back the mirrored TaskFlow stateJson, or null when absent. */
  async getStateJson(flowId: string): Promise<string | null> {
    try {
      const rows = await this.db.query<{ state_json: string | null }>(
        "SELECT state_json FROM flow_runs WHERE flow_id = ?",
        [flowId],
      );
      return rows[0]?.state_json ?? null;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.getStateJson failed: ${msg} (dbType=${this.db.dbType}, flowId=${flowId})`);
      return null;
    }
  }

  /** Decrement failed_count by resetCount and set status back to running (for retry-failed).
   *  Avoids blindly clearing failed_count to 0 which would erase counts for
   *  other failed nodes that are not being retried. */
  async resetFailedForRetry(flowId: string, resetCount: number): Promise<boolean> {
    try {
      const now = this.db.dialect.now();
      const result = await this.db.exec(
        "UPDATE flow_runs SET failed_count = GREATEST(failed_count - ?, 0), status = 'running', completed_at = NULL, gmt_modified = ? WHERE flow_id = ?",
        [resetCount, now, flowId],
      );
      return result.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.resetFailedForRetry failed: ${msg}`);
      return false;
    }
  }

  async deleteByFlowId(flowId: string): Promise<boolean> {
    try {
      const result = await this.db.exec(
        "DELETE FROM flow_runs WHERE flow_id = ?",
        [flowId],
      );
      return result.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.deleteByFlowId failed: ${msg}`);
      return false;
    }
  }

  async updateNodeCount(flowId: string, nodeCount: number): Promise<boolean> {
    try {
      const now = this.db.dialect.now();
      const result = await this.db.exec(
        "UPDATE flow_runs SET node_count = ?, gmt_modified = ? WHERE flow_id = ?",
        [nodeCount, now, flowId],
      );
      return result.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.updateNodeCount failed: ${msg}`);
      return false;
    }
  }

  /** Overwrite result_json with the last successful node's output. Best-effort. */
  async updateResultJson(flowId: string, nodeId: string, result: Record<string, unknown>): Promise<boolean> {
    try {
      const now = this.db.dialect.now();
      const resultJson = JSON.stringify({ nodeId, ...result });
      const res = await this.db.exec(
        "UPDATE flow_runs SET result_json = ?, gmt_modified = ? WHERE flow_id = ?",
        [resultJson, now, flowId],
      );
      return res.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.updateResultJson failed: ${msg}`);
      return false;
    }
  }

  /** Update BaaS session info (origin_bot_id, origin_session_key, origin_session_id) for a flow run. */
  async updateSessionInfo(flowId: string, patch: {
    originBotId?: string | null;
    originSessionKey?: string | null;
    originSessionId?: string | null;
  }): Promise<boolean> {
    try {
      const now = this.db.dialect.now();
      const sets: string[] = [];
      const params: unknown[] = [];
      if (patch.originBotId !== undefined) {
        sets.push("origin_bot_id = ?");
        params.push(patch.originBotId ?? null);
      }
      if (patch.originSessionKey !== undefined) {
        sets.push("origin_session_key = ?");
        params.push(patch.originSessionKey ?? null);
      }
      if (patch.originSessionId !== undefined) {
        sets.push("origin_session_id = ?");
        params.push(patch.originSessionId ?? null);
      }
      if (sets.length === 0) return false;
      sets.push("gmt_modified = ?");
      params.push(now);
      params.push(flowId);
      const result = await this.db.exec(
        `UPDATE flow_runs SET ${sets.join(", ")} WHERE flow_id = ?`,
        params,
      );
      return result.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.updateSessionInfo failed: ${msg}`);
      return false;
    }
  }

  /** Count runs grouped by status for accurate success-rate calculation (avoids pagination skew). */
  async countByStatus(options: FindFlowRunsOptions = {}): Promise<Record<string, number>> {
    const { sql, params } = this._buildWhere(options);
    try {
      const rows = await this.db.query<{ status: string; cnt: number }>(
        `SELECT status, COUNT(*) AS cnt FROM flow_runs${sql} GROUP BY status`,
        params,
      );
      const result: Record<string, number> = {};
      for (const row of rows) {
        result[row.status] = row.cnt;
      }
      return result;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.countByStatus failed: ${msg}`);
      return {};
    }
  }

  /** Query aggregated run stats per workflow from flow_runs. Used to enrich the workflow list. */
  async findRunStatsByWorkflow(): Promise<WorkflowTypeRow[]> {
    try {
      // Use self-join instead of correlated subquery for O(N) instead of O(N*M)
      return await this.db.query<WorkflowTypeRow>(
        `SELECT fr.workflow_id,
                MAX(fr.workflow_title) AS workflow_title,
                COUNT(*) AS run_count,
                latest.status AS last_status,
                MAX(fr.started_at) AS last_run_at
         FROM flow_runs fr
         LEFT JOIN (
           SELECT fr2.workflow_id, fr2.status
           FROM flow_runs fr2
           INNER JOIN (
             SELECT workflow_id, MAX(started_at) AS max_started
             FROM flow_runs
             GROUP BY workflow_id
           ) latest2 ON latest2.workflow_id = fr2.workflow_id AND latest2.max_started = fr2.started_at
         ) latest ON latest.workflow_id = fr.workflow_id
         GROUP BY fr.workflow_id`,
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.findRunStatsByWorkflow failed: ${msg}`);
      return [];
    }
  }

  /**
   * Query workflow-level run statistics with database-side filtering and pagination.
   * A status filter selects workflows that have at least one matching run while the
   * returned counters and latest status still describe all runs of that workflow.
   */
  async findWorkflowTypesPage(
    options: FindWorkflowTypesPageOptions = {},
  ): Promise<FindWorkflowTypesPageResult> {
    const limit = options.limit ?? 50;
    const offset = options.offset ?? 0;

    if (options.workflowIds?.length === 0) {
      return { items: [], total: 0 };
    }

    try {
      const whereClauses: string[] = [];
      const params: unknown[] = [];

      if (options.workflowIds) {
        whereClauses.push(`workflow_id IN (${options.workflowIds.map(() => "?").join(",")})`);
        params.push(...options.workflowIds);
      }
      if (options.status) {
        whereClauses.push(
          "workflow_id IN (SELECT workflow_id FROM flow_runs WHERE status = ?)",
        );
        params.push(options.status);
      }

      const whereSql = whereClauses.length > 0
        ? `WHERE ${whereClauses.join(" AND ")}`
        : "";
      const countRows = await this.db.query<{ cnt: number }>(
        `SELECT COUNT(DISTINCT workflow_id) AS cnt FROM flow_runs ${whereSql}`,
        params,
      );
      const total = countRows[0]?.cnt ?? 0;
      if (total === 0) return { items: [], total: 0 };

      const items = await this.db.query<WorkflowTypeRow>(
        `WITH stats AS (
           SELECT workflow_id,
                  MAX(workflow_title) AS workflow_title,
                  COUNT(*) AS run_count,
                  MAX(started_at) AS last_run_at
           FROM flow_runs
           ${whereSql}
           GROUP BY workflow_id
         ),
         latest AS (
           SELECT DISTINCT workflow_id,
                  FIRST_VALUE(status) OVER (
                    PARTITION BY workflow_id
                    ORDER BY started_at DESC, id DESC
                  ) AS last_status
           FROM flow_runs
           ${whereSql}
         )
         SELECT s.workflow_id,
                s.workflow_title,
                s.run_count,
                l.last_status,
                s.last_run_at
         FROM stats s
         JOIN latest l ON l.workflow_id = s.workflow_id
         ORDER BY s.last_run_at DESC, s.workflow_id ASC
         LIMIT ? OFFSET ?`,
        [...params, ...params, limit, offset],
      );

      return { items, total };
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.findWorkflowTypesPage failed: ${msg}`);
      return { items: [], total: 0 };
    }
  }
}
