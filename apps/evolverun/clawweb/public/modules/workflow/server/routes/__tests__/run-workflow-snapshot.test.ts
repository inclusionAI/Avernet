// @vitest-environment node
import { describe, it, expect, vi } from 'vitest';
import express from 'express';
import Database from 'better-sqlite3';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { workflowConfigurationDigest } from '@avernet/clawweb-shared/server/services/workflow-snapshot';
import { createRunWorkflowSnapshotRouter } from '../run-workflow-snapshot.js';

const spec = { id: 'wf', nodes: [{ id: 'n', executor: { type: 'action', token: 'secret-token' }, params: { password: 'secret' } }] };
async function setup() {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, workflow_id TEXT, workflow_snapshot_json TEXT, workflow_version INTEGER, workflow_deploy_number INTEGER, state_json TEXT);
    CREATE TABLE workflow_deploy_history (workflow_id TEXT, deploy_number INTEGER, version INTEGER, spec_json TEXT);
    CREATE TABLE workflow_repair_release_records (record_id TEXT, record_kind TEXT);`);
  const binding = { status: 'resolved', snapshot: { kind: 'deployment', workflowId: 'wf', workflowVersion: 1, deployNumber: 7, configDigest: workflowConfigurationDigest(spec) } };
  raw.prepare('INSERT INTO flow_runs VALUES (?, ?, ?, ?, ?, ?)').run('f', 'wf', JSON.stringify(binding), 1, 7, null);
  raw.prepare('INSERT INTO workflow_deploy_history VALUES (?, ?, ?, ?)').run('wf', 7, 1, JSON.stringify(spec));
  const authorize = vi.fn(async () => ({ actorId: 'user', canEdit: false }));
  const app = express(); app.use('/api/runs', createRunWorkflowSnapshotRouter(new SqliteDatabase(raw), authorize));
  const server = app.listen(0); await new Promise<void>(resolve => server.once('listening', resolve));
  const addr = server.address(); if (!addr || typeof addr === 'string') throw new Error('listen failed');
  const request = async () => { const res = await fetch(`http://127.0.0.1:${addr.port}/api/runs/f/workflow-snapshot`); return { status: res.status, data: await res.json(), cache: res.headers.get('cache-control') }; };
  return { raw, authorize, request, close: () => new Promise<void>(resolve => server.close(() => { raw.close(); resolve(); })) };
}
describe('authorized run snapshot', () => {
  it('returns only projected topology for a verified immutable binding', async () => {
    const f = await setup(); try {
      expect(await f.request()).toEqual({ status: 200, cache: 'no-store', data: { nodes: [{ id: 'n', title: 'n', dependsOn: [], executor: { type: 'action' } }] } });
      expect(f.authorize).toHaveBeenCalledWith(expect.anything(), 'wf', 'view');
    } finally { await f.close(); }
  });
  it('hides a run from an unauthorized requester before reading the snapshot', async () => {
    const f = await setup(); try { f.authorize.mockResolvedValueOnce(null as never); expect((await f.request()).status).toBe(404); } finally { await f.close(); }
  });
  it('rejects modified snapshot and absent binding rather than inferring a digest', async () => {
    const f = await setup(); try {
      f.raw.prepare('UPDATE workflow_deploy_history SET spec_json = ?').run(JSON.stringify({ ...spec, nodes: [] }));
      expect((await f.request()).status).toBe(409);
      f.raw.exec('UPDATE flow_runs SET workflow_snapshot_json = NULL');
      expect((await f.request()).status).toBe(409);
    } finally { await f.close(); }
  });
  it('decodes a verified YAML wrapper and recognizes legacy loop-group nodes', async () => {
    const f = await setup(); try {
      const wrapper = { content: 'id: wf\nnodes:\n  - id: loop\n    type: loop-group\n' };
      const binding = { status: 'resolved', snapshot: { kind: 'deployment', workflowId: 'wf', workflowVersion: 1, deployNumber: 7, configDigest: workflowConfigurationDigest(wrapper) } };
      f.raw.prepare('UPDATE flow_runs SET workflow_snapshot_json = ?').run(JSON.stringify(binding));
      f.raw.prepare('UPDATE workflow_deploy_history SET spec_json = ?').run(JSON.stringify(wrapper));
      expect((await f.request()).data).toEqual({ nodes: [{ id: 'loop', title: 'loop', dependsOn: [], executor: { type: 'loop-group' } }] });
    } finally { await f.close(); }
  });
  it('uses loop-group type priority even when another executor type is present', async () => {
    const f = await setup(); try {
      const loop = { id: 'wf', nodes: [{ id: 'l', type: 'loop-group', executor: { type: 'action' } }] };
      const binding = { status: 'resolved', snapshot: { kind: 'deployment', workflowId: 'wf', workflowVersion: 1, deployNumber: 7, configDigest: workflowConfigurationDigest(loop) } };
      f.raw.prepare('UPDATE flow_runs SET workflow_snapshot_json = ?').run(JSON.stringify(binding));
      f.raw.prepare('UPDATE workflow_deploy_history SET spec_json = ?').run(JSON.stringify(loop));
      expect((await f.request()).data.nodes[0].executor.type).toBe('loop-group');
    } finally { await f.close(); }
  });
  it('does not treat ordinary content as a wrapper when nodes are present', async () => {
    const f = await setup(); try {
      const withContent = { ...spec, content: 'not YAML metadata' };
      const binding = { status: 'resolved', snapshot: { kind: 'deployment', workflowId: 'wf', workflowVersion: 1, deployNumber: 7, configDigest: workflowConfigurationDigest(withContent) } };
      f.raw.prepare('UPDATE flow_runs SET workflow_snapshot_json = ?').run(JSON.stringify(binding));
      f.raw.prepare('UPDATE workflow_deploy_history SET spec_json = ?').run(JSON.stringify(withContent));
      expect((await f.request()).status).toBe(200);
    } finally { await f.close(); }
  });
  it('supports a saved snapshot only when binding and source flow match', async () => {
    const f = await setup(); try {
      const commit = 'a'.repeat(40);
      const binding = { status: 'resolved', snapshot: { kind: 'saved', workflowId: 'wf', sourceFlowId: 'f', commit, configDigest: workflowConfigurationDigest(spec) } };
      const state = { workflowPin: { source: 'remote-snapshot', workflowId: 'wf', workflowDigest: `remote-snapshot:${commit}` }, workflowSnapshot: spec };
      f.raw.prepare('UPDATE flow_runs SET workflow_snapshot_json = ?, workflow_version = -1, state_json = ?').run(JSON.stringify(binding), JSON.stringify(state));
      expect((await f.request()).status).toBe(200);
      binding.snapshot.sourceFlowId = 'other';
      f.raw.prepare('UPDATE flow_runs SET workflow_snapshot_json = ?').run(JSON.stringify(binding));
      expect((await f.request()).status).toBe(409);
    } finally { await f.close(); }
  });
  it('does not expose trial runs through the production view', async () => {
    const f = await setup(); try { f.raw.exec("INSERT INTO workflow_repair_release_records VALUES ('f','trial_run')"); expect((await f.request()).status).toBe(404); } finally { await f.close(); }
  });
});
