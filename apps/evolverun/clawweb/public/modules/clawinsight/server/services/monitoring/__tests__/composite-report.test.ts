import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import express from 'express';
import { readFileSync } from 'node:fs';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { database } from './test-database.js';
import { initializeMonitoringSqlite } from '../schema.js';
import { createMonitoringRuntime } from '../monitoring-runtime.js';
import { createMonitoringRouter } from '../../../routes/monitoring.js';
import { SqlMonitoringBotDirectory } from '../../../repositories/monitoring-bot-directory.js';
import { MonitoringRepository } from '../../../repositories/monitoring-repository.js';
import { parseCheck, parseDiagnosis, parseQuery } from '../validation.js';
import type { MonitoringTarget } from '../contracts.js';

const now = Date.parse('2026-09-22T06:00:00.000Z');
const a = { botId: 'default', entityId: '001', env: 'test' };
const b = { ...a, entityId: '002' }, prod = { ...a, env: 'prod' };
const scope = { tenant: 'synthetic', allowedTargetEnvs: ['test', 'prod', 'TEST'] };
const fixture = JSON.parse(readFileSync('server/fixtures/monitoring/alert.json', 'utf8'));
const event = (t = a, eventId = 'opaque-original-event') => ({ ...fixture, ...t, eventId, diagnosisId: eventId });
const check = (t = a) => ({ schemaVersion: 'claw-monitoring/bot-check/v2', ...t, engine: 'TE', status: 'HEALTHY',
  checkedAt: new Date(now).toISOString(), lastSuccessfulCheckAt: new Date(now).toISOString() });
let db: IDatabase, directory: SqlMonitoringBotDirectory, repo: MonitoringRepository;
let server: ReturnType<express.Application['listen']> | undefined, url: string;
async function bot(t: MonitoringTarget, tenant = scope.tenant, deleted = 0, engine = 'teclaw') {
  await db.exec(`INSERT INTO ac_bots (bot_id,entity_id,env,avernet_tenant,is_delete,active_engine,owner_id)
    VALUES (?,?,?,?,?,?,?)`, [t.botId, t.entityId, t.env, tenant, deleted, engine, 'synthetic-owner']);
}
async function post(payload: Record<string, unknown>, endpoint: string) {
  const response = await fetch(`${url}/internal/monitoring/${endpoint}`, { method: 'POST', headers: {
    'Content-Type': 'application/json', ...(endpoint === 'diagnosis-events' ? { 'Idempotency-Key': String(payload.eventId) } : {}),
  }, body: JSON.stringify(payload) });
  return { status: response.status, body: await response.json() };
}
async function roster() { return (await fetch(`${url}/monitoring/bots`)).json(); }
function failure(result: { status: number; body: unknown }, status: number, code: string) {
  expect(result).toEqual({ status, body: { error: { code, requestId: expect.any(String) } } });
}
beforeEach(async () => {
  db = database(':memory:'); await initializeMonitoringSqlite(db);
  // Deliberately no UNIQUE constraint: also exercise defensive ambiguity rejection before any schema audit.
  await db.exec(`CREATE TABLE ac_bots (id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id TEXT, entity_id TEXT,
    env TEXT, avernet_tenant TEXT, is_delete INTEGER, active_engine TEXT, owner_id TEXT, bot_name TEXT, owner_name TEXT)`);
  directory = new SqlMonitoringBotDirectory(db, true); repo = new MonitoringRepository(db);
  const runtime = createMonitoringRuntime(() => db, () => now, { directory, scope, isolatedStorageTenant: scope.tenant,
    bindings: [{ reportedBotId: 'legacy-default', target: a }],
    principal: async () => ({ ...scope, staffId: 'synthetic-owner', isAuthenticated: true, isClawInsightAdmin: true }) });
  const app = express(); app.use('/api/insight/v1', createMonitoringRouter(runtime));
  server = await new Promise((resolve, reject) => {
    const s = app.listen(0, '127.0.0.1', () => resolve(s)); s.once('error', reject);
  });
  url = `http://127.0.0.1:${(server!.address() as { port: number }).port}/api/insight/v1`;
});
afterEach(async () => {
  if (server) await new Promise<void>((resolve, reject) => server!.close(error => error ? reject(error) : resolve()));
  server = undefined; vi.restoreAllMocks(); await db.close();
});

describe('v2 compound identity HTTP conformance with the real directory and durable adapter', () => {
  it('keeps same-name bots independent across entity/env, returns exact ACKs and never resolves aliases', async () => {
    const exact = vi.spyOn(directory, 'exact');
    for (const [index, t] of [a, b, prod].entries()) {
      await bot(t);
      expect(await post(event(t, `original-${index}`), 'diagnosis-events')).toEqual({ status: 201,
        body: { accepted: true, stored: true, duplicate: false, eventId: `original-${index}`, ...t } });
      expect(await post(check(t), 'bot-checks')).toEqual({ status: 200, body: { accepted: true, applied: true, ...t } });
      expect(await repo.readStatus(t)).toMatchObject({ count: 1, check: { ...t, status: 'HEALTHY' } });
    }
    expect((await roster()).items).toEqual(expect.arrayContaining([a, b, prod]));
    expect((await roster()).items).toHaveLength(3);
    failure(await post(event({ ...a, botId: 'legacy-default' }), 'diagnosis-events'), 403, 'BOT_IDENTITY_UNAVAILABLE');
    expect(exact).not.toHaveBeenCalled();
  });
  it('preserves opaque event IDs, retries, content/identity conflicts and the original durable record', async () => {
    await bot(a); await bot(b); const wire = event();
    expect((await post(wire, 'diagnosis-events')).status).toBe(201);
    expect(await post(wire, 'diagnosis-events')).toEqual({ status: 200,
      body: { accepted: true, stored: true, duplicate: true, eventId: wire.eventId, ...a } });
    for (const changed of [{ ...wire, systemDiagnosis: 'private changed body' }, { ...wire, ...b }]) {
      failure(await post(changed, 'diagnosis-events'), 409, 'MONITORING_EVENT_CONFLICT');
    }
    expect((await repo.listDiagnoses(a, parseQuery({}))).items[0]?.systemDiagnosis).toBe(wire.systemDiagnosis);
    expect((await repo.readStatus(b)).count).toBe(0);
  });
  it('keeps same-time checks independent and rejects only same-target same-time changes', async () => {
    await bot(a); await bot(b);
    expect((await post(check(a), 'bot-checks')).body.applied).toBe(true);
    expect((await post({ ...check(b), status: 'PAUSED' }, 'bot-checks')).body.applied).toBe(true);
    expect((await post(check(a), 'bot-checks')).body.applied).toBe(false);
    failure(await post({ ...check(a), status: 'ERROR' }, 'bot-checks'), 409, 'MONITORING_CHECK_CONFLICT');
    const older = new Date(now - 1000).toISOString();
    expect((await post({ ...check(a), checkedAt: older, lastSuccessfulCheckAt: older, status: 'ERROR' }, 'bot-checks')).body.applied).toBe(false);
    expect((await repo.readStatus(a)).check?.status).toBe('HEALTHY');
    expect((await repo.readStatus(b)).check?.status).toBe('PAUSED');
  });
  const invalidIdentities = [
    { entityId: undefined }, { env: undefined }, { botId: undefined }, { entityId: null }, { entityId: 1 },
    { entityId: '' }, { entityId: '001 ' }, { env: ' test' }, { botId: 'default ' }, { entityId: '\ud800' },
    { env: 'te\nst' }, { entityId: 'x'.repeat(129) }, { env: 'x'.repeat(21) }, { botId: 'x'.repeat(129) },
  ];
  it.each(invalidIdentities)('rejects invalid identity before lookup or write: %j', async patch => {
    const get = vi.spyOn(directory, 'get'), exec = vi.spyOn(db, 'exec');
    for (const [endpoint, payload] of [['diagnosis-events', event()], ['bot-checks', check()]] as const) {
      failure(await post({ ...payload, ...patch }, endpoint), 400, 'INVALID_IDENTITY');
    }
    expect(get).not.toHaveBeenCalled(); expect(exec).not.toHaveBeenCalled();
  });
  it('rejects v1 even with a complete valid identity, without a compatibility lookup', async () => {
    await bot(a); const get = vi.spyOn(directory, 'get');
    failure(await post({ ...event(), schemaVersion: 'claw-monitoring/diagnosis-event/v1' }, 'diagnosis-events'), 400, 'INVALID_IDENTITY');
    failure(await post({ ...check(), schemaVersion: 'claw-monitoring/bot-check/v1' }, 'bot-checks'), 400, 'INVALID_IDENTITY');
    expect(get).not.toHaveBeenCalled();
  });
  it('hides missing, wrong-tenant, forbidden-env and deleted targets identically and never creates bots', async () => {
    await bot({ ...a, entityId: 'other-tenant' }, 'other');
    await bot({ ...a, entityId: 'deleted' }, scope.tenant, 1);
    await bot({ ...a, env: 'staging' });
    const exec = vi.spyOn(db, 'exec');
    for (const t of [a, { ...a, entityId: 'other-tenant' }, { ...a, entityId: 'deleted' }, { ...a, env: 'staging' }]) {
      failure(await post(event(t), 'diagnosis-events'), 403, 'BOT_IDENTITY_UNAVAILABLE');
      failure(await post(check(t), 'bot-checks'), 403, 'BOT_IDENTITY_UNAVAILABLE');
    }
    expect(exec).not.toHaveBeenCalled();
    expect(await roster()).toEqual({ items: [] });
  });
  it('detects duplicate triplets before engine validation; engine cannot disambiguate identity', async () => {
    await bot(a); await bot(a, scope.tenant, 0, 'openclaw');
    for (const engine of ['OC', 'TE']) {
      failure(await post({ ...event(), engine }, 'diagnosis-events'), 409, 'BOT_IDENTITY_AMBIGUOUS');
      failure(await post({ ...check(), engine }, 'bot-checks'), 409, 'BOT_IDENTITY_AMBIGUOUS');
    }
    expect((await repo.readStatus(a)).count).toBe(0); expect((await repo.readStatus(a)).check).toBeNull();
  });
  it('validates engine after a unique match, without accepting a new engine or modifying the bot', async () => {
    await bot(a);
    failure(await post({ ...event(), engine: 'OC' }, 'diagnosis-events'), 400, 'MONITORING_INVALID_EVENT');
    failure(await post({ ...check(), engine: 'OC' }, 'bot-checks'), 400, 'MONITORING_INVALID_EVENT');
    await db.exec('UPDATE ac_bots SET active_engine = ?', ['openclaw']);
    expect((await post({ ...check(), engine: 'OC' }, 'bot-checks')).status).toBe(200);
  });
  it('preserves leading zeros and exact case; SQL-like entity text stays a bound literal', async () => {
    const mixed = { botId: 'Default', entityId: 'OwnerA', env: 'test' };
    await bot(a); await bot(mixed);
    const query = vi.spyOn(db, 'query');
    for (const t of [{ ...a, entityId: '1' }, { ...a, env: 'TEST' }, { ...a, botId: 'DEFAULT' },
      { ...mixed, entityId: 'ownera' }, { ...a, entityId: "' OR 1=1 --" }]) {
      failure(await post(check(t), 'bot-checks'), 403, 'BOT_IDENTITY_UNAVAILABLE');
    }
    const [sql, params] = query.mock.calls.at(-1)!;
    expect(sql).not.toContain("' OR 1=1 --"); expect(params).toContain("' OR 1=1 --");
    expect(sql).toContain('entity_id COLLATE BINARY = ?'); expect(params?.at(-1)).toBe(2);
    expect((await post(check(a), 'bot-checks')).body.entityId).toBe('001');
    expect((await post(check(mixed), 'bot-checks')).status).toBe(200);
  });
  it('rechecks directory scope for the roster without silently merging defaults', async () => {
    for (const t of [a, b, prod]) { await bot(t); await post(check(t), 'bot-checks'); }
    await db.exec('UPDATE ac_bots SET is_delete = 1 WHERE entity_id = ?', ['002']);
    await db.exec('UPDATE ac_bots SET avernet_tenant = ? WHERE env = ?', ['other', 'prod']);
    expect(await roster()).toEqual({ items: [a] });
  });
  it.each(['diagnosis-events', 'bot-checks'])('waits for actual persistence before ACK: %s', async endpoint => {
    await bot(a);
    let release!: () => void, entered!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    const writing = new Promise<void>(resolve => { entered = resolve; });
    const original = db.exec.bind(db);
    vi.spyOn(db, 'exec').mockImplementation(async (sql, params) => {
      if (sql.startsWith('INSERT INTO insight_monitoring_')) { entered(); await gate; }
      return original(sql, params);
    });
    let settled = false;
    const response = post(endpoint === 'bot-checks' ? check() : event(), endpoint).finally(() => { settled = true; });
    try {
      await writing; expect(settled).toBe(false);
      expect(await repo.readStatus(a)).toEqual({ check: null, count: 0 });
    } finally { release(); }
    expect((await response).status).toBe(endpoint === 'bot-checks' ? 200 : 201);
    const state = await repo.readStatus(a);
    expect(endpoint === 'bot-checks' ? state.check !== null : state.count === 1).toBe(true);
  });
  it('propagates durable write failure with safe errors and no success ACK', async () => {
    await bot(a); vi.spyOn(db, 'exec').mockRejectedValue(new Error('private database details'));
    failure(await post(event(), 'diagnosis-events'), 503, 'MONITORING_NOT_READY');
    failure(await post(check(), 'bot-checks'), 503, 'MONITORING_NOT_READY');
  });
  it('reads historical v1 rows without rewriting them and conflicts with attempted v2 replacement', async () => {
    await bot(a); await post(event(), 'diagnosis-events');
    await db.exec('UPDATE insight_monitoring_diagnose SET schema_version = ?', ['claw-monitoring/diagnosis-event/v1']);
    const exec = vi.spyOn(db, 'exec');
    expect((await repo.listDiagnoses(a, parseQuery({}))).items[0]).toMatchObject({ ...a, diagnosisId: event().eventId });
    expect(exec).not.toHaveBeenCalled();
    failure(await post(event(), 'diagnosis-events'), 409, 'MONITORING_EVENT_CONFLICT');
    expect((await db.query('SELECT schema_version FROM insight_monitoring_diagnose'))[0]?.schema_version).toBe('claw-monitoring/diagnosis-event/v1');
  });
  it('rejects repository target substitution rather than silently reassigning a validated report', async () => {
    await expect(repo.insertDiagnosis({ wire: parseDiagnosis(event(), event().eventId), target: b }, now)).rejects.toMatchObject({ code: 'INVALID_IDENTITY' });
    await expect(repo.applyCheck({ wire: parseCheck(check(), now), target: b }, now)).rejects.toMatchObject({ code: 'INVALID_IDENTITY' });
    expect(await repo.listTargets()).toEqual([]);
  });
});
