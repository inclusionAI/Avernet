// @vitest-environment node
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import express from 'express';
import type { Server } from 'node:http';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { sqliteDialect } from '@avernet/clawweb-shared/server/db/dialect';
import { FlowRunRepository } from '../../../repositories/flow-run-repository.js';
import { createInternalRunsRouter } from '../runs.js';

const exec = vi.fn(async () => ({ affectedRows: 1 }));
const query = vi.fn(async () => []);
const db = { dbType: 'sqlite', dialect: sqliteDialect, exec, query,
  transaction: async (fn: (tx: IDatabase) => Promise<unknown>) => fn(db as IDatabase), close: async () => {} } as IDatabase;
let server: Server;
let base: string;
let configuredBase: string;
beforeAll(async () => {
  const app = express(); app.use(express.json()); app.use('/runs', createInternalRunsRouter(new FlowRunRepository(db)));
  app.use('/configured-runs', createInternalRunsRouter(new FlowRunRepository(db), undefined, undefined, {}, {
    authenticate: async () => ({ botId: 'bot', ownerId: 'owner' }), authorize: async () => true,
  }));
  server = await new Promise<Server>(resolve => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}/runs`;
  configuredBase = base.replace(/\/runs$/, '/configured-runs');
});
afterAll(async () => { await new Promise<void>(resolve => server.close(() => resolve())); });
const post = (body: object, path = '', configured = false) => fetch((configured ? configuredBase : base) + path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
const run = { flow_id: 'child', workflow_id: 'workflow', status: 'running' };
describe('unavailable ordinary request association fails closed', () => {
  it('does not silently create an unassociated run when either new field is present', async () => {
    for (const fields of [{ ordinary_request_key: 'request', ordinary_parent_flow_id: 'parent' },
      { ordinary_request_key: null }, { ordinary_parent_flow_id: 'parent' }]) {
      exec.mockClear(); query.mockClear();
      const response = await post({ ...run, ...fields });
      expect(response.status).toBe(503);
      expect(await response.json()).toMatchObject({ success: false });
      expect(exec).not.toHaveBeenCalled(); expect(query).not.toHaveBeenCalled();
    }
  });
  it('rejects mixed trial and ordinary identities without registration', async () => {
    exec.mockClear(); query.mockClear();
    const response = await post({ ...run, ordinary_request_key: 'key', ordinary_parent_flow_id: 'parent', run_request_id: 'trial' });
    expect(response.status).toBe(409);
    expect(exec).not.toHaveBeenCalled(); expect(query).not.toHaveBeenCalled();
  });
  it('keeps the dedicated registration unavailable without host authorization', async () => {
    const response = await post({ ...run, type: 'ordinary-run-request.register.v1', ordinary_request_key: 'key', ordinary_parent_flow_id: 'parent' }, '/request-association/register');
    expect(response.status).toBe(503); expect(exec).not.toHaveBeenCalled();
    const nested = await post({ type: 'ordinary-run-request.register.v1', ordinary_request_key: 'key', ordinary_parent_flow_id: 'parent', run }, '/request-association/register');
    expect(nested.status).toBe(503);
    const mixed = await post({ type: 'ordinary-run-request.register.v1', ordinary_request_key: 'key', ordinary_parent_flow_id: 'parent', run: { ...run, run_request_id: 'trial' } }, '/request-association/register');
    expect(mixed.status).toBe(503);
  });
  it.each([
    '/request-association/register',
    '/request-association/register/',
    '/REQUEST-ASSOCIATION/REGISTER',
    '/Request-Association/Register/',
  ])('fails closed without writes for a legacy-shaped body on %s', async (path) => {
    exec.mockClear(); query.mockClear();
    const response = await post(run, path);
    expect(response.status).toBe(503);
    expect(response.headers.get('cache-control')).toContain('no-store');
    expect(await response.json()).toMatchObject({ success: false });
    expect(exec).not.toHaveBeenCalled();
    expect(query).not.toHaveBeenCalled();
  });
  it('continues to register a legacy run on the root path without ordinary auth', async () => {
    exec.mockClear(); query.mockClear();
    const response = await post(run);
    expect(response.status).toBe(201);
    expect(await response.json()).toMatchObject({ success: true });
    expect(exec).toHaveBeenCalled();
  });
  it.each([false, true])('rejects nested ordinary identity on the legacy route (auth configured: %s)', async (configured) => {
    for (const nested of [
      { ordinary_request_key: 'key' }, { ordinary_request_key: null },
      { ordinary_parent_flow_id: 'parent' }, { ordinary_parent_flow_id: null },
      { type: 'ordinary-run-request.register.v1' },
    ]) {
      exec.mockClear(); query.mockClear();
      const response = await post({ ...run, run: nested }, '', configured);
      expect(response.status).toBe(503);
      expect(response.headers.get('cache-control')).toContain('no-store');
      expect(exec).not.toHaveBeenCalled(); expect(query).not.toHaveBeenCalled();
    }
  });
  it.each([false, true])('rejects trial and ordinary identities at either envelope level (auth configured: %s)', async (configured) => {
    for (const body of [
      { ...run, run_request_id: 'trial', run: { ordinary_request_key: 'key' } },
      { ...run, ordinary_request_key: 'key', run: { run_request_id: 'trial' } },
      { ...run, run: { ordinary_parent_flow_id: 'parent', candidate_commit: 'commit' } },
    ]) {
      exec.mockClear(); query.mockClear();
      const response = await post(body, '', configured);
      expect(response.status).toBe(409);
      expect(exec).not.toHaveBeenCalled(); expect(query).not.toHaveBeenCalled();
    }
  });
  it.each([false, true])('preserves legacy business parameters with ordinary-like names (auth configured: %s)', async (configured) => {
    exec.mockClear(); query.mockClear();
    const business = { ordinary_request_key: 'business', ordinary_parent_flow_id: 'business', type: 'ordinary-run-request.register.v1' };
    const response = await post({ ...run, params_json: JSON.stringify(business), input_json: JSON.stringify(business),
      run: { params: business, type: 'business' }, metadata: business }, '', configured);
    expect(response.status).toBe(201);
    expect(exec).toHaveBeenCalled();
  });
  it('does not report a missing association when lookup is unavailable', async () => {
    const response = await fetch(`${base}/request-association/lookup`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ type: 'ordinary-run-request.lookup.v1', ordinary_request_key: 'key' }) });
    expect(response.status).toBe(503);
    expect(response.headers.get('cache-control')).toContain('no-store');
    expect(await response.json()).toMatchObject({ success: false });
  });
});
