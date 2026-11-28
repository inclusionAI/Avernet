import express from 'express';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { callbackCompensationSecurity, createCallbackCompensationsRouter } from '../callback-compensations.js';
import type { CallbackCompensationPrincipal } from '../callback-compensations.js';
import type { AddressInfo } from 'node:net';

const servers: import('node:http').Server[] = [];
afterEach(async () => { await Promise.all(servers.splice(0).map(server => new Promise<void>(resolve => server.close(() => resolve())))); });
async function start(actor: CallbackCompensationPrincipal | null = { actorId: 'admin', isAdmin: true }) {
  const service = { capabilities: vi.fn(async () => ({ environment: 'test', configs: [] })),
    preview: vi.fn(async () => ({ previewId: 'preview-1' })), create: vi.fn(async () => ({ batchId: 'batch-1' })),
    get: vi.fn(async () => ({ batchId: 'batch-1' })), stop: vi.fn(async () => ({ status: 'stopped' })) };
  const principal = vi.fn(async () => actor);
  const app = express();
  app.use(callbackCompensationSecurity(['https://console.example.test']));
  app.use(express.json());
  app.use('/api/workflows', createCallbackCompensationsRouter({ service: service as never, principal }));
  const server = app.listen(0, '127.0.0.1'); servers.push(server);
  await new Promise<void>(resolve => server.once('listening', resolve));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/api/workflows/wf/callback-compensations`;
  const headers = { Origin: 'https://console.example.test', 'Content-Type': 'application/json', 'X-Callback-Compensation-CSRF': '1' };
  return { service, principal, base, headers };
}
describe('callback compensation authorization and delivery adapter', () => {
  it('does not accept an asserted header administrator', async () => {
    const { base, service } = await start(null);
    const r = await fetch(`${base}/capabilities`, { headers: { 'X-User-Id': 'admin' } });
    expect(r.status).toBe(401); expect(service.capabilities).not.toHaveBeenCalled();
  });
  it('does not grant ordinary edit/view identities compensation access', async () => {
    const { base, service } = await start({ actorId: 'editor', isAdmin: false });
    expect((await fetch(`${base}/capabilities`)).status).toBe(403);
    expect(service.capabilities).not.toHaveBeenCalled();
  });
  it('denies foreign origins, missing CSRF, no-origin writes and form bodies before service calls', async () => {
    const { base, headers, service } = await start();
    for (const h of [{ ...headers, Origin: 'https://evil.example.test' }, { Origin: headers.Origin, 'Content-Type': 'application/json' },
      { 'Content-Type': 'application/json', 'X-Callback-Compensation-CSRF': '1' }, { ...headers, 'Content-Type': 'text/plain' }]) {
      expect((await fetch(`${base}/preview`, { method: 'POST', headers: h, body: '{}' })).status).toBe(403);
    }
    expect(service.preview).not.toHaveBeenCalled();
  });
  it('returns only approved CORS preflights', async () => {
    const { base, headers } = await start();
    const ok = await fetch(base, { method: 'OPTIONS', headers: { Origin: headers.Origin,
      'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'content-type,x-callback-compensation-csrf' } });
    expect(ok.status).toBe(204); expect(ok.headers.get('access-control-allow-origin')).toBe(headers.Origin);
    const bad = await fetch(base, { method: 'OPTIONS', headers: { Origin: headers.Origin, 'Access-Control-Request-Method': 'DELETE' } });
    expect(bad.status).toBe(403);
  });
  it('rejects arbitrary targets/secrets and invalid selection types', async () => {
    const { base, headers, service } = await start();
    for (const value of [{ configId: 'c', from: 'a', to: 'b', url: 'https://evil.example.test' },
      { configId: 'c', from: 'a', to: 'b', flowIds: [4] }, { configId: 'c', from: 'a', to: 'b', secret: 'not-a-secret' }]) {
      expect((await fetch(`${base}/preview`, { method: 'POST', headers, body: JSON.stringify(value) })).status).toBe(400);
    }
    expect(service.preview).not.toHaveBeenCalled();
  });
  it('passes only verified actor and path workflow, preserves request key and returns 202', async () => {
    const { base, headers, service } = await start();
    const value = { previewId: 'p', itemIds: ['i'], reason: 'Reviewed missing notification', requestKey: 'stable-request-key' };
    const r = await fetch(base, { method: 'POST', headers, body: JSON.stringify(value) });
    expect(r.status).toBe(202); expect(r.headers.get('cache-control')).toBe('no-store');
    expect(service.create).toHaveBeenCalledWith('wf', 'admin', value);
  });
  it('requires fresh administrator authorization for status and stop', async () => {
    const { base, headers, principal, service } = await start();
    expect((await fetch(`${base}/batch-1`)).status).toBe(200);
    principal.mockResolvedValue(null as never);
    expect((await fetch(`${base}/batch-1/stop`, { method: 'POST', headers, body: '{}' })).status).toBe(401);
    expect(service.stop).not.toHaveBeenCalled();
  });
  it('redacts service/authorization exceptions rather than returning database or transport messages', async () => {
    const { base, service } = await start();
    service.capabilities.mockRejectedValue(new Error('database body contains private credentials'));
    const r = await fetch(`${base}/capabilities`);
    expect(r.status).toBe(503); expect(await r.text()).not.toMatch(/credentials|database body/);
  });
  it('does not consume unrelated workflow routes', async () => {
    const guard = callbackCompensationSecurity([]); const next = vi.fn();
    guard({ path: '/api/workflows/wf/history' } as never, {} as never, next);
    expect(next).toHaveBeenCalledOnce();
  });
});
