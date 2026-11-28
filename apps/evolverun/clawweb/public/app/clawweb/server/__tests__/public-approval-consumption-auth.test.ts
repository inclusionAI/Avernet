// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { generateKeyPairSync, sign, type KeyObject } from 'node:crypto';
import { once } from 'node:events';
import type { Server } from 'node:http';
import Database from 'better-sqlite3';
import express from 'express';
import { SqliteDatabase, runMigrations, type IDatabase } from '@avernet/clawweb-shared/server/db';
import { ApprovalCardRepository } from '@avernet/workflow/server/repositories/approval-card-repository';
import { invalidateResolvedCache } from '@avernet/workflow/server/routes/internal/approval-cards';
import type { ClawWebLifecycle } from '../bootstrap.js';

const state = vi.hoisted(() => ({ db: null as IDatabase | null, publicKeyB64: '' }));
vi.mock('@avernet/clawweb-shared/server/db', async original => ({
  ...await original<typeof import('@avernet/clawweb-shared/server/db')>(),
  initDatabase: async () => ({ db: state.db }), closeDatabase: vi.fn(),
  resolveSignatureConfig: () => ({ publicKeyB64: state.publicKeyB64 }),
}));
// Only unrelated providers and DB acquisition are replaced. The production
// bootstrap, engine authentication, approval routers, cache and SQL are real.
vi.mock('@avernet/clawevolve', async () => {
  const { Router } = await import('express');
  return { createClawevolveModule: () => ({ start: async () => {}, stop: async () => {}, publicRouter: Router(), benchRouter: Router() }) };
});
vi.mock('@avernet/clawinsight', async () => {
  const { Router } = await import('express');
  return { createInsightRuntime: () => ({ service: {} }), createInsightManagementRuntime: () => ({ router: Router() }), createInsightRouter: () => Router(), createSessionRecoveryRouter: () => Router() };
});
vi.mock('../repair-ais-execution.js', () => ({ resolveRepairAisConfiguration: async () => null }));
import { createClawWebBootstrap } from '../public-bootstrap.js';

const keys = generateKeyPairSync('ed25519');
const forgedKeys = generateKeyPairSync('ed25519');
const publicKeyB64 = keys.publicKey.export({ type: 'spki', format: 'der' }).toString('base64');
const wrongAlgorithmKey = generateKeyPairSync('ec', { namedCurve: 'prime256v1' })
  .publicKey.export({ type: 'spki', format: 'der' }).toString('base64');
const createBody = { flow_id: 'new-flow', node_id: 'review', workflow_id: 'workflow', approver_ids: 'reviewer' };
const consumedBody = { consumed: true };
const queries = ['?botId=bot-a', '?botId=bot-b', ''];
const initialIds = [[2, 1], [3, 1], [3, 2, 1]];
let raw: Database.Database;
let lifecycle: ClawWebLifecycle | void;
let server: Server;
let base: string;

function signed(body?: unknown, timestamp = String(Date.now()), privateKey: KeyObject = keys.privateKey) {
  return { 'X-Timestamp': timestamp, 'X-Signature': sign(null,
    Buffer.from(`${timestamp}.${body === undefined ? '' : JSON.stringify(body)}`), privateKey).toString('base64') };
}
async function start(environment: 'dev' | 'pre' | 'prod', key = publicKeyB64) {
  state.publicKeyB64 = key;
  lifecycle = await createClawWebBootstrap().start({ environment, profile: 'public',
    config: { auth: { admins: ['fixture-admin'] } }, configSources: [] });
  if (!server.listening) await once(server, 'listening');
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
}
async function stop() {
  await lifecycle?.stop?.(); lifecycle = undefined;
}
function request(path: string, method = 'GET', body?: unknown, headers: Record<string, string> = signed(body)) {
  return fetch(base + '/api/approval-cards' + path, { method,
    headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...headers },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
}
async function pollIds(query = '', headers = signed()) {
  const response = await request('/resolved' + query, 'GET', undefined, headers);
  expect(response.status).toBe(200);
  const body = await response.json(); expect(body.success).toBe(true);
  return body.data.map((row: { id: number }) => row.id);
}
async function primeCaches() {
  for (const [index, query] of queries.entries()) expect(await pollIds(query)).toEqual(initialIds[index]);
}
function history() {
  return raw.prepare('SELECT * FROM approval_cards ORDER BY id').all();
}
const invalidKinds = ['anonymous', 'spoofed-identity', 'missing-signature', 'malformed-signature',
  'malformed-timestamp', 'forged', 'expired', 'future', 'tampered-body'] as const;
function invalidHeaders(kind: typeof invalidKinds[number], body?: unknown): Record<string, string> {
  switch (kind) {
    case 'anonymous': return {};
    case 'spoofed-identity': return { 'X-User-Id': 'fixture-admin', cookie: 'staff_id=fixture-admin',
      'X-Forwarded-For': '127.0.0.1', 'X-ClawWeb-Dev-Mode': '1' };
    case 'missing-signature': return { 'X-Timestamp': String(Date.now()) };
    case 'malformed-signature': return { ...signed(body), 'X-Signature': 'not-base64!' };
    case 'malformed-timestamp': return { ...signed(body), 'X-Timestamp': '1e12' };
    case 'forged': return signed(body, String(Date.now()), forgedKeys.privateKey);
    case 'expired': return signed(body, String(Date.now() - 600_000));
    case 'future': return signed(body, String(Date.now() + 600_000));
    case 'tampered-body': return signed({ notTheActualPayload: true });
  }
}

beforeEach(async () => {
  invalidateResolvedCache();
  raw = new Database(':memory:'); state.db = new SqliteDatabase(raw);
  await runMigrations(state.db, 'sqlite');
  raw.exec(`INSERT INTO flow_runs (flow_id,workflow_id,status,started_at,origin_bot_id) VALUES
    ('flow-legacy','workflow','waiting',1,NULL),
    ('flow-a','workflow','waiting',1,'bot-a:owner'),
    ('flow-b','workflow','blocked',1,'bot-b:owner'),
    ('flow-pending','workflow','waiting',1,'bot-a:owner');
    INSERT INTO approval_cards (id,flow_id,node_id,workflow_id,approver_ids,status,delivery_mode,
      created_at,resolved_at,approved_by,rejected_by) VALUES
    (1,'flow-legacy','review','workflow','reviewer','approved','card-web',1,10,'reviewer',''),
    (2,'flow-a','review','workflow','reviewer','approved','card-web',1,20,'reviewer',''),
    (3,'flow-b','review','workflow','reviewer','rejected','card-web',1,30,'','reviewer'),
    (4,'flow-pending','review','workflow','reviewer','pending','card-web',1,NULL,'','');`);
  vi.stubEnv('PORT', '0');
  const listen = express.application.listen;
  vi.spyOn(express.application, 'listen').mockImplementation(function (...args: Parameters<typeof listen>) {
    server = listen.apply(this, args); return server;
  });
});
afterEach(async () => {
  await stop();
  invalidateResolvedCache();
  await state.db?.close(); state.db = null;
  vi.restoreAllMocks(); vi.unstubAllEnvs();
});

it.each(invalidKinds)('rejects %s production engine reads/writes before ACK persistence or cache invalidation', async kind => {
  await start('prod');
  const find = vi.spyOn(ApprovalCardRepository.prototype, 'findResolvedCardWeb');
  const ack = vi.spyOn(ApprovalCardRepository.prototype, 'markProcessed');
  const create = vi.spyOn(ApprovalCardRepository.prototype, 'create');
  await primeCaches();
  const before = history();
  for (const [path, method, body] of [['/2/processed', 'POST', consumedBody], ['/resolved?botId=bot-a', 'GET', undefined],
    ['/', 'POST', createBody]] as const) {
    const response = await request(path, method, body, invalidHeaders(kind, body));
    expect(response.status).toBe(401);
    expect(response.headers.get('cache-control')).toBe('private, no-store');
    expect(await response.json()).toMatchObject({ success: false, error: 'Unauthorized' });
    expect(history()).toEqual(before);
    await primeCaches();
    expect(find).toHaveBeenCalledTimes(3);
  }
  expect(ack).not.toHaveBeenCalled(); expect(create).not.toHaveBeenCalled();
});

it.each([['missing', ''], ['invalid', 'not-an-ed25519-key'], ['wrong algorithm', wrongAlgorithmKey]])(
  'fails closed with %s production key configuration and retains previously populated caches', async (_kind, key) => {
    await start('prod');
    const find = vi.spyOn(ApprovalCardRepository.prototype, 'findResolvedCardWeb');
    const ack = vi.spyOn(ApprovalCardRepository.prototype, 'markProcessed');
    const create = vi.spyOn(ApprovalCardRepository.prototype, 'create');
    await primeCaches();
    await stop();
    await start('prod', key);
    const before = history();
    for (const [path, method, body] of [['/2/processed', 'POST', consumedBody], ['/resolved?botId=bot-a', 'GET', undefined],
      ['/', 'POST', createBody]] as const) {
      const response = await request(path, method, body);
      expect(response.status).toBe(503);
      expect(await response.json()).toMatchObject({ success: false, error: 'Service Unavailable' });
      expect(history()).toEqual(before);
    }
    expect(ack).not.toHaveBeenCalled(); expect(create).not.toHaveBeenCalled();
    await stop();
    await start('prod');
    await primeCaches();
    expect(find).toHaveBeenCalledTimes(3);
  },
);

it.each([
  { name: 'bodyless', body: undefined },
  { name: 'empty object', body: {} },
  { name: 'object without consumed', body: { legacy: true } },
])('accepts a signed legacy $name ACK without consuming a waiting card or invalidating caches', async ({ body }) => {
  await start('prod');
  const find = vi.spyOn(ApprovalCardRepository.prototype, 'findResolvedCardWeb');
  const ack = vi.spyOn(ApprovalCardRepository.prototype, 'markProcessed');
  await primeCaches();
  const before = history();
  const response = await request('/2/processed', 'POST', body);
  expect(response.status).toBe(200); expect(await response.json()).toEqual({ success: true });
  expect(ack).toHaveBeenCalledWith(2, false);
  expect(history()).toEqual(before);
  expect((await new ApprovalCardRepository(state.db!).findById(2))?.processed_at).toBeNull();
  await primeCaches();
  expect(find).toHaveBeenCalledTimes(3);
  // An uncached read must retain the card too, not only the pre-ACK cache.
  invalidateResolvedCache();
  await primeCaches();
  expect(find).toHaveBeenCalledTimes(6);
});

it('rejects a legacy-body signature reused for a consumed-v1 ACK without persistence or cache invalidation', async () => {
  await start('prod');
  const find = vi.spyOn(ApprovalCardRepository.prototype, 'findResolvedCardWeb');
  const ack = vi.spyOn(ApprovalCardRepository.prototype, 'markProcessed');
  await primeCaches();
  const before = history();
  const response = await request('/2/processed', 'POST', consumedBody, signed({}));
  expect(response.status).toBe(401);
  expect(response.headers.get('cache-control')).toBe('private, no-store');
  expect(await response.json()).toMatchObject({ success: false, error: 'Unauthorized' });
  expect(ack).not.toHaveBeenCalled(); expect(history()).toEqual(before);
  await primeCaches();
  expect(find).toHaveBeenCalledTimes(3);
});

it.each([
  { name: 'null body', body: null },
  { name: 'array body', body: [] },
  { name: 'boolean body', body: true },
  { name: 'number body', body: 1 },
  { name: 'string body', body: 'consumed' },
  { name: 'false consumed', body: { consumed: false } },
  { name: 'null consumed', body: { consumed: null } },
  { name: 'numeric consumed', body: { consumed: 1 } },
  { name: 'string consumed', body: { consumed: 'true' } },
  { name: 'object consumed', body: { consumed: {} } },
  { name: 'array consumed', body: { consumed: [] } },
])('rejects a signed malformed $name ACK without persistence or cache invalidation', async ({ body }) => {
  await start('prod');
  const find = vi.spyOn(ApprovalCardRepository.prototype, 'findResolvedCardWeb');
  const ack = vi.spyOn(ApprovalCardRepository.prototype, 'markProcessed');
  await primeCaches();
  const before = history();
  const response = await request('/2/processed', 'POST', body);
  expect(response.status).toBe(400);
  // Strict JSON parsing may reject scalar/null bodies before the engine guard.
  expect(ack).not.toHaveBeenCalled(); expect(history()).toEqual(before);
  await primeCaches();
  expect(find).toHaveBeenCalledTimes(3);
});

it.each([['approved', 2], ['rejected', 3]] as const)(
  'permits signed GET and durable %s ACK while preserving Bot isolation and the first timestamp', async (_status, id) => {
    await start('prod');
    const find = vi.spyOn(ApprovalCardRepository.prototype, 'findResolvedCardWeb');
    await primeCaches();
    const before = history();
    const now = Date.now(); const clock = vi.spyOn(Date, 'now').mockReturnValue(now);
    const response = await request(`/${id}/processed`, 'POST', consumedBody);
    expect(response.status).toBe(200); expect(await response.json()).toEqual({ success: true });
    const card = await new ApprovalCardRepository(state.db!).findById(id);
    expect(card).toMatchObject({ id, processed_at: Math.floor(now / 1000) });
    const { processed_at: _processed, gmt_modified: _modified, ...audit } = card! as typeof card & { gmt_modified: number };
    const original = before.find((row: { id: number }) => row.id === id) as Record<string, unknown>;
    const { processed_at: _oldProcessed, gmt_modified: _oldModified, ...oldAudit } = original;
    expect(audit).toEqual(oldAudit);
    for (const [index, query] of queries.entries()) {
      expect(await pollIds(query)).toEqual(initialIds[index].filter(value => value !== id));
    }
    expect(find).toHaveBeenCalledTimes(6);
    clock.mockReturnValue(now + 1_000);
    const repeated = await request(`/${id}/processed`, 'POST', consumedBody);
    expect(repeated.status).toBe(200); expect(await repeated.json()).toEqual({ success: true });
    expect((await new ApprovalCardRepository(state.db!).findById(id))?.processed_at).toBe(Math.floor(now / 1000));
    for (const [index, query] of queries.entries()) {
      expect(await pollIds(query)).toEqual(initialIds[index].filter(value => value !== id));
    }
    expect(find).toHaveBeenCalledTimes(9);
    const consumedHistory = history();
    for (const legacyBody of [undefined, {}, { legacy: true }]) {
      const legacy = await request(`/${id}/processed`, 'POST', legacyBody);
      expect(legacy.status).toBe(200); expect(await legacy.json()).toEqual({ success: true });
      expect(history()).toEqual(consumedHistory);
      for (const [index, query] of queries.entries()) {
        expect(await pollIds(query)).toEqual(initialIds[index].filter(value => value !== id));
      }
      expect(find).toHaveBeenCalledTimes(9);
    }
  },
);

it('accepts signed JSON card creation only after verifying the actual payload', async () => {
  await start('prod');
  const before = history();
  const denied = await request('/', 'POST', createBody, signed({ ...createBody, flow_id: 'tampered' }));
  expect(denied.status).toBe(401); expect(history()).toEqual(before);
  const response = await request('/', 'POST', createBody);
  expect(response.status).toBe(201);
  const body = await response.json(); expect(body.success).toBe(true);
  expect(await new ApprovalCardRepository(state.db!).findById(body.data.id))
    .toMatchObject({ flow_id: 'new-flow', status: 'pending', processed_at: null });
});

it.each(['dev', 'prod'] as const)('leaves the user-facing approval pages and session authorization unchanged in %s', async environment => {
  await start(environment);
  const detail = await fetch(base + '/api/approval/4');
  expect(detail.status).toBe(200); expect(await detail.json()).toMatchObject({ id: 4, status: 'pending' });
  const body = { action: 'approve' };
  const response = await fetch(base + '/api/approval/4/resolve/session', { method: 'POST',
    headers: { 'Content-Type': 'application/json', cookie: 'staff_id=stranger' }, body: JSON.stringify(body) });
  // A dev session reaches the existing approver check without an engine signature.
  // Production still has no verified browser session, even if the engine signs.
  expect(response.status).toBe(environment === 'dev' ? 403 : 401);
  if (environment === 'prod') {
    const engine = await fetch(base + '/api/approval/4/resolve/session', { method: 'POST',
      headers: { 'Content-Type': 'application/json', cookie: 'staff_id=reviewer', ...signed(body) }, body: JSON.stringify(body) });
    expect(engine.status).toBe(401);
  }
  expect(await new ApprovalCardRepository(state.db!).findById(4))
    .toMatchObject({ status: 'pending', processed_at: null });
});

it('retains the existing unsigned dev socket-loopback policy only when no key is configured', async () => {
  await start('dev', '');
  expect(await pollIds('?botId=bot-a', {})).toEqual([2, 1]);
  const response = await request('/2/processed', 'POST', consumedBody, {});
  expect(response.status).toBe(200);
  expect((await new ApprovalCardRepository(state.db!).findById(2))?.processed_at).toEqual(expect.any(Number));
});
