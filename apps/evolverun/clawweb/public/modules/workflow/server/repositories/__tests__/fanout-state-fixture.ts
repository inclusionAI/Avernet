import Database from 'better-sqlite3';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import { migrations } from '@avernet/clawweb-shared/server/schema';

const createdAt = new Date().toISOString();
const deadlineAt = new Date(Date.now() + 3_600_000).toISOString();

export function fixture() {
  const raw = new Database(':memory:');
  const db = new SqliteDatabase(raw);
  for (const sql of migrations.find(m => m.version === 2)!.sql) {
    if (/CREATE TABLE IF NOT EXISTS flow_runs|CREATE.*INDEX.*ON flow_runs/.test(sql)) raw.exec(sql);
  }
  for (const sql of migrations.find(m => m.version === 5)!.sql) raw.exec(sql);
  for (const sql of migrations.find(m => m.version === 73)!.sql) raw.exec(sql);
  raw.exec('ALTER TABLE workflow_specs ADD COLUMN version INTEGER');
  raw.prepare('INSERT INTO workflow_specs (workflow_id, spec_json, version) VALUES (?, ?, ?)').run('child-wf', '{}', 1);
  raw.exec(`CREATE TABLE flow_run_links (
    child_flow_id TEXT PRIMARY KEY,
    parent_flow_id TEXT NOT NULL,
    parent_node_id TEXT NOT NULL,
    parent_attempt INTEGER,
    child_workflow_id TEXT NOT NULL,
    gmt_create INTEGER NOT NULL DEFAULT 0
  );
  CREATE INDEX idx_flow_run_links_parent ON flow_run_links(parent_flow_id);`);
  for (const name of ['state_json', 'credentials_json', 'origin_session_key', 'origin_bot_id', 'user_id', 'current_phase']) {
    raw.exec(`ALTER TABLE flow_runs ADD COLUMN ${name} TEXT`);
  }
  raw.prepare('INSERT INTO flow_runs (flow_id, workflow_id, status, started_at) VALUES (?, ?, ?, ?)')
    .run('parent', 'parent-wf', 'running', 1);
  return { raw, db };
}

export function group(nodeId = 'fanout'): Record<string, any> {
  return {
    schemaVersion: 'fanout.v1', kind: 'subworkflowFanoutState', parentFlowId: 'parent',
    groupNodeId: nodeId, groupExecutionId: `execution-${nodeId}`, revision: 0, status: 'running',
    createdAt, deadlineAt,
    sealedAt: null, cancelRequestedAt: null, cancelDeadlineAt: null,
    config: { schemaVersion: 'fanout.v1', items: [{ id: 'one' }], itemIdFrom: ['id'], concurrency: 1,
      successPolicy: 'all_done', finishMode: 'wait_all', resultPolicy: 'collect_all',
      groupTimeoutSeconds: 3600, cancelGraceSeconds: 5 },
    childPublishedVersion: 1,
    childWorkflow: { id: 'child-wf', version: 1, nodes: [{ id: 'work', phase: 'P1', executor: { type: 'agent' } }] },
    inputDigest: 'input-snapshot-digest',
    children: [{ parentFlowId: 'parent', parentNodeId: nodeId, groupExecutionId: `execution-${nodeId}`,
      itemId: 'one', itemIndex: 0, attemptNumber: 1, childFlowId: `child-${nodeId}`,
      sessionKey: `session-${nodeId}`, stateDirRef: `state-${nodeId}`, params: { input: 'hello' },
      depth: 1, item: { id: 'one' }, status: 'dispatch_pending', result: null, error: null }],
    error: null, cleanupPendingChildIds: [], result: null,
  };
}

export function childInput(g = group()): Record<string, any> {
  const child = g.children[0];
  return {
    parentNodeId: g.groupNodeId, groupExecutionId: g.groupExecutionId, itemId: child.itemId,
    attemptNumber: child.attemptNumber, workflowId: g.childWorkflow.id, sessionKey: child.sessionKey,
    stateJson: { workflowId: g.childWorkflow.id, workflowSnapshot: g.childWorkflow, params: child.params,
      subworkflowMeta: { parentFlowId: 'parent', parentNodeId: g.groupNodeId, depth: child.depth },
      nodeStates: {}, activeNodes: [], workflowData: {}, currentPhase: 'P1', originCredentials: 'private-value' },
  };
}
