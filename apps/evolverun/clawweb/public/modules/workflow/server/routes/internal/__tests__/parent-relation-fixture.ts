import Database from 'better-sqlite3';
import express from 'express';
import type { Server } from 'node:http';
import { SqliteDatabase, runMigrations } from '@avernet/clawweb-shared/server/db';
import { FlowRunRepository } from '../../../repositories/flow-run-repository.js';
import { createInternalRunsRouter } from '../runs.js';
import { group } from '../../../repositories/__tests__/fanout-state-fixture.js';

export async function parentRelationFixture(fanout = false, itemId = 'one') {
  const raw = new Database(':memory:');
  const db = new SqliteDatabase(raw);
  await runMigrations(db, 'sqlite');
  const repo = new FlowRunRepository(db);
  const relation = { kind: 'subworkflow.v1', parent_flow_id: 'parent', parent_node_id: 'call', parent_attempt: 2,
    ...(fanout ? { fanout: { group_execution_id: 'execution-call', item_id: itemId, item_attempt: 1,
      dispatch_claim_token: 'claim-token' } } : {}) };
  const executor = { type: 'subworkflow', workflowId: 'child-wf', ...(fanout ? { fanout: { schemaVersion: 'fanout.v1' } } : {}) };
  const spec = { id: 'parent-wf', nodes: [{ id: 'call', executor }] };
  const state = { workflowId: 'parent-wf', workflowPin: { source: 'remote-snapshot', workflowId: 'parent-wf',
    workflowDigest: `remote-snapshot:${'a'.repeat(40)}` }, workflowSnapshot: spec, nodeStates: { call: { status: 'running', attempts: 2 } } };
  const identity = { originBotId: 'bot:owner', userId: 'owner', originSessionKey: 'parent-session', originSessionId: 'session-id' };
  if (!await repo.insert({ flowId: 'parent', workflowId: 'parent-wf', status: 'running', startedAt: 1,
    stateJson: JSON.stringify(state), workflowVersion: -1, ...identity })) throw new Error('Fixture registration failed');
  await db.exec(`INSERT INTO node_executions (flow_id,workflow_id,node_id,status,attempt,started_at)
    VALUES ('parent','parent-wf','call','running',2,1)`);
  const execution = raw.prepare("SELECT id FROM node_executions WHERE flow_id='parent' AND node_id='call' AND attempt=2").get() as { id: number };
  let claimed = group('call');
  if (fanout) {
    claimed.children[0].itemId = itemId;
    claimed.invocation = { schemaVersion: 'subworkflow.single.v1', parentAttempt: 2, parentExecutionId: String(execution.id), onFailure: 'skip' };
    claimed.children[0].status = 'queued';
    await repo.fanoutState.saveGroup('parent', 'call', { group: claimed, expectedRevision: null });
    claimed = structuredClone(claimed);
    claimed.revision = 1; claimed.children[0].status = 'dispatch_pending';
    claimed.children[0].dispatchClaimToken = 'claim-token';
    claimed.cleanupPendingChildIds = [claimed.children[0].childFlowId];
    await repo.fanoutState.saveGroup('parent', 'call', { group: claimed, expectedRevision: 0 });
  }
  const body = { flow_id: 'native-child', workflow_id: 'child-wf', status: 'running', started_at: 2,
    workflow_version: -1, origin_bot_id: identity.originBotId, user_id: identity.userId,
    origin_session_key: fanout ? claimed.children[0].sessionKey : identity.originSessionKey,
    origin_session_id: identity.originSessionId,
    params_json: JSON.stringify({ value: 'original' }), input_json: '{}', credentials_json: '{}',
    state_json: JSON.stringify({ workflowId: 'child-wf', nodeStates: {} }), parent_relation: relation };
  const app = express(); app.use(express.json());
  app.use('/api/internal/runs', createInternalRunsRouter(repo));
  const server = await new Promise<Server>(resolve => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
  const base = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/internal/runs`;
  const post = async (value: unknown = body, query = '') => {
    const response = await fetch(base + query, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(value) });
    return { status: response.status, body: await response.json() };
  };
  const child = () => raw.prepare('SELECT * FROM flow_runs WHERE flow_id = ?').get('native-child');
  const links = () => raw.prepare('SELECT child_flow_id,parent_flow_id,parent_node_id,parent_attempt,child_workflow_id FROM flow_run_links').all();
  const close = async () => { await new Promise<void>(resolve => { server.closeAllConnections(); server.close(() => resolve()); }); await db.close(); };
  return { db, raw, repo, relation, spec, body, post, child, links, close, claimed, base };
}
