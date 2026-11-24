// @vitest-environment node
import express from 'express';
import { once } from 'node:events';
import type { Server } from 'node:http';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FanoutReadError, type FanoutReadErrorCode, type RunSubviewPermissions,
  type RunSubworkflowsReader, type RunSubworkflowsStore, type StoredRunSnapshot } from '../../contracts/run-subworkflows.js';
import type { FlowRunRepository } from '../../repositories/flow-run-repository.js';
import { createRunsRouter } from '../../routes/runs.js';
import { RunSubworkflowsService } from '../run-subworkflows-service.js';

vi.mock('@avernet/clawevolve/server/services/baas-intervention', () => ({ sendIntervention: vi.fn() }));
const servers: Server[] = [];
const diagnostics: unknown[] = [];
beforeEach(() => {
  diagnostics.length = 0;
  for (const channel of ['log', 'info', 'warn', 'error', 'debug'] as const) vi.spyOn(console, channel).mockImplementation(() => {});
});
afterEach(async () => {
  await Promise.all(servers.splice(0).map(server => new Promise<void>(resolve => server.close(() => resolve()))));
  vi.restoreAllMocks();
});

async function listen(repo: FlowRunRepository | null, reader: RunSubworkflowsReader | null = null) {
  const app = express();
  app.use(express.json());
  app.use((_req, res, next) => {
    res.once('finish', () => { diagnostics.push(res.locals.taskGuardApiDiagnostic); });
    next();
  });
  app.use('/api/runs', createRunsRouter(repo, null, null, null, null, null, null, null, null, null, reader));
  const server = app.listen(0, '127.0.0.1');
  servers.push(server);
  await once(server, 'listening');
  return `http://127.0.0.1:${(server.address() as { port: number }).port}/api/runs`;
}
function readerRejecting(error: unknown): RunSubworkflowsReader {
  return { listRuns: vi.fn().mockRejectedValue(error), workflowStats: vi.fn().mockRejectedValue(error),
    readGroup: vi.fn().mockRejectedValue(error), inspectRun: vi.fn().mockRejectedValue(error),
    isFanoutOriginChild: vi.fn().mockRejectedValue(error) };
}
function persistenceFailure() {
  return Object.assign(new Error('durable run read failed'), {
    config: { headers: { Authorization: '<private-auth-marker>', Cookie: '<private-cookie-marker>' } },
    token: '<private-token-marker>', cookies: ['<private-cookie-marker>'],
    [Symbol.for('nodejs.util.inspect.custom')]() { throw new Error('must not inspect HTTP client metadata'); },
  });
}
function expectPrivateMetadataAbsent() {
  const calls = ['log', 'info', 'warn', 'error', 'debug'].flatMap(channel => vi.mocked(console[channel as 'error']).mock.calls);
  expect(JSON.stringify({ calls, diagnostics })).not.toMatch(/private-|Authorization|Cookie|cookies|config|token/);
}
function expectException(error: Error, prefix = '[runs] read failed:') {
  expect(console.error).toHaveBeenCalledExactlyOnceWith(prefix, error.stack);
  expect(error.stack).toMatch(/\n\s+at /);
  expect(diagnostics).toEqual([{ message: error.message, stack: error.stack, stackSource: 'exception' }]);
  expect(console.warn).not.toHaveBeenCalled();
  expectPrivateMetadataAbsent();
}

const run: StoredRunSnapshot = { flow_id: 'run', workflow_id: 'workflow', origin_bot_id: 'bot:owner', status: 'running',
  workflow_title: null, started_at: 1, completed_at: null, total_duration_ms: null, state_json: '{}',
  origin_session_key: null, origin_session_id: null };
function domainReader(stored: StoredRunSnapshot | null) {
  const store = { readRun: vi.fn().mockResolvedValue(stored), listRuns: vi.fn(), readIdentities: vi.fn(),
    readFanoutOriginIdentities: vi.fn(), readAssociatedIdentities: vi.fn() } satisfies RunSubworkflowsStore;
  const permissions = { resolveRunViewScope: vi.fn().mockResolvedValue('deny'), listRunViewScopes: vi.fn() } satisfies RunSubviewPermissions;
  return { reader: new RunSubworkflowsService(store, permissions), store, permissions };
}

describe('actual runs read HTTP adapter logging', () => {
  it('retains the default list countRuns failure stack with the unchanged generic 500 response', async () => {
    const error = persistenceFailure();
    const countRuns = vi.fn().mockRejectedValue(error);
    const base = await listen({ countRuns } as unknown as FlowRunRepository);
    const response = await fetch(base);
    expect(response.status).toBe(500);
    expect(await response.json()).toEqual({ error: 'read_failed', message: 'Unable to read persisted run data' });
    expect(countRuns).toHaveBeenCalledOnce();
    expectException(error);
    expect(error.stack).toContain('persistenceFailure');
  });

  it.each([
    { status: 401, code: 'unauthenticated', message: 'User identity required', user: false, stored: run },
    { status: 403, code: 'forbidden', message: 'Run view permission required', user: true, stored: run },
    { status: 404, code: 'not_found', message: 'Run not found', user: true, stored: null },
  ])('preserves real domain authorization/missing-run $status and emits code-only WARN', async ({ status, code, message, user, stored }) => {
    const { reader, store, permissions } = domainReader(stored);
    const base = await listen(null, reader);
    const response = await fetch(`${base}/run`, { headers: user ? { 'x-user-id': 'viewer' } : {} });
    expect(response.status).toBe(status);
    expect(await response.json()).toEqual({ error: code, message });
    expect(console.warn).toHaveBeenCalledExactlyOnceWith('[runs] read rejected:', { code });
    expect(console.error).not.toHaveBeenCalled();
    expect(diagnostics).toEqual([undefined]);
    if (!user) expect(store.readRun).not.toHaveBeenCalled();
    else expect(store.readRun).toHaveBeenCalledExactlyOnceWith('run');
    if (status === 403) expect(permissions.resolveRunViewScope).toHaveBeenCalledExactlyOnceWith('workflow', 'viewer');
    else expect(permissions.resolveRunViewScope).not.toHaveBeenCalled();
  });

  it.each<FanoutReadErrorCode>(['unauthenticated', 'forbidden', 'not_found', 'invalid_query', 'identity_conflict', 'snapshot_changed'])(
    'does not inspect or log the raw domain %s error/stack', async code => {
      const error = Object.assign(new FanoutReadError(code, '<private-domain-message>'), { token: '<private-token-marker>' });
      Object.defineProperty(error, 'stack', { get() { throw new Error('domain rejection stack must not be inspected'); } });
      const base = await listen({} as FlowRunRepository, readerRejecting(error));
      const response = await fetch(base);
      const statuses = { unauthenticated: 401, forbidden: 403, not_found: 404, invalid_query: 400, identity_conflict: 409, snapshot_changed: 409 };
      expect(response.status).toBe(statuses[code as keyof typeof statuses]);
      expect(await response.json()).toEqual({ error: code, message: error.message });
      expect(console.warn).toHaveBeenCalledExactlyOnceWith('[runs] read rejected:', { code });
      expect(console.error).not.toHaveBeenCalled();
      expect(diagnostics).toEqual([undefined]);
      expectPrivateMetadataAbsent();
    });

  it.each([{ code: 'invalid_state', status: 500 }, { code: 'unavailable', status: 503 }] as const)(
    'hands off the actual domain system $status stack without altering its response', async ({ code, status }) => {
      const error = Object.assign(new FanoutReadError(code, 'durable run state unavailable'), { config: { token: '<private-token-marker>' } });
      const base = await listen({} as FlowRunRepository, readerRejecting(error));
      const response = await fetch(base);
      expect(response.status).toBe(status);
      expect(await response.json()).toEqual({ error: code, message: error.message });
      expectException(error);
    });

  it.each(['/run', '/run/nodes/group/subworkflows?groupExecutionId=group'])(
    'retains the original ordinary read exception through %s', async path => {
      const error = persistenceFailure();
      const base = await listen(null, readerRejecting(error));
      const response = await fetch(`${base}${path}`);
      expect(response.status).toBe(500);
      expect(response.headers.get('cache-control')).toBe('private, no-store');
      expect(await response.json()).toEqual({ error: 'read_failed', message: 'Unable to read persisted run data' });
      expectException(error);
    });

  it('labels non-Error system throws as observation stacks without exposing extensions', async () => {
    const error = { message: 'run storage unavailable', get config() { throw new Error('must not inspect config'); } };
    const base = await listen({} as FlowRunRepository, readerRejecting(error));
    const response = await fetch(base);
    expect(response.status).toBe(500);
    expect(await response.json()).toEqual({ error: 'read_failed', message: 'Unable to read persisted run data' });
    expect(diagnostics).toEqual([{ message: error.message, stack: expect.stringContaining('Workflow API failure observed: run storage unavailable'), stackSource: 'observation' }]);
    const stack = (diagnostics[0] as { stack: string }).stack;
    expect(stack).toMatch(/\n\s+at /);
    expect(console.error).toHaveBeenCalledExactlyOnceWith('[runs] read failed:', stack);
    expectPrivateMetadataAbsent();
  });

  it.each([{ method: 'PATCH', path: 'session' }, { method: 'POST', path: 'abort' }])(
    'retains real storage failure stack in the $method $path guard without allowing a write', async ({ method, path }) => {
      const error = persistenceFailure();
      const { reader, store } = domainReader(run);
      store.readRun.mockRejectedValue(error);
      const updateSessionInfo = vi.fn();
      const base = await listen({ updateSessionInfo } as unknown as FlowRunRepository, reader);
      const response = await fetch(`${base}/run/${path}`, { method, headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ originSessionKey: 'new-session' }) });
      expect(response.status).toBe(503);
      expect(await response.json()).toEqual({ error: 'Service Unavailable', message: 'Run state unavailable' });
      expect(store.readRun).toHaveBeenCalledExactlyOnceWith('run');
      expect(updateSessionInfo).not.toHaveBeenCalled();
      expectException(error, '[runs] write guard failed:');
    });
});
