import { matchesRetryEpoch, withRetryEpochRead } from './flow-run-write-fence.js';
import { writeRunMirror } from './flow-run-mirror.js';
import { resumeWithCheckpoint } from './flow-run-retry-checkpoint.js';
import { writeRunStatus } from './flow-run-status.js';
import { completeFlowRun } from './flow-run-completion.js';
import { writeTimeoutFailure, type CompletionOutcome } from './flow-run-timeout-write.js';
import { normalizeFlowRunLineage } from '../services/flow-run-lineage.js';
import { normalizeFlowRunMarkers } from '../services/flow-run-markers.js';
import { OrdinaryRunRequestService } from '../services/ordinary-run-request.js';
import { WorkflowSnapshotRepository } from '@avernet/clawweb-shared/server/services/workflow-snapshot';
import { registerRunRequest } from '../services/run-request-registration.js';
import { productionRunSql, trialRunSql, productionRunsSql, isTrialRun, type RunPurpose } from "@avernet/clawweb-shared/server/services/run-purpose";
/**
 * FlowRunRepository — reads and writes flow_runs table via raw SQL.
 * No dependency on ClawFlow; shares the same database schema.
 */
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import type { ExecutionRecordFlowRun, ExecutionRecordWorkflow } from "../../shared/execution-record.js";
import { ExecutionRecordWorkflowRepository, isMissingExecutionRecordWorkflowAssociation } from "./execution-record-workflow-repository.js";
import { FanoutStateRepository } from "./fanout-state-repository.js";
import { RunLogRepository } from "./run-log-repository.js";
import { registerParentRelatedRun, type ParentRelation } from '../services/parent-related-run-registration.js';

function isMissingEvolutionAnalysisStatusColumn(message: string): boolean {
  const normalized = message.toLowerCase();
  return normalized.includes("unknown column 'evolution_analysis_status'")
    || normalized.includes("no such column: evolution_analysis_status");
}

/** Internal selection for execution-record guards; state is never part of the callback DTO. */
export type ExecutionRecordSnapshot = ExecutionRecordFlowRun & {
  state_json: string | null;
  workflow_version: number | null;
  workflow_deploy_number: number | null;
  workflow_snapshot_json: string | null;
};

export type FlowRunRow = {
  id: number;
  flow_id: string;
  workflow_id: string;
  workflow_title: string | null;
  parent_flow_id: string | null;
  root_flow_id: string | null;
  env: string | null;
  tag: string | null;
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

export type RunOriginScope = {
  workflowId: string;
  originBotId: string | null;
};

export type FlowRunInsert = {
  flowId: string;
  workflowId: string;
  workflowTitle?: string | null;
  parentFlowId?: string | null;
  rootFlowId?: string | null;
  env?: string | null;
  tag?: string | null;
  status: string;
  stateJson?: string | null;
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
  /** Omission means legacy epoch zero; positive attempts must explicitly match. */
  retryEpoch?: number;
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
  purpose?: RunPurpose | "all";
  workflowId?: string;
  /** Permission scope: undefined is unrestricted; an empty list denies all. */
  allowedWorkflowIds?: string[];
  status?: string;
  statuses?: string[];
  from?: number;
  to?: number;
  limit?: number;
  offset?: number;
  /** Fuzzy-match against input_json (LIKE %value%) */
  inputQuery?: string;
  /** Literal substring search across run ID, initiator, origin Bot ID and input JSON. */
  query?: string;
  /**
   * Filter by allowed bot_ids (origin_bot_id format: botId:botOwnerId).
   * undefined = no filter; [] = deny all; ['*'] = all bots.
   * NULL/empty origin_bot_id rows are always included when any bot is allowed.
   */
  allowedBotIds?: string[];
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
  readonly fanoutState: FanoutStateRepository;
  readonly ordinaryRequests: OrdinaryRunRequestService;
  readonly runLogs: RunLogRepository;

  constructor(private db: IDatabase) {
    this.fanoutState = new FanoutStateRepository(db);
    this.runLogs = new RunLogRepository(db);
    this.ordinaryRequests = new OrdinaryRunRequestService(db, async (tx, run) => {
      if (!await new FlowRunRepository(tx).insert(run)) throw new Error("Run persistence failed");
    });
  }

  registerParentRelatedRun(body: Record<string, unknown>, run: FlowRunInsert, relation: ParentRelation) {
    return registerParentRelatedRun(this.db, body, run, relation);
  }

  insertForRequest(body: Record<string, unknown>, run: FlowRunInsert): Promise<'created' | 'existing' | null> {
    return registerRunRequest(this.db, body, async tx => {
      if (!await new FlowRunRepository(tx).insert(run)) throw new Error('Run persistence failed');
    });
  }

  private flowIdPredicate(flowId: string, expectedScope?: RunOriginScope) {
    const conditions = ["flow_id = ?"];
    const params = [flowId];
    if (expectedScope) {
      // Scope IDs are identities, not text to compare under the table's collation.
      const exact = (column: "workflow_id" | "origin_bot_id") => this.db.dialect.driver === "mysql2"
        ? `BINARY ${column} = BINARY ?`
        : `${column} COLLATE BINARY = ?`;
      conditions.push(exact("workflow_id"));
      params.push(expectedScope.workflowId);
      if (expectedScope.originBotId === null) {
        conditions.push("origin_bot_id IS NULL");
      } else {
        conditions.push(exact("origin_bot_id"));
        params.push(expectedScope.originBotId);
      }
    }
    return { sql: conditions.join(" AND "), params };
  }

  // ── Read methods ──

  /** Propagate lookup errors so conflict/not-found classification cannot hide database failures. */
  async findStatusByFlowId(flowId: string): Promise<{ status: string } | null> {
    const rows = await this.db.query<{ status: string }>(
      "SELECT status FROM flow_runs WHERE flow_id = ?",
      [flowId],
    );
    return rows[0] ?? null;
  }

  /** Strict, small control snapshot. List rows intentionally do not expose retry_epoch. */
  async findForAbort(flowId: string): Promise<{
    workflow_id: string; status: string; completed_at: number | null; retry_epoch?: number | null;
  } | null> {
    type Run = { workflow_id: string; status: string; completed_at: number | null; retry_epoch?: number | null };
    return withRetryEpochRead(this.db, async (db, epoch) => (await db.query<Run>(
      `SELECT workflow_id, status, completed_at${epoch ? ', retry_epoch' : ''} FROM flow_runs WHERE flow_id = ?`,
      [flowId]))[0] ?? null, async (_db, saved) => saved);
  }

  /**
   * Find a flow run by flow_id, returning only lightweight columns (no TEXT blobs).
   * Prefer this method for status checks, UI listings, and metadata-only access.
   */
  async findByFlowId(flowId: string, expectedScope?: RunOriginScope): Promise<FlowRunRow | null> {
    const { sql, params } = this.flowIdPredicate(flowId, expectedScope);
    const tryQuery = async (legacy: boolean): Promise<FlowRunRow[]> => {
      const cols = FlowRunRepository.listColumns(legacy).join(", ");
      return this.db.query<FlowRunRow>(`SELECT ${cols} FROM flow_runs WHERE ${sql}`, params);
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
          if (expectedScope) throw error2;
          return null;
        }
      }
      console.warn(`[db] FlowRunRepository.findByFlowId failed: ${msg}`);
      if (expectedScope) throw error;
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
      if (!rows[0]) return null;
      // SELECT * must retain legacy fields, but internal incarnation markers
      // are never part of a flow-run response or a repository caller's data.
      const { ordinary_run_marker: _ordinaryMarker, parent_run_marker: _parentMarker,
        child_run_marker: _childMarker, ...run } = rows[0] as FlowRunRow & Record<string, unknown>;
      return run as FlowRunRow;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.findFullByFlowId failed: ${msg}`);
      return null;
    }
  }

  /** Callback fields plus same-snapshot internal state. Never reads credentials; failures propagate. */
  async findExecutionRecordByFlowIdStrict(flowId: string): Promise<ExecutionRecordSnapshot | null> {
    const select = (association: string) => this.db.query<ExecutionRecordSnapshot>(
      `SELECT id, flow_id, workflow_id, workflow_title, status, params_json, input_json,
              result_json, node_count, succeeded_count, failed_count, total_duration_ms,
              total_token_usage, triggered_by, identity_key, current_phase, started_at,
              completed_at, NULL AS credentials_json, origin_session_key, origin_session_id,
              origin_bot_id, user_id, plugin_version, gmt_create, gmt_modified, state_json,
              workflow_version, workflow_deploy_number, ${association}
       FROM flow_runs WHERE flow_id = ?`,
      [flowId],
    );
    try { return (await select("workflow_snapshot_json"))[0] ?? null; }
    catch (error) {
      if (!isMissingExecutionRecordWorkflowAssociation(error)) throw error;
      return (await select("NULL AS workflow_snapshot_json"))[0] ?? null;
    }
  }

  /** Call only after token, identity and same-snapshot structural guards. */
  readExecutionRecordWorkflow(run: ExecutionRecordSnapshot): Promise<ExecutionRecordWorkflow> {
    return new ExecutionRecordWorkflowRepository(this.db).read(run);
  }

  isTrialRun(flowId: string): Promise<boolean> { return isTrialRun(this.db, flowId); }

  private _buildWhere(
    options: FindFlowRunsOptions,
  ): { sql: string; params: unknown[] } {
    const conds: string[] = options.purpose && options.purpose !== "all" ? [options.purpose === "trial" ? trialRunSql("flow_runs.flow_id") : productionRunSql("flow_runs.flow_id")] : [];
    const params: unknown[] = [];
    if (options.workflowId) { conds.push("workflow_id = ?"); params.push(options.workflowId); }
    if (options.allowedWorkflowIds !== undefined) {
      if (options.allowedWorkflowIds.length === 0) {
        conds.push("1 = 0");
      } else {
        conds.push(`workflow_id IN (${options.allowedWorkflowIds.map(() => "?").join(",")})`);
        params.push(...options.allowedWorkflowIds);
      }
    }
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
    if (options.query?.trim()) {
      const pattern = `%${options.query.trim().replace(/[!%_]/g, "!$&")}%`;
      conds.push("(flow_id LIKE ? ESCAPE '!' OR triggered_by LIKE ? ESCAPE '!' OR origin_bot_id LIKE ? ESCAPE '!' OR input_json LIKE ? ESCAPE '!')");
      params.push(pattern, pattern, pattern, pattern);
    }
    // allowedBotIds filtering: origin_bot_id format is "botId:botOwnerId" (e.g. "default:461514")
    // NULL/empty origin_bot_id rows are always included when any bot is allowed.
    if (options.allowedBotIds !== undefined) {
      if (options.allowedBotIds.length === 0) {
        conds.push("1 = 0");
      } else if (!options.allowedBotIds.includes("*")) {
        const patterns = options.allowedBotIds.map((id) => `${id}:%`);
        const placeholders = patterns.map(() => "origin_bot_id LIKE ?").join(" OR ");
        conds.push(`(${placeholders} OR origin_bot_id IS NULL OR origin_bot_id = '')`);
        params.push(...patterns);
      }
    }
    const sql = conds.length > 0 ? ` WHERE ${conds.join(" AND ")}` : "";
    return { sql, params };
  }

  /** Columns returned by list queries (excludes large TEXT columns not needed in list views). */
  private static readonly LIST_COLUMNS = [
    "id", "flow_id", "workflow_id", "workflow_title", "status",
    "parent_flow_id", "root_flow_id",
    "node_count", "succeeded_count", "failed_count",
    "total_duration_ms", "total_token_usage",
    "started_at", "completed_at",
    "triggered_by", "identity_key", "current_phase",
    "origin_session_key", "origin_session_id", "origin_bot_id",
    "user_id", "plugin_version", "engine", "env", "tag",
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
      const markers = normalizeFlowRunMarkers(run);
      const lineage = normalizeFlowRunLineage(run);
      // The run must not survive a failed snapshot read/write. Transaction-bound
      // repositories reuse their caller's transaction for purpose registration.
      return await this.db.transaction(async tx => {
        const now = tx.dialect.now();
        await tx.exec(
          `INSERT INTO flow_runs (flow_id, workflow_id, workflow_title, status, state_json, params_json, input_json, node_count, triggered_by, identity_key, current_phase, started_at, credentials_json, origin_session_key, origin_session_id, origin_bot_id, user_id, plugin_version, engine, workflow_version, workflow_deploy_number, env, tag, parent_flow_id, root_flow_id, gmt_create, gmt_modified)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
          [
            run.flowId,
            run.workflowId,
            run.workflowTitle ?? null,
            run.status,
            run.stateJson ?? null,
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
            markers.env,
            markers.tag,
            lineage.parent_flow_id,
            lineage.root_flow_id,
            now,
            now,
          ],
        );
        await new WorkflowSnapshotRepository(tx).associateRun(run.flowId);
        return true;
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.insert failed: ${msg}`);
      return false;
    }
  }

  async incrementNodeCount(flowId: string, field: "succeeded_count" | "failed_count"): Promise<boolean> {
    try {
      const now = this.db.dialect.now();
      // Completion owns the final counts; late node events must not change them.
      const result = await this.db.exec(
        `UPDATE flow_runs SET ${field} = ${field} + 1, gmt_modified = ? WHERE flow_id = ? AND completed_at IS NULL AND LOWER(TRIM(status)) NOT IN ('succeeded', 'failed', 'cancelled', 'canceled', 'completed')`,
        [now, flowId],
      );
      return result.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.incrementNodeCount failed: ${msg}`);
      return false;
    }
  }

  /** Compatibility API: accepted identical settlements remain successful boolean writes. */
  async updateCompletion(flowId: string, completion: FlowRunCompletion): Promise<boolean> {
    return await this.updateCompletionOutcome(flowId, completion) !== 'rejected';
  }

  /** Routes distinguish an actual transition from an idempotent settlement acknowledgement. */
  updateCompletionOutcome(flowId: string, completion: FlowRunCompletion): Promise<CompletionOutcome> {
    return completeFlowRun(this.db, flowId, completion);
  }

  /** The engine decides timeout; only the first unsettled snapshot can be claimed. */
  async markFailedIfRunning(flowId: string, fields: {
    reason: string;
    currentPhase?: string | null;
    totalDurationMs?: number | null;
    completedAt: number;
    retryEpoch?: number;
  }): Promise<boolean> {
    return await writeTimeoutFailure(this.db, flowId, { ...fields, resultJson: fields.reason }, completedAt => {
      const sets = ["status = 'failed'", 'result_json = ?', 'total_duration_ms = ?', 'completed_at = ?'];
      const values: unknown[] = [fields.reason, fields.totalDurationMs ?? null, completedAt];
      if (fields.currentPhase !== undefined) { sets.push('current_phase = ?'); values.push(fields.currentPhase); }
      return { sets, values };
    }) === 'updated';
  }

  async updateStatus(flowId: string, status: string, retryEpoch?: number): Promise<boolean> {
    return writeRunStatus(this.db, flowId, status, retryEpoch);
  }

  /** Abort only the selected active generation; omitted epoch is legacy zero. Errors propagate. */
  async abort(flowId: string, reason?: string, retryEpoch?: number): Promise<boolean> {
    type Parent = { status: string; completed_at: number | null; retry_epoch?: number | null };
    return withRetryEpochRead(this.db, async (db, epoch) => (await db.query<Parent>(
      `SELECT status, completed_at${epoch ? ', retry_epoch' : ''} FROM flow_runs WHERE flow_id = ?`, [flowId]))[0], async (db, saved) => {
      if (!saved || !['running', 'waiting', 'blocked', 'pending', 'queued', 'postActionsRunning'].includes(saved.status)
        || saved.completed_at !== null || !matchesRetryEpoch(saved, retryEpoch)) return false;
      const exact = (column: string) => db.dialect.driver === 'mysql2'
        ? `BINARY ${column} = BINARY ?` : `${column} COLLATE BINARY = ?`;
      const completedAt = Math.floor(Date.now() / 1000);
      const resultJson = JSON.stringify({ aborted: true, reason: reason ?? "用户手动中止", abortedAt: completedAt });
      const params: unknown[] = [completedAt, resultJson, db.dialect.now(), flowId, saved.status];
      const conditions = [exact('flow_id'), exact('status'), 'completed_at IS NULL'];
      if (Object.hasOwn(saved, 'retry_epoch')) {
        conditions.push(saved.retry_epoch === null ? 'retry_epoch IS NULL' : 'retry_epoch = ?');
        if (saved.retry_epoch !== null) params.push(saved.retry_epoch);
      }
      const result = await db.exec(`UPDATE flow_runs SET status = 'cancelled', completed_at = ?, result_json = ?, gmt_modified = ?
        WHERE ${conditions.join(' AND ')}`, params);
      return result.affectedRows > 0;
    });
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

  async updateCurrentPhase(flowId: string, currentPhase: string, retryEpoch?: number): Promise<boolean> {
    return writeRunMirror(this.db, flowId, 'current_phase', currentPhase, retryEpoch);
  }

  /** @deprecated Retry admission preserves started_at; legacy mutations always fail closed. */
  async resetStartedAt(flowId: string, _startedAt: number): Promise<boolean> {
    await this.findStatusByFlowId(flowId); // Preserve read errors; never touch run timestamps.
    return false;
  }

  /**
   * Mirror the engine's TaskFlow stateJson (written at flow boundaries:
   * start / waiting / terminal). Admission and any embedded sync use the
   * captured execution epoch; terminal mirrors cannot rewrite saved settlement.
   */
  async updateStateJson(flowId: string, stateJson: string, retryEpoch?: number): Promise<boolean> {
    return writeRunMirror(this.db, flowId, 'state_json', stateJson, retryEpoch);
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

  /**
   * @deprecated No generation-bearing admission: never reset nodes or reopen a run.
   * Kept as a read-only compatibility entry point so callers get an honest
   * conflict/not_found. Use coordinated resumeForRetry with a captured epoch+1.
   */
  async resetFailedForRetry(
    flowId: string,
    _resetFailedNodes: (tx: IDatabase) => Promise<number>,
  ): Promise<{ status: 'not_found' } | { status: 'conflict' }> {
    const rows = await this.db.query('SELECT flow_id FROM flow_runs WHERE flow_id = ?', [flowId]);
    return { status: rows.length ? 'conflict' : 'not_found' };
  }

  /** Resume a locally authorized manual retry without rewriting node history or started_at. */
  async resumeForRetry(flowId: string, retryEpoch: number, stateJson?: string): Promise<'resumed' | 'not_found' | 'conflict'> {
    if (!Number.isSafeInteger(retryEpoch) || retryEpoch < 1) throw new TypeError('retry_epoch must be a positive safe integer');
    if (stateJson !== undefined) return resumeWithCheckpoint(this.db, flowId, retryEpoch, stateJson);
    const now = this.db.dialect.now();
    const result = await this.db.exec(
      `UPDATE flow_runs SET status = 'running', retry_epoch = ?, completed_at = NULL, gmt_modified = ?
       WHERE flow_id = ? AND LOWER(TRIM(status)) NOT IN ('cancelled', 'canceled') AND COALESCE(retry_epoch, 0) = ?`,
      [retryEpoch, now, flowId, retryEpoch - 1],
    );
    if (result.affectedRows > 0) return 'resumed';
    const rows = await this.db.query<{ status: string; retry_epoch: number | null }>('SELECT status, retry_epoch FROM flow_runs WHERE flow_id = ?', [flowId]);
    if (!rows.length) return 'not_found';
    // A lost response may replay the same retry; an older or skipped epoch must not reopen it.
    return rows[0].status === 'running' && rows[0].retry_epoch === retryEpoch ? 'resumed' : 'conflict';
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

  /** @deprecated Only fenced completion owns the final result; never write node output here. */
  async updateResultJson(flowId: string, _nodeId: string, _result: Record<string, unknown>): Promise<boolean> {
    await this.findStatusByFlowId(flowId); // Read-only even for nonterminal epoch-zero rows.
    return false;
  }

  /**
   * Update BaaS session info, optionally only if the stored origin still matches.
   * With a scope, DB errors propagate so a failed write is distinguishable from a denied one.
   */
  async updateSessionInfo(flowId: string, patch: {
    originBotId?: string | null;
    originSessionKey?: string | null;
    originSessionId?: string | null;
  }, expectedScope?: RunOriginScope): Promise<boolean> {
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
      const where = this.flowIdPredicate(flowId, expectedScope);
      const result = await this.db.exec(
        `UPDATE flow_runs SET ${sets.join(", ")} WHERE ${where.sql}`,
        [...params, now, ...where.params],
      );
      return result.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowRunRepository.updateSessionInfo failed: ${msg}`);
      if (expectedScope) throw error;
      return false;
    }
  }

  /** Count runs grouped by status for accurate success-rate calculation (avoids pagination skew). */
  async countByStatus(options: FindFlowRunsOptions = {}): Promise<Record<string, number>> {
    const { sql, params } = this._buildWhere({ ...options, purpose: options.purpose ?? "production" });
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
         FROM ${productionRunsSql()} fr
         LEFT JOIN (
           SELECT fr2.workflow_id, fr2.status
           FROM ${productionRunsSql()} fr2
           INNER JOIN (
             SELECT workflow_id, MAX(started_at) AS max_started
             FROM ${productionRunsSql()} flow_runs
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
          `workflow_id IN (SELECT workflow_id FROM ${productionRunsSql()} flow_runs WHERE status = ?)`,
        );
        params.push(options.status);
      }

      const whereSql = whereClauses.length > 0
        ? `WHERE ${whereClauses.join(" AND ")}`
        : "";
      const countRows = await this.db.query<{ cnt: number }>(
        `SELECT COUNT(DISTINCT workflow_id) AS cnt FROM ${productionRunsSql()} flow_runs ${whereSql}`,
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
           FROM ${productionRunsSql()} flow_runs
           ${whereSql}
           GROUP BY workflow_id
         ),
         latest AS (
           SELECT DISTINCT workflow_id,
                  FIRST_VALUE(status) OVER (
                    PARTITION BY workflow_id
                    ORDER BY started_at DESC, id DESC
                  ) AS last_status
           FROM ${productionRunsSql()} flow_runs
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
