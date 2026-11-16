import { afterEach, describe, expect, it, vi } from 'vitest';
import { generateKeyPairSync, sign, type KeyObject } from 'node:crypto';
import { once } from 'node:events';
import type { Server } from 'node:http';
import express from 'express';
import { createInternalEngineAuth } from '../internal-engine-auth.js';

const keys = generateKeyPairSync('ed25519');
const keyB64 = keys.publicKey.export({ type: 'spki', format: 'der' }).toString('base64');
const privateKeyB64 = Buffer.from(keys.privateKey.export({ type: 'pkcs8', format: 'pem' })).toString('base64');
const servers: Server[] = [];
afterEach(async () => {
  for (const server of servers.splice(0)) await new Promise<void>(resolve => { server.closeAllConnections(); server.close(() => resolve()); });
  vi.restoreAllMocks();
});
async function fixture(environment: 'dev' | 'pre' | 'prod', publicKeyB64: string, remoteAddress?: string | null) {
  const app = express(); app.set('trust proxy', true); app.use(express.json());
  // Only the transport peer is substituted to simulate nonlocal sockets in a local test.
  if (remoteAddress !== undefined) app.use((req, _res, next) => {
    Object.defineProperty(req.socket, 'remoteAddress', { configurable: true, value: remoteAddress ?? undefined }); next();
  });
  app.use(createInternalEngineAuth({ environment, publicKeyB64 }));
  let accepted = 0;
  app.all('/engine', (req, res) => { accepted++; res.json({ accepted, body: req.body ?? null }); });
  const server = app.listen(0, '127.0.0.1'); servers.push(server); await once(server, 'listening');
  const base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  return { base, accepted: () => accepted };
}
function headers(body = '', timestamp = String(Date.now()), privateKey: KeyObject = keys.privateKey) {
  return { 'X-Timestamp': timestamp, 'X-Signature': sign(null, Buffer.from(`${timestamp}.${body}`), privateKey).toString('base64') };
}
function request(base: string, method: string, body?: unknown, auth: Record<string, string> = headers(body === undefined ? '' : JSON.stringify(body))) {
  return fetch(base + '/engine', { method, headers: { 'Content-Type': 'application/json', ...auth }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
}

describe('strict internal engine authentication', () => {
  it('accepts the documented JSON body signature without an enterprise client or browser identity', async () => {
    const f = await fixture('prod', keyB64);
    const body = { flow_id: 'run', status: 'running', nested: { text: '中文', list: [1, null] } };
    const timestamp = String(Date.now());
    const signature = sign(null, Buffer.from(`${timestamp}.${JSON.stringify(body)}`), keys.privateKey).toString('base64');
    const response = await fetch(f.base + '/engine', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Timestamp': timestamp, 'X-Signature': signature },
      body: JSON.stringify(body),
    });
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ accepted: 1, body });
  });
  it.each(['GET', 'DELETE', 'HEAD'])('uses the empty-body signature for %s', async method => {
    const f = await fixture('prod', keyB64);
    const response = await request(f.base, method); expect(response.status).toBe(200);
    expect(response.headers.get('cache-control')).toBe('private, no-store');
  });
  it('rejects body tampering and forged keys without reaching the protected operation', async () => {
    const f = await fixture('dev', keyB64);
    expect((await request(f.base, 'POST', { value: 'changed' }, headers(JSON.stringify({ value: 'signed' })))).status).toBe(401);
    expect((await request(f.base, 'POST', {}, headers('{}', String(Date.now()), generateKeyPairSync('ed25519').privateKey))).status).toBe(401);
    expect(f.accepted()).toBe(0);
  });
  it.each([-300001, 300001])('rejects correctly signed timestamps outside the window (%s ms)', async delta => {
    const now = Date.now(); vi.spyOn(Date, 'now').mockReturnValue(now);
    const f = await fixture('prod', keyB64);
    expect((await request(f.base, 'PUT', {}, headers('{}', String(now + delta)))).status).toBe(401);
    expect(f.accepted()).toBe(0);
  });
  it.each(['0', '-1', 'NaN', 'Infinity', '1e12', ' 123 ', '9007199254740992'])('rejects malformed timestamp %s', async timestamp => {
    const f = await fixture('prod', keyB64);
    expect((await request(f.base, 'POST', {}, headers('{}', timestamp))).status).toBe(401);
    expect(f.accepted()).toBe(0);
  });
  it('rejects missing, malformed and duplicate signature headers even on localhost', async () => {
    const f = await fixture('dev', keyB64);
    const valid = headers('{}');
    for (const auth of [{}, { 'X-Timestamp': valid['X-Timestamp'] },
      { ...valid, 'X-Signature': valid['X-Signature'] + 'garbage' },
      { ...valid, 'X-Signature': valid['X-Signature'] + ',' + valid['X-Signature'] },
      { ...valid, 'X-Timestamp': valid['X-Timestamp'] + ',' + valid['X-Timestamp'] }]) {
      expect((await request(f.base, 'POST', {}, auth)).status).toBe(401);
    }
    expect(f.accepted()).toBe(0);
  });
  it('does not authenticate a DELETE payload using the empty-body signature', async () => {
    const f = await fixture('prod', keyB64);
    expect((await request(f.base, 'DELETE', { unsafe: true }, headers())).status).toBe(401);
    expect(f.accepted()).toBe(0);
  });
  it.each(['dev', 'pre', 'prod'] as const)('validates signed requests from a nonloopback peer in %s when configured', async environment => {
    const f = await fixture(environment, keyB64, '192.0.2.10');
    expect((await request(f.base, 'PUT', { valid: true })).status).toBe(200);
    expect((await request(f.base, 'PUT', { valid: true }, {})).status).toBe(401);
    expect(f.accepted()).toBe(1);
  });
  it.each(['127.0.0.1', '127.2.3.4', '::1', '0:0:0:0:0:0:0:1', '::ffff:127.0.0.1', '::ffff:7f00:1'])('allows unsigned dev requests only for socket loopback %s', async address => {
    const f = await fixture('dev', '', address);
    expect((await request(f.base, 'POST', {}, { 'X-Forwarded-For': '192.0.2.10' })).status).toBe(200);
  });
  it.each(['192.0.2.10', '::ffff:192.0.2.10', '::', 'localhost', '127.0.0.1.evil', null])('ignores forwarded identity for nonloopback/unknown socket %s', async address => {
    const f = await fixture('dev', '', address);
    expect((await request(f.base, 'POST', {}, { 'X-Forwarded-For': '127.0.0.1', 'X-Real-IP': '127.0.0.1', Forwarded: 'for=127.0.0.1', 'X-User-Id': 'admin' })).status).toBe(403);
    expect(f.accepted()).toBe(0);
  });
  it.each(['pre', 'prod'] as const)('returns 503 without a key in %s, even with valid-looking headers and a local peer', async environment => {
    const f = await fixture(environment, '');
    expect((await request(f.base, 'POST', {})).status).toBe(503); expect(f.accepted()).toBe(0);
  });
  it.each(['not-base64!', '   ', generateKeyPairSync('ec', { namedCurve: 'prime256v1' }).publicKey.export({ type: 'spki', format: 'der' }).toString('base64')])('fails closed for invalid or non-Ed25519 configuration', async value => {
    const f = await fixture('dev', value);
    expect((await request(f.base, 'POST', {}, {})).status).toBe(503); expect(f.accepted()).toBe(0);
  });
});
