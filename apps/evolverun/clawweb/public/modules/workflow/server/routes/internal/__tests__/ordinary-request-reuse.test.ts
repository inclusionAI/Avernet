// @vitest-environment node
import { describe, expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { ordinaryRunRequestDdl } from '@avernet/clawweb-shared/server/schema-ordinary-run-request';
import { OrdinaryRunRequestService } from '../../../services/ordinary-run-request.js';
import { FlowRunRepository } from '../../../repositories/flow-run-repository.js';

const principal = { botId: 'bot', ownerId: 'owner' };
const auth = { authenticate: async () => principal, authorize: async () => true };
const body = { type: 'ordinary-run-request.register.v1', ordinary_request_key: 'key', ordinary_parent_flow_id: 'parent' };
const run = { flowId: 'child', workflowId: 'wf', status: 'pending', startedAt: 1 };

describe('ordinary association run incarnation', () => {
  it.each(['child', 'parent'])('fails closed after deleting and recreating the %s run with the same identifiers', async id => {
    const raw = new Database(':memory:');
    const db = new SqliteDatabase(raw);
    raw.exec(`CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, flow_id TEXT UNIQUE, workflow_id TEXT NOT NULL, workflow_title TEXT,
      status TEXT NOT NULL, state_json TEXT, params_json TEXT, input_json TEXT, node_count INTEGER, triggered_by TEXT,
      identity_key TEXT, current_phase TEXT, started_at INTEGER, credentials_json TEXT, origin_session_key TEXT,
      origin_session_id TEXT, origin_bot_id TEXT, user_id TEXT, plugin_version TEXT, engine TEXT, workflow_version INTEGER,
      workflow_deploy_number INTEGER, gmt_create INTEGER, gmt_modified INTEGER, ordinary_run_marker TEXT);
      CREATE TABLE workflow_repair_release_records (record_kind TEXT, record_id TEXT, body_json TEXT);
      INSERT INTO flow_runs (flow_id,workflow_id,status,started_at) VALUES ('parent','parent-wf','running',1);`);
    await db.exec(db.dialect.renderDdl(ordinaryRunRequestDdl));
    const service = new OrdinaryRunRequestService(db, async (tx, input) => { await new FlowRunRepository(tx).insert(input); });
    try {
      const failing = new OrdinaryRunRequestService(db, async () => { throw new Error('insert failed'); });
      await expect(failing.create(body, run, principal, auth)).rejects.toThrow('insert failed');
      expect(raw.prepare("SELECT ordinary_run_marker FROM flow_runs WHERE flow_id='parent'").get()).toEqual({ ordinary_run_marker: null });
      expect(raw.prepare('SELECT count(*) AS n FROM ordinary_run_requests').get()).toEqual({ n: 0 });
      expect((await service.create(body, run, principal, auth)).created).toBe(true);
      const lookup = await service.lookup('key', principal, auth);
      expect(lookup).toEqual({ parent_flow_id: 'parent', flow_id: 'child', workflow_id: 'wf' });
      const markers = raw.prepare('SELECT parent_run_marker,child_run_marker FROM ordinary_run_requests').get() as { parent_run_marker: string; child_run_marker: string };
      expect(markers.parent_run_marker).toMatch(/^[0-9a-f]{64}$/);
      expect(markers.child_run_marker).toMatch(/^[0-9a-f]{64}$/);
      expect(markers.parent_run_marker).not.toBe(markers.child_run_marker);
      raw.exec('UPDATE ordinary_run_requests SET parent_run_marker=NULL,child_run_marker=NULL');
      await expect(service.lookup('key', principal, auth)).rejects.toThrow();
      await expect(service.create(body, run, principal, auth)).rejects.toThrow();
      raw.prepare('UPDATE ordinary_run_requests SET parent_run_marker=?,child_run_marker=?').run(markers.parent_run_marker, markers.child_run_marker);
      raw.prepare('DELETE FROM flow_runs WHERE flow_id = ?').run(id);
      raw.prepare('INSERT INTO flow_runs (flow_id,workflow_id,status,started_at) VALUES (?,?,?,?)')
        .run(id, id === 'child' ? 'wf' : 'parent-wf', 'pending', 1);
      await expect(service.lookup('key', principal, auth)).rejects.toThrow();
      await expect(service.create(body, { ...run, flowId: 'retry' }, principal, auth)).rejects.toThrow();
      expect(raw.prepare('SELECT count(*) AS n FROM ordinary_run_requests').get()).toEqual({ n: 1 });
    } finally { await db.close(); }
  });
});
