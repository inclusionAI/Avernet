import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import type { HttpCallbackConfigRow } from './http-callback-config-repository.js';
import type { CompensationEvidence, PreviewInput } from '../services/callback-compensation/types.js';
import { CompensationError } from '../services/callback-compensation/types.js';
import { hash } from '../services/callback-compensation/protection.js';

type Row = Record<string, any>;
export const runFields = `id, flow_id, parent_flow_id, root_flow_id, workflow_id, workflow_title,
  status, params_json, input_json, result_json, node_count, succeeded_count, failed_count,
  total_duration_ms, total_token_usage, triggered_by, identity_key, current_phase,
  started_at, completed_at, origin_session_key, origin_session_id, origin_bot_id, user_id,
  plugin_version, gmt_create, gmt_modified`;
export const nodeFields = `id, flow_id, workflow_id, node_id, executor_type, status, attempt,
  input_json, output_json, error_text, duration_ms, token_usage_json, node_title, triggered_by,
  branch_id, progress_message, session_key, session_id, system_context_json, embedded_session_key,
  started_at, completed_at, gmt_create, gmt_modified`;
export interface RunSnapshot { run: Row; nodes: Row[]; trial: boolean; snapshotHash: string }

// These columns are BIGINT Unix seconds / retry fences, never SQL TIMESTAMPs.
// mysql2 currently returns safe integers by default; server adapters may return canonical
// decimal strings. Accept only exact safe integers, not Date.parse guesses or coercions.
function unsignedInteger(value: unknown): number | null {
  const numeric = typeof value === 'string' && /^(?:0|[1-9]\d*)$/.test(value) ? Number(value) : value;
  return typeof numeric === 'number' && Number.isSafeInteger(numeric) && numeric >= 0 ? numeric : null;
}

/** No catch-to-empty, default epoch, or row caps on evidence. DB errors are fatal. */
export class CallbackCompensationSource {
  constructor(private readonly db: IDatabase) {}
  async configs(workflowId: string): Promise<HttpCallbackConfigRow[]> {
    return this.db.query('SELECT * FROM http_callback_configs WHERE workflow_id = ? ORDER BY config_id', [workflowId]);
  }
  async config(workflowId: string, configId: string): Promise<HttpCallbackConfigRow | null> {
    const rows = await this.db.query<HttpCallbackConfigRow>(
      'SELECT * FROM http_callback_configs WHERE workflow_id = ? AND config_id = ?', [workflowId, configId]);
    return rows.find(r => r.workflow_id === workflowId && r.config_id === configId) ?? null;
  }
  async candidates(workflowId: string, input: PreviewInput): Promise<string[]> {
    if (input.flowIds) {
      const rows = await this.db.query<Row>(`SELECT flow_id FROM flow_runs WHERE workflow_id = ?
        AND flow_id IN (${input.flowIds.map(() => '?').join(',')})`, [workflowId, ...input.flowIds]);
      const present = new Set(rows.map(r => r.flow_id));
      if (input.flowIds.some(id => !present.has(id))) throw new CompensationError('RUN_NOT_FOUND_IN_WORKFLOW', 404);
      return [...input.flowIds];
    }
    // flow_runs.completed_at is BIGINT Unix seconds in BOTH SQLite and MySQL (v61),
    // not a TIMESTAMP; dialect.epochToDb would incorrectly bind a MySQL date string.
    const rows = await this.db.query<Row>(`SELECT flow_id FROM flow_runs WHERE workflow_id = ?
      AND completed_at >= ? AND completed_at <= ? ORDER BY completed_at, flow_id LIMIT 101`,
    [workflowId, Date.parse(input.from!) / 1000, Date.parse(input.to!) / 1000]);
    if (rows.length > 100) throw new CompensationError('RANGE_TOO_LARGE', 422, 'More than 100 runs; narrow the range');
    return rows.map(r => r.flow_id);
  }
  async snapshot(workflowId: string, flowId: string): Promise<RunSnapshot | null> {
    const runs = await this.db.query<Row>(`SELECT ${runFields}, env, retry_epoch, state_json
      FROM flow_runs WHERE workflow_id = ? AND flow_id = ?`, [workflowId, flowId]);
    const run = runs.find(r => r.workflow_id === workflowId && r.flow_id === flowId);
    if (!run) return null;
    // Normalize only a canonical representation, never infer a generation from this fence.
    run.retry_epoch = unsignedInteger(run.retry_epoch) ?? run.retry_epoch;
    const nodes = await this.db.query<Row>(`SELECT ${nodeFields} FROM node_executions
      WHERE flow_id = ? ORDER BY id`, [flowId]);
    const trials = await this.db.query<Row>(`SELECT record_id FROM workflow_repair_release_records
      WHERE record_kind IN ('trial', 'trial_run') AND record_id = ?`, [flowId]);
    const trial = trials.length > 0;
    return { run, nodes, trial, snapshotHash: hash({ run, nodes, trial }) };
  }
  completedAt(snapshot: RunSnapshot): number | null {
    const seconds = unsignedInteger(snapshot.run.completed_at);
    return seconds !== null && Number.isFinite(new Date(seconds * 1000).getTime()) ? seconds : null;
  }
  async attempts(workflowId: string, flowId: string, evidence: CompensationEvidence): Promise<'delivered' | 'review' | null> {
    // All attempts, all ages, all historical versions/URLs of this logical receiver.
    // Independent config-ID OR URL matching intentionally errs toward review/skip.
    const rows = await this.db.query<{ status: string }>(`SELECT status FROM http_callback_logs
      WHERE workflow_id = ? AND flow_id = ? AND notify_event = 'workflow_succeeded'
      AND (config_id IN (${evidence.configIds.map(() => '?').join(',')})
        OR callback_url IN (${evidence.urls.map(() => '?').join(',')}))`,
    [workflowId, flowId, ...evidence.configIds, ...evidence.urls]);
    if (rows.some(r => r.status === 'delivered')) return 'delivered';
    return rows.length ? 'review' : null;
  }
}
