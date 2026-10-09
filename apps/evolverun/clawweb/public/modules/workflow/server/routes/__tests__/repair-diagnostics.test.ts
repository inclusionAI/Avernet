// @vitest-environment node
import { afterEach, expect, it, vi } from 'vitest';
import express from 'express';
import type { Server } from 'node:http';
import { createRepairAuthorizer } from '../repair-authorization.js';
import { createRepairBatchesRouter } from '../repair-batches.js';
import { createRepairWorkbenchService } from '../../services/repair-batch-service.js';
import { repairFixture } from '../../services/__tests__/repair-batch-fixtures.js';

afterEach(() => vi.restoreAllMocks());

it('correlates concurrent allow and deny decisions without logging request secrets', async () => {
  const logs: unknown[][] = [];
  vi.spyOn(console, 'info').mockImplementation((...args) => { logs.push(args); });
  vi.spyOn(console, 'warn').mockImplementation((...args) => { logs.push(args); });
  const f = await repairFixture();
  const app = express();
  const authorize = createRepairAuthorizer(async req => {
    // Test host boundary, not a production header-based identity resolver.
    const role = req.get('x-test-role');
    return role === 'missing' ? null : { actorId: role!, isAdmin: false };
  }, { hasEditPermission: async () => false,
    resolveViewScope: async (_wf, actor) => actor === 'viewer' ? 'all' : { botIds: ['private-bot'] } });
  app.use('/api/workflow-repairs', createRepairBatchesRouter({ authorize,
    service: createRepairWorkbenchService(f.db, f.sourcePort) }));
  const server = await new Promise<Server>(resolve => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
  try {
    const url = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/workflow-repairs/candidates?workflowId=wf-1`;
    const responses = await Promise.all(['missing', 'limited', 'viewer'].map(role => fetch(url, {
      headers: { 'x-test-role': role, cookie: 'session=do-not-log', authorization: 'Bearer do-not-log', 'x-request-id': 'untrusted' },
    })));
    expect(responses.map(r => r.status)).toEqual([403, 403, 200]);
    const requestIds = responses.map(r => r.headers.get('x-repair-request-id'));
    expect(requestIds.every(id => /^[a-f0-9-]{36}$/.test(id ?? ''))).toBe(true);
    expect(new Set(requestIds).size).toBe(3);
    const denied = await responses[0].json();
    expect(denied).toMatchObject({ code: 'FORBIDDEN', requestId: requestIds[0] });
    const records = logs.filter(row => row[0] === '[workflow-repair] request diagnostic').map(row => JSON.parse(String(row[1])));
    expect(records).toContainEqual(expect.objectContaining({ requestId: requestIds[0], status: 403, reason: 'MISSING_IDENTITY', actorId: null }));
    expect(records).toContainEqual(expect.objectContaining({ requestId: requestIds[1], status: 403, reason: 'INSUFFICIENT_VIEW_SCOPE', actorId: 'limited', viewScope: 'limited' }));
    expect(records).toContainEqual(expect.objectContaining({ requestId: requestIds[2], status: 200, reason: 'ALLOWED', actorId: 'viewer',
      instance: expect.any(String), stagesMs: expect.objectContaining({ principal: expect.any(Number), permissions: expect.any(Number), stored_items: expect.any(Number), sources_summary: expect.any(Number), revisions: expect.any(Number) }) }));
    expect(JSON.stringify(records)).not.toMatch(/do-not-log|private-bot|untrusted/);
    expect(records.filter(row => row.status === 403).every(row => !('sources_summary' in row.stagesMs))).toBe(true);
  } finally {
    await new Promise<void>(resolve => server.close(() => resolve()));
    await f.db.close();
  }
});
