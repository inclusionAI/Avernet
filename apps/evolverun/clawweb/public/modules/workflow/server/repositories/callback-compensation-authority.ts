import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import type { HttpCallbackConfigRow } from './http-callback-config-repository.js';
import { nodeFields, runFields, type RunSnapshot } from './callback-compensation-source.js';
import type { CompensationEvidence } from '../services/callback-compensation/types.js';

export interface PostAuthority {
  snapshot: RunSnapshot;
  config: HttpCallbackConfigRow;
  evidence: CompensationEvidence;
}

const runColumns = [...runFields.split(',').map(s => s.trim()), 'env', 'retry_epoch', 'state_json'];
const nodeColumns = nodeFields.split(',').map(s => s.trim());
const configColumns = ['id', 'config_id', 'workflow_id', 'name', 'url', 'secret', 'enabled', 'notify_on',
  'timeout_ms', 'max_retries', 'retry_delay_ms', 'include_node_output', 'gmt_create', 'gmt_modified'];

/** Conservative bind budgets leave room below SQLite/MySQL statement limits.
 * A too-large authority stays review-only BEFORE reserving a delivery identity.
 */
export function postAuthorityWithinBudget(db: IDatabase, snapshot: RunSnapshot, evidence: CompensationEvidence): boolean {
  const bindings = 5 + runColumns.length + configColumns.length + 2 + nodeColumns.length * snapshot.nodes.length
    + 1 + 2 + evidence.configIds.length + evidence.urls.length;
  return bindings <= (db.dbType === 'sqlite' ? 30_000 : 60_000);
}

/** One SQL authorization point, not a lock on the receiver or future engine writes.
 * Compare values, not second-resolution modification timestamps alone. Text equality
 * must be byte-sensitive even on case-insensitive MySQL/OceanBase collations.
 */
export function postAuthorityPredicate(db: IDatabase, authority: PostAuthority): { sql: string; params: unknown[] } {
  const { snapshot, config, evidence } = authority;
  const params: unknown[] = [];
  const mysql = db.dbType === 'mysql' || db.dbType === 'zdas';
  const equal = (alias: string, field: string, value: unknown): string => {
    params.push(value);
    const column = `${alias}.${field}`;
    const timestamp = field === 'gmt_create' || field === 'gmt_modified';
    if (timestamp || typeof value === 'number') return `${column} ${mysql ? '<=>' : 'IS'} ?`;
    return mysql ? `CAST(${column} AS BINARY) <=> CAST(? AS BINARY)` : `CAST(${column} AS BLOB) IS CAST(? AS BLOB)`;
  };
  const rowEquals = (alias: string, fields: string[], row: Record<string, unknown>) =>
    fields.map(field => equal(alias, field, row[field])).join(' AND ');
  const run = `EXISTS (SELECT 1 FROM flow_runs r WHERE ${rowEquals('r', runColumns, snapshot.run)})`;
  const callback = `EXISTS (SELECT 1 FROM http_callback_configs c WHERE ${rowEquals('c', configColumns, config)})`;
  params.push(snapshot.run.flow_id, snapshot.nodes.length);
  const count = '(SELECT COUNT(*) FROM node_executions n WHERE n.flow_id = ?) = ?';
  const nodes = snapshot.nodes.map(node => `EXISTS (SELECT 1 FROM node_executions n WHERE ${rowEquals('n', nodeColumns, node)})`);
  params.push(snapshot.run.flow_id);
  const trial = "NOT EXISTS (SELECT 1 FROM workflow_repair_release_records t WHERE t.record_id = ? AND t.record_kind IN ('trial', 'trial_run'))";
  params.push(snapshot.run.workflow_id, snapshot.run.flow_id, ...evidence.configIds, ...evidence.urls);
  const history = `NOT EXISTS (SELECT 1 FROM http_callback_logs h WHERE h.workflow_id = ? AND h.flow_id = ?
    AND h.notify_event = 'workflow_succeeded' AND (h.config_id IN (${evidence.configIds.map(() => '?').join(',')})
    OR h.callback_url IN (${evidence.urls.map(() => '?').join(',')})))`;
  // SQLite limits expression depth independently from parameter count. A flat
  // conjunction creates a left-deep tree; balance without omitting any node.
  const conjunction = (parts: string[]): string => {
    if (parts.length === 1) return parts[0];
    const half = Math.floor(parts.length / 2);
    return `(${conjunction(parts.slice(0, half))} AND ${conjunction(parts.slice(half))})`;
  };
  return { sql: conjunction([run, callback, count, ...nodes, trial, history]), params };
}
