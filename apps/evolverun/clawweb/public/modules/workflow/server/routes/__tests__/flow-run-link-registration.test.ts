// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest';
import express from 'express';
import Database from 'better-sqlite3';
import type { Server } from 'node:http';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { createInternalFlowRunLinksRouter, type RunLinkAuth } from '../internal/flow-run-links.js';

const link = { childFlowId: 'child', parentFlowId: 'parent', parentNodeId: 'node', parentAttempt: 2, childWorkflowId: 'child-wf' };
const cleanups: Array<() => Promise<void>> = [];
afterEach(async () => { for (const cleanup of cleanups.splice(0)) await cleanup(); });

async function fixture(enabled = true) {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL);
    INSERT INTO flow_runs VALUES ('parent','parent-wf'),('child','child-wf'),('other','other-wf');
    CREATE TABLE flow_run_links (child_flow_id TEXT PRIMARY KEY, parent_flow_id TEXT NOT NULL, parent_node_id TEXT NOT NULL, parent_attempt INTEGER, child_workflow_id TEXT NOT NULL, gmt_create INTEGER DEFAULT 0);
    CREATE TABLE workflow_repair_release_records (record_id TEXT, record_kind TEXT);
    INSERT INTO workflow_repair_release_records VALUES ('parent','trial'),('child','trial_run');`);
  const db = new SqliteDatabase(raw);
  const authenticate = vi.fn<RunLinkAuth['authenticate']>().mockResolvedValue({ botId: 'trusted-bot', ownerId: 'trusted-owner' });
  const authorize = vi.fn<RunLinkAuth['authorize']>().mockResolvedValue(true);
  const app = express(); app.use(express.json());
  app.use('/api/internal/flow-run-links', createInternalFlowRunLinksRouter(db, enabled ? { authenticate, authorize } : undefined));
  const server = await new Promise<Server>(resolve => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
  cleanups.push(() => new Promise<void>(resolve => server.close(() => resolve())), () => db.close());
  const url = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/internal/flow-run-links`;
  const post = async (body: unknown, query = '') => {
    const response = await fetch(url + query, { method: 'POST', headers: { 'content-type': 'application/json', 'x-bot-id': 'forged' }, body: JSON.stringify(body) });
    return { status: response.status, text: await response.text(), cache: response.headers.get('cache-control') };
  };
  return { raw, db, authenticate, authorize, post };
}

describe('internal flow run link registration', () => {
  it('fails closed without host authentication even for plausible requests', async () => {
    const f = await fixture(false);
    expect(await f.post(link)).toEqual({ status: 503, text: '{"error":"Unavailable"}', cache: 'no-store' });
    expect(f.raw.prepare('SELECT * FROM flow_run_links').all()).toEqual([]);
  });
  it('authenticates independently, validates DB identities and is idempotent for trial runs', async () => {
    const f = await fixture();
    expect((await f.post(link)).status).toBe(204);
    expect((await f.post(link)).status).toBe(204);
    expect(f.raw.prepare('SELECT child_flow_id, parent_flow_id, parent_node_id, parent_attempt, child_workflow_id FROM flow_run_links').all())
      .toEqual([{ child_flow_id: 'child', parent_flow_id: 'parent', parent_node_id: 'node', parent_attempt: 2, child_workflow_id: 'child-wf' }]);
    expect(f.authorize).toHaveBeenCalledTimes(2);
    expect(f.authorize.mock.calls[0][0]).toEqual({ botId: 'trusted-bot', ownerId: 'trusted-owner' });
    expect(f.authorize.mock.calls[0][1]).toEqual({ parent: { flowId: 'parent', workflowId: 'parent-wf' }, child: { flowId: 'child', workflowId: 'child-wf' }, parentNodeId: 'node', parentAttempt: 2 });
  });
  it('rejects spoofed identities, malformed inputs, missing runs, workflow mismatch and reparenting', async () => {
    const f = await fixture();
    for (const body of [{ ...link, botId: 'trusted-bot' }, { ...link, parentAttempt: 0 }, { ...link, childFlowId: 'parent' }, { ...link, parentNodeId: '../x' }]) expect((await f.post(body)).status).toBe(400);
    expect((await f.post(link, '?botId=x')).status).toBe(400);
    for (const body of [{ ...link, parentFlowId: 'absent' }, { ...link, childFlowId: 'absent' }, { ...link, childWorkflowId: 'other-wf' }]) expect((await f.post(body)).status).toBe(404);
    expect(f.authorize).not.toHaveBeenCalled();
    expect((await f.post(link)).status).toBe(204);
    expect((await f.post({ ...link, parentFlowId: 'other' })).status).toBe(404);
    expect(f.raw.prepare('SELECT parent_flow_id FROM flow_run_links').get()).toEqual({ parent_flow_id: 'parent' });
  });
  it('rechecks authorization on retries and conceals denied relationships', async () => {
    const f = await fixture();
    expect((await f.post({ ...link, parentAttempt: null })).status).toBe(204);
    f.authorize.mockResolvedValue(false);
    expect(await f.post({ ...link, parentAttempt: null })).toEqual({ status: 404, text: '{"error":"Not found"}', cache: 'no-store' });
    f.authenticate.mockResolvedValue(null);
    expect((await f.post(link)).status).toBe(401);
  });
  it('propagates database and authorizer failures without leaking their details', async () => {
    const f = await fixture();
    f.authorize.mockRejectedValueOnce(new Error('secret-owner'));
    expect(await f.post(link)).toEqual({ status: 503, text: '{"error":"Unavailable"}', cache: 'no-store' });
    f.raw.exec('DROP TABLE flow_run_links');
    expect(await f.post(link)).toEqual({ status: 503, text: '{"error":"Unavailable"}', cache: 'no-store' });
  });
  it('serializes concurrent idempotent writes and refuses reassignment', async () => {
    const f = await fixture();
    const responses = await Promise.all([f.post(link), f.post(link), f.post({ ...link, parentFlowId: 'other' })]);
    expect(responses.filter(r => r.status === 204)).toHaveLength(2);
    expect(responses.filter(r => r.status === 404)).toHaveLength(1);
    expect(f.raw.prepare('SELECT count(*) AS n FROM flow_run_links').get()).toEqual({ n: 1 });
  });
});
