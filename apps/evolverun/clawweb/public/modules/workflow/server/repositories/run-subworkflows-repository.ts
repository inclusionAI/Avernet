import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import type { RunViewScope } from "@avernet/clawweb-shared/server/services/run-view-permissions";
import type { AuthorizedRunListQuery, AuthorizedRunList, RunListMetadata, RunIdentity, RunSubworkflowsStore, StoredRunSnapshot, AssociationCandidate, AssociatedRunIdentity, FanoutOriginCandidate } from '../contracts/run-subworkflows.js';

/** Deliberately separate from legacy repositories which swallow DB errors.
 * Only SELECTs, no credentials column and no engine calls. Read failures propagate. */
export class RunSubworkflowsRepository implements RunSubworkflowsStore {
  constructor(private readonly db: IDatabase) {}

  /** Only the two optional projection columns may be omitted on old schemas;
   * every other storage error propagates. */
  private async metadataQuery<T>(columns: readonly string[], suffix: string, params: unknown[]): Promise<T[]> {
    let projection = [...columns];
    for (;;) {
      try { return await this.db.query<T>(`SELECT ${projection.join(", ")} ${suffix}`, params); }
      catch (error) {
        const message = error instanceof Error ? error.message.toLowerCase() : '';
        const missing = ['evolution_analysis_status', 'ordinary_run_marker'].find(column => projection.includes(column)
          && (message === `no such column: ${column}` || message === `unknown column '${column}' in 'field list'`
            || message === `unknown column '${column}'`));
        if (!missing) throw error;
        projection = projection.filter(column => column !== missing);
      }
    }
  }

  async listRuns(query: AuthorizedRunListQuery, scopes: Map<string, RunViewScope> | null): Promise<AuthorizedRunList> {
    const filters: string[] = []; const args: unknown[] = [];
    if (query.workflowId) { filters.push('workflow_id = ?'); args.push(query.workflowId); }
    const statuses = query.statuses?.length ? query.statuses : query.status ? [query.status] : [];
    if (statuses.length) { filters.push(`status IN (${statuses.map(() => '?').join(',')})`); args.push(...statuses); }
    for (const [operator, value] of [['>=', query.from], ['<=', query.to]] as const) {
      if (value !== undefined) { filters.push(`started_at ${operator} ?`); args.push(value); }
    }
    // Searching redacted parent input would be a boolean oracle for hidden item data.
    // Use the DB's structured JSON parser, with a CASE guard for corrupt legacy state.
    const publicInput = `(CASE WHEN state_json IS NULL OR state_json = '' THEN 1
      WHEN JSON_VALID(state_json) THEN CASE WHEN JSON_EXTRACT(state_json, '$.subworkflowFanouts') IS NULL THEN 1 ELSE 0 END
      ELSE 0 END = 1)`;
    if (query.inputQuery) { filters.push(`(${publicInput} AND input_json LIKE ?)`); args.push(`%${query.inputQuery}%`); }
    const escape = (value: string) => value.replace(/[!%_]/g, '!$&');
    if (query.query) {
      filters.push(`(flow_id LIKE ? ESCAPE '!' OR triggered_by LIKE ? ESCAPE '!' OR origin_bot_id LIKE ? ESCAPE '!' OR (${publicInput} AND input_json LIKE ? ESCAPE '!'))`);
      args.push(...Array(4).fill(`%${escape(query.query)}%`));
    }
    if (query.botId) { filters.push("origin_bot_id LIKE ? ESCAPE '!'"); args.push(`${escape(query.botId)}:%`); }
    // One grant per workflow, so batches cannot overlap or double-count rows.
    const grants = scopes === null ? null : [...scopes].filter(([, scope]) => scope !== 'deny').map(([workflowId, scope]) => {
      if (scope === 'all') return { sql: 'workflow_id = ?', args: [workflowId] as unknown[] };
      if (scope === 'deny' || !scope.bots.length) return { sql: '1 = 0', args: [] };
      const predicates = scope.bots.map(bot => bot.ownerId === '*' ? "origin_bot_id LIKE ? ESCAPE '!'" : 'origin_bot_id = ?');
      return { sql: `(workflow_id = ? AND (${predicates.join(' OR ')}))`,
        args: [workflowId, ...scope.bots.map(bot => bot.ownerId === '*' ? `${escape(bot.botId)}:_%` : `${bot.botId}:${bot.ownerId}`)] };
    });
    let rows: RunListMetadata[] = []; let total = 0; const statusCounts: Record<string, number> = {};
    const batches = grants === null ? [null] : Array.from({ length: Math.ceil(grants.length / 100) }, (_, i) => grants.slice(i * 100, (i + 1) * 100));
    for (const batch of batches) {
      const where = [...filters, ...(batch ? [`(${batch.map(grant => grant.sql).join(' OR ')})`] : [])];
      const params = [...args, ...(batch?.flatMap(grant => grant.args) ?? [])];
      const clause = where.length ? ` WHERE ${where.join(' AND ')}` : '';
      const counts = await this.db.query<{ status: string; count: number }>(`SELECT status, COUNT(*) AS count FROM flow_runs${clause} GROUP BY status`, params);
      for (const row of counts) { total += Number(row.count); statusCounts[row.status] = (statusCounts[row.status] ?? 0) + Number(row.count); }
      rows.push(...await this.metadataQuery<RunListMetadata>([
        'flow_id', 'workflow_id', 'workflow_title', 'status', 'node_count', 'succeeded_count',
        'failed_count', 'total_duration_ms', 'total_token_usage', 'started_at', 'completed_at', 'triggered_by',
        'origin_bot_id', 'current_phase', 'workflow_version', 'workflow_deploy_number', 'id', 'identity_key',
        'origin_session_key', 'origin_session_id', 'user_id', 'plugin_version', 'engine', 'gmt_create',
        'gmt_modified', 'evolution_analysis_status'
      ],
        `FROM flow_runs${clause} ORDER BY started_at DESC, flow_id DESC LIMIT ?`, [...params, query.offset + query.limit]));
    }
    rows.sort((a, b) => Number(b.started_at) - Number(a.started_at) || String(b.flow_id).localeCompare(String(a.flow_id)));
    return { runs: rows.slice(query.offset, query.offset + query.limit), total, limit: query.limit, offset: query.offset,
      ...(!statuses.length && !query.inputQuery && !query.query ? { statusCounts } : {}) };
  }

  async readRun(flowId: string): Promise<StoredRunSnapshot | null> {
    const rows = await this.metadataQuery<StoredRunSnapshot>(
      [
        'flow_id', 'workflow_id', 'workflow_title', 'origin_bot_id', 'status', 'state_json',
        'input_json', 'result_json', 'params_json', 'node_count', 'succeeded_count', 'failed_count',
        'total_duration_ms', 'total_token_usage', 'started_at', 'completed_at', 'triggered_by', 'current_phase',
        'workflow_version', 'workflow_deploy_number', 'origin_session_key', 'origin_session_id', 'id', 'identity_key',
        'user_id', 'plugin_version', 'engine', 'gmt_create', 'gmt_modified', 'evolution_analysis_status', 'ordinary_run_marker'
      ],
       `FROM flow_runs WHERE flow_id = ?`, [flowId]);
    return rows[0] ?? null;
  }

  async readIdentities(flowIds: string[]): Promise<RunIdentity[]> {
    const ids = [...new Set(flowIds)];
    const rows: RunIdentity[] = [];
    for (let offset = 0; offset < ids.length; offset += 200) {
      const batch = ids.slice(offset, offset + 200);
      rows.push(...await this.db.query<RunIdentity>(
        `SELECT flow_id, workflow_id, origin_bot_id FROM flow_runs WHERE flow_id IN (${batch.map(() => '?').join(',')})`, batch));
    }
    return rows;
  }
  /** Correlate only explicit trusted-engine metadata. Never return stored state. */
  async readFanoutOriginIdentities(candidates: FanoutOriginCandidate[]): Promise<RunIdentity[]> {
    const result: RunIdentity[] = [];
    for (let offset = 0; offset < candidates.length; offset += 200) {
      const batch = candidates.slice(offset, offset + 200);
      const rows = await this.db.query<RunIdentity & { state_json: string | null }>(
        `SELECT flow_id, workflow_id, origin_bot_id, state_json FROM flow_runs WHERE flow_id IN (${batch.map(() => '?').join(',')})`,
        batch.map(candidate => candidate.nativeFlowId));
      for (const row of rows) {
        const candidate = batch.find(value => value.nativeFlowId === row.flow_id);
        if (!candidate || row.workflow_id !== candidate.workflowId || typeof row.state_json !== 'string') continue;
        let origin: Record<string, unknown>;
        try {
          const state: unknown = JSON.parse(row.state_json);
          if (!state || typeof state !== 'object' || Array.isArray(state)) continue;
          const value = (state as Record<string, unknown>).fanoutOrigin;
          if (!value || typeof value !== 'object' || Array.isArray(value)) continue;
          origin = value as Record<string, unknown>;
        } catch { continue; }
        const keys = ['parentFlowId', 'parentNodeId', 'groupExecutionId', 'itemId', 'itemIndex',
          'attemptNumber', 'childFlowId', 'workflowId'] as const;
        if (keys.some(key => origin[key] !== candidate[key])
          || typeof origin.requestDigest !== 'string' || !origin.requestDigest.trim()
          || (Object.hasOwn(candidate, 'requestDigest') && origin.requestDigest !== candidate.requestDigest)) continue;
        result.push({ flow_id: row.flow_id, workflow_id: row.workflow_id, origin_bot_id: row.origin_bot_id });
      }
    }
    return result;
  }

  /** The request key is the item childFlowId only for a future host that adopts
   * that convention. The present code does not start or register native runs. */
  async readAssociatedIdentities(parent: StoredRunSnapshot, childWorkflowId: string, candidates: AssociationCandidate[]): Promise<AssociatedRunIdentity[]> {
    const result: AssociatedRunIdentity[] = [];
    const marker = parent.ordinary_run_marker;
    for (let offset = 0; offset < candidates.length; offset += 200) {
      const batch = candidates.slice(offset, offset + 200);
      // Always execute the query for candidates, even if the parent marker is
      // missing: missing schema or query failures are errors, not empty matches.
      const rows = await this.db.query<{
        request_key_hash: string; parent_flow_id: string; child_flow_id: string; child_workflow_id: string;
        parent_run_marker: string | null; child_run_marker: string | null;
        actual_parent_id: string; actual_parent_workflow: string; actual_parent_origin: string | null; actual_parent_marker: string | null;
        flow_id: string; workflow_id: string; origin_bot_id: string | null; actual_child_marker: string | null;
      }>(`SELECT a.request_key_hash, a.parent_flow_id, a.child_flow_id, a.child_workflow_id,
          a.parent_run_marker, a.child_run_marker, p.flow_id AS actual_parent_id, p.workflow_id AS actual_parent_workflow,
          p.origin_bot_id AS actual_parent_origin, p.ordinary_run_marker AS actual_parent_marker,
          c.flow_id, c.workflow_id, c.origin_bot_id, c.ordinary_run_marker AS actual_child_marker
        FROM ordinary_run_requests a JOIN flow_runs p ON p.flow_id = a.parent_flow_id
        JOIN flow_runs c ON c.flow_id = a.child_flow_id
        WHERE a.request_key_hash IN (${batch.map(() => '?').join(',')})`, batch.map(c => c.requestKeyHash));
      for (const row of rows) {
        const candidate = batch.find(c => c.requestKeyHash === row.request_key_hash && c.nativeFlowId === row.child_flow_id);
        // Compare exact strings in JS too: SQL collations may be case-insensitive.
        if (!candidate || !marker || !/^[0-9a-f]{64}$/.test(marker)
          || !row.child_run_marker || !/^[0-9a-f]{64}$/.test(row.child_run_marker)
          || row.parent_flow_id !== parent.flow_id || row.actual_parent_id !== parent.flow_id
          || row.actual_parent_workflow !== parent.workflow_id || row.actual_parent_origin !== parent.origin_bot_id
          || row.parent_run_marker !== marker || row.actual_parent_marker !== marker
          || row.child_workflow_id !== childWorkflowId || row.workflow_id !== childWorkflowId
          || row.flow_id !== candidate.nativeFlowId || row.child_run_marker !== row.actual_child_marker) continue;
        result.push({ request_key_hash: row.request_key_hash, parent_run_marker: marker,
          child_run_marker: row.child_run_marker, flow_id: row.flow_id, workflow_id: row.workflow_id, origin_bot_id: row.origin_bot_id });
      }
    }
    return result;
  }

}
