// @vitest-environment node
import express from 'express';
import type { Server } from 'node:http';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { ordinaryRunRequestDdl } from '@avernet/clawweb-shared/server/schema-ordinary-run-request';
import { fixture, group, childInput } from '../../repositories/__tests__/fanout-state-fixture.js';
import { FlowRunRepository } from '../../repositories/flow-run-repository.js';
import { RunSubworkflowsRepository } from '../../repositories/run-subworkflows-repository.js';
import { RunSubworkflowsService } from '../run-subworkflows-service.js';
import { createInternalRunsRouter } from '../../routes/internal/runs.js';

let f: ReturnType<typeof fixture>; let server: Server; let base: string; let reader: RunSubworkflowsService;
const actor = { actorId: 'reader', isAdmin: false };
const permissions = { resolveRunViewScope: async () => ({ bots: [{ botId: 'bot', ownerId: 'alice' }] }) };
const observe = (node: string, generation: string) => reader.readGroup(actor, 'parent', node, { groupExecutionId: generation });
async function request(method: string, path: string, body: unknown) {
  const response = await fetch(base + path, { method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  return { status: response.status, body: await response.json() };
}

describe('real SQLite fanout identity authority', () => {
  beforeEach(async () => {
    f = fixture();
    const columns = new Set((f.raw.prepare('PRAGMA table_info(flow_runs)').all() as { name: string }[]).map(c => c.name));
    for (const column of ['workflow_title', 'origin_bot_id', 'status', 'state_json', 'input_json', 'result_json', 'params_json',
      'node_count', 'succeeded_count', 'failed_count', 'total_duration_ms', 'total_token_usage', 'started_at', 'completed_at',
      'triggered_by', 'current_phase', 'workflow_version', 'workflow_deploy_number', 'origin_session_key', 'origin_session_id',
      'identity_key', 'user_id', 'plugin_version', 'engine', 'gmt_create', 'gmt_modified', 'evolution_analysis_status', 'ordinary_run_marker']) {
      if (!columns.has(column)) f.raw.exec(`ALTER TABLE flow_runs ADD COLUMN ${column} TEXT`);
    }
    f.raw.exec(`CREATE TABLE workflow_repair_release_records (record_kind TEXT, record_id TEXT, body_json TEXT)`);
    await f.db.exec(f.db.dialect.renderDdl(ordinaryRunRequestDdl));
    f.raw.prepare("UPDATE flow_runs SET origin_bot_id='bot:alice' WHERE flow_id='parent'").run();
    const repo = new FlowRunRepository(f.db);
    const app = express(); app.use(express.json());
    app.use('/runs', createInternalRunsRouter(repo, null, undefined, {}, {
      authenticate: async () => ({ botId: 'bot', ownerId: 'alice' }), authorize: async () => true,
    }));
    await new Promise<void>(resolve => { server = app.listen(0, '127.0.0.1', resolve); });
    base = `http://127.0.0.1:${(server.address() as { port: number }).port}/runs`;
    reader = new RunSubworkflowsService(new RunSubworkflowsRepository(f.db), permissions as never);
  });
  afterEach(async () => { await new Promise<void>(resolve => server.close(() => resolve())); await f.db.close(); });

  it('does not attach an orphan ordinary association to a different group using the same key', async () => {
    const registered = await request('POST', '/request-association/register', {
      type: 'ordinary-run-request.register.v1', ordinary_request_key: 'orphan-key', ordinary_parent_flow_id: 'parent',
      run: { flow_id: 'native-child', workflow_id: 'child-wf', status: 'pending', origin_bot_id: 'bot:alice' },
    });
    expect(registered.status).toBe(201);
    const other = group('other'); other.children[0].childFlowId = 'orphan-key'; other.children[0].nativeFlowId = 'native-child';
    delete other.children[0].sessionKey; delete other.children[0].stateDirRef;
    expect((await request('PUT', '/parent/fanouts/other', { group: other, expectedRevision: null })).status).toBe(200);
    const page = await observe('other', 'execution-other');
    expect(page).toMatchObject({ countScope: 'visible', groupTotal: null, visibleTotal: 0, children: [] });
  });
  it('keeps a genuine old flat managed child hidden when no unforgeable provenance exists', async () => {
    const legacy = group();
    expect((await request('PUT', '/parent/fanouts/fanout', { group: legacy, expectedRevision: null })).status).toBe(200);
    const started = await request('PUT', '/parent/fanout-children/child-fanout', { ...childInput(legacy), originBotId: 'bot:alice' });
    expect(started.status).toBe(201);
    expect(await observe('fanout', 'execution-fanout')).toMatchObject({ countScope: 'visible', groupTotal: null, visibleTotal: 0, children: [] });
  });
  it('keeps a forged managed checkpoint inserted through ordinary POST invisible', async () => {
    const legacy = group();
    expect((await request('PUT', '/parent/fanouts/fanout', { group: legacy, expectedRevision: null })).status).toBe(200);
    const started = await request('PUT', '/parent/fanout-children/child-fanout', { ...childInput(legacy), originBotId: 'bot:alice' });
    expect(started.status).toBe(201);
    const original = f.raw.prepare("SELECT * FROM flow_runs WHERE flow_id='child-fanout'").get() as Record<string, unknown>;
    f.raw.prepare("DELETE FROM flow_runs WHERE flow_id='child-fanout'").run();
    const forged = await request('POST', '', { flow_id: original.flow_id, workflow_id: original.workflow_id, status: original.status,
      state_json: original.state_json, params_json: original.params_json, origin_bot_id: original.origin_bot_id,
      origin_session_key: original.origin_session_key, started_at: original.started_at });
    expect(forged.status).toBe(201);
    const replay = f.raw.prepare("SELECT * FROM flow_runs WHERE flow_id='child-fanout'").get() as Record<string, unknown>;
    for (const field of ['flow_id', 'workflow_id', 'status', 'state_json', 'params_json', 'origin_bot_id', 'origin_session_key', 'started_at']) {
      expect(replay[field]).toEqual(original[field]);
    }
    const child = await new FlowRunRepository(f.db).fanoutState.getChild('parent', 'child-fanout');
    expect(child.flowId).toBe('child-fanout'); // checkpoint is indistinguishable to the managed validator
    expect(await observe('fanout', 'execution-fanout')).toMatchObject({ visibleTotal: 0, children: [] });
  });
});
