import { afterEach, expect, it, vi } from 'vitest';
import express from 'express';
import { once } from 'node:events';
import { randomUUID } from 'node:crypto';
import { workflowAiApi } from '@avernet/workflow/web/api/workflow-ai';
import { aiDraftFixture, overlayYaml } from './workflow-ai-draft-db-fixture.js';
import { createWorkflowAiDraftRuntime } from '../workflow-ai-draft-runtime.js';

vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({
  getClientUser: () => ({ userId: 'owner', nickName: 'Synthetic owner' }),
}));
const cleanup: Array<() => Promise<unknown>> = [];
afterEach(async () => {
  vi.unstubAllGlobals();
  for (const close of cleanup.splice(0).reverse()) await close();
});

it('connects the actual browser transport to the protected runtime: gzip, CSRF, idempotency and cancellation', async () => {
  const f = await aiDraftFixture(); cleanup.push(() => f.db.close());
  const runtime = createWorkflowAiDraftRuntime(f.db, { principal: async () => ({ actorId: 'owner' }), execution: f.execution });
  const app = express();
  const observed: Array<{ method: string; csrf: string | undefined; encoding: string | undefined }> = [];
  app.use((req, _res, next) => {
    observed.push({ method: req.method, csrf: req.get('x-pack-version-csrf'), encoding: req.get('content-encoding') }); next();
  });
  app.use(express.json({ limit: '200kb' })); app.use('/api/workflows', runtime.router);
  const server = app.listen(0, '127.0.0.1'); await once(server, 'listening');
  cleanup.push(() => new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve())));
  const origin = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  const realFetch = globalThis.fetch;
  // Resolve browser-relative URLs only; the real shared fetchJson, network,
  // JSON/gzip parser, CSRF guard, ACL, SQL service and lifecycle all execute.
  vi.stubGlobal('fetch', (input: string | URL | Request, init?: RequestInit) => realFetch(
    typeof input === 'string' && input.startsWith('/') ? origin + input : input, init));
  const signal = new AbortController().signal;
  expect(await workflowAiApi.capabilities('flow', signal)).toEqual({ available: true });
  const input = { requestId: randomUUID(), mode: 'modify' as const, prompt: 'Synthetic offline request',
    currentYaml: overlayYaml + 'description: ' + 'x'.repeat(40_000) + '\n' };
  const submitted = await workflowAiApi.generate('flow', input, signal);
  expect(submitted.status).toBe('running');
  expect((await workflowAiApi.generate('flow', input, signal)).draftId).toBe(submitted.draftId);
  expect(f.execution.dispatch).toHaveBeenCalledOnce();
  expect((await workflowAiApi.poll('flow', submitted.draftId, signal)).status).toBe('running');
  expect((await workflowAiApi.cancel('flow', submitted.draftId, signal)).status).toBe('cancelled');
  expect((await workflowAiApi.poll('flow', submitted.draftId, signal)).status).toBe('cancelled');
  expect(observed.filter(request => request.method === 'POST').every(request => request.csrf === '1')).toBe(true);
  expect(observed.some(request => request.encoding === 'gzip')).toBe(true);
  expect(f.raw.prepare("SELECT spec_json FROM workflow_specs WHERE workflow_id = 'flow'").get()).toEqual({ spec_json: '{}' });
});
