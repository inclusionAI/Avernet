import Database from 'better-sqlite3';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import { FlowRunRepository, type ExecutionRecordSnapshot } from '../flow-run-repository.js';

export const executedSpec = { id: 'wf', title: 'Executed v1', nodes: [{ id: 'step', executor: { type: 'agent', prompt: 'v1' } }],
  business: { zero: 0, flag: false, blank: '', nullable: null, quoted: 'true', list: ['one', 2] } };
export const commit = 'a'.repeat(40);
export function workflowRun(overrides: Partial<ExecutionRecordSnapshot> = {}): ExecutionRecordSnapshot {
  return {
    id: 1, flow_id: 'flow', workflow_id: 'wf', workflow_title: null, status: 'succeeded',
    params_json: null, input_json: null, result_json: null, node_count: 1, succeeded_count: 1, failed_count: 0,
    total_duration_ms: null, total_token_usage: null, triggered_by: null, identity_key: null, current_phase: null,
    started_at: 1, completed_at: 2, credentials_json: null, origin_session_key: null, origin_session_id: null,
    origin_bot_id: null, user_id: null, plugin_version: null, gmt_create: 1, gmt_modified: null,
    state_json: null, workflow_version: 1, workflow_deploy_number: 10, workflow_snapshot_json: null, ...overrides,
  };
}

export function executionRecordWorkflowFixture(associationColumn = true) {
  const raw = new Database(':memory:');
  const db = new SqliteDatabase(raw);
  // No credentials column at all: the execution-record selector must never read it.
  const run = workflowRun();
  const columns = Object.entries(run).filter(([key]) => key !== 'credentials_json' && (associationColumn || key !== 'workflow_snapshot_json'));
  const text = new Set(['flow_id', 'workflow_id', 'workflow_title', 'status', 'params_json', 'input_json', 'result_json',
    'triggered_by', 'identity_key', 'current_phase', 'origin_session_key', 'origin_session_id', 'origin_bot_id',
    'user_id', 'plugin_version', 'state_json', 'workflow_snapshot_json']);
  raw.exec(`CREATE TABLE flow_runs (${columns.map(([key]) => `${key} ${text.has(key) ? 'TEXT' : 'INTEGER'}`).join(',')});
    CREATE TABLE workflow_deploy_history (workflow_id TEXT, version INTEGER, deploy_number INTEGER, spec_json TEXT, is_active INTEGER);
    CREATE TABLE workflow_specs (workflow_id TEXT, spec_json TEXT);
    CREATE TABLE node_executions (id INTEGER);`);
  raw.prepare(`INSERT INTO flow_runs (${columns.map(([key]) => key).join(',')}) VALUES (${columns.map(() => '?').join(',')})`)
    .run(...columns.map(([, value]) => value));
  function deployment(spec: unknown = executedSpec, version = 1, deployNumber = 10, active = 0) {
    raw.prepare('INSERT INTO workflow_deploy_history VALUES (?,?,?,?,?)')
      .run('wf', version, deployNumber, typeof spec === 'string' ? spec : JSON.stringify(spec), active);
  }
  return { raw, db, repository: new FlowRunRepository(db), deployment };
}
