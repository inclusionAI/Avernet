import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { database } from './test-database.js';
import { initializeMonitoringSqlite } from '../schema.js';
import { MonitoringRepository } from '../../../repositories/monitoring-repository.js';
import { SqlMonitoringBotDirectory } from '../../../repositories/monitoring-bot-directory.js';
import { createMonitoringBrowserService } from '../monitoring-browser-service.js';
import { createMonitoringReferences } from '../target-ref.js';
import { createTargetResolver } from '../target-resolver.js';
import { createMonitoringService } from '../monitoring-service.js';
import { parseCheck, parseDiagnosis, parseQuery } from '../validation.js';
import { parseWindow } from '../browser-validation.js';
import type { MonitoringTarget } from '../contracts.js';
import type { MonitoringPrincipal } from '../directory-contracts.js';

const now = Date.parse('2026-09-10T09:00:00Z');
const all = { start: 'all', end: 'all' };
const window = { startMs: null, endMs: null };
const scope = { tenant: 'one', allowedTargetEnvs: ['test', 'prod'] };
const member: MonitoringPrincipal = { ...scope, staffId: '001', isAuthenticated: true, isClawInsightAdmin: false };
const admin = { ...member, isClawInsightAdmin: true };
const a = { botId: 'default', entityId: '001', env: 'test' };
const b = { ...a, entityId: '002' }, prod = { ...a, env: 'prod' };
const fixture = JSON.parse(readFileSync('server/fixtures/monitoring/alert.json', 'utf8'));
let db: IDatabase, repo: MonitoringRepository, directory: SqlMonitoringBotDirectory;
const refs = createMonitoringReferences(() => now);
const browser = () => createMonitoringBrowserService(repo, directory, refs, scope, () => now);
async function bot(t: MonitoringTarget, owner = t.entityId, name = '默认 Bot', tenant = 'one', deleted = 0) {
  await db.exec('INSERT INTO ac_bots (bot_id,entity_id,env,owner_id,bot_name,owner_name,avernet_tenant,is_delete) VALUES (?,?,?,?,?,?,?,?)',
    [t.botId, t.entityId, t.env, owner, name, '姓名', tenant, deleted]);
}
async function check(t = a, status = 'HEALTHY', offset = 0, engine = 'TE') {
  const wire = parseCheck({ schemaVersion: 'claw-monitoring/bot-check/v1', botId: t.botId, engine,
    checkedAt: new Date(now + offset).toISOString(), lastSuccessfulCheckAt: null, status }, now + Math.max(offset, 0));
  return repo.applyCheck({ target: t, wire }, now + Math.max(offset, 0));
}
async function diagnosis(t = a, eventId = 'event-1', patch = {}) {
  const wire = parseDiagnosis({ ...fixture, botId: t.botId, eventId, diagnosisId: eventId, ...patch }, eventId);
  return repo.insertDiagnosis({ target: t, wire }, now);
}
beforeEach(async () => {
  db = database(':memory:'); await initializeMonitoringSqlite(db);
  await db.exec(`CREATE TABLE ac_bots (id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id TEXT, entity_id TEXT, env TEXT,
    owner_id TEXT, bot_name TEXT, owner_name TEXT, avernet_tenant TEXT, is_delete INTEGER)`);
  repo = new MonitoringRepository(db); directory = new SqlMonitoringBotDirectory(db, true);
});
afterEach(async () => { await db.close(); vi.restoreAllMocks(); });

describe('compound monitoring identity with real SQL', () => {
  it('isolates same default IDs by entity and environment for CAS, lists and summaries', async () => {
    for (const [i, t] of [a, b, prod].entries()) { await check(t); await diagnosis(t, `e${i}`); }
    await check(b, 'PAUSED', 1000);
    expect(await check(a, 'ERROR', -1000)).toBe(false);
    expect((await repo.summaries([a,b,prod], window)).map(s => s?.status)).toEqual(['HEALTHY','PAUSED','HEALTHY']);
    for (const t of [a,b,prod]) expect((await repo.listDiagnoses(t, parseQuery({}))).total).toBe(1);
    expect(await repo.listTargets()).toHaveLength(3);
    await expect(check(a, 'ERROR', -1000, 'OC')).rejects.toMatchObject({ code: 'EVENT_CONFLICT' });
    await expect(diagnosis(b, 'e0')).rejects.toMatchObject({ code: 'EVENT_CONFLICT' });
  });
  it('counts typed engine/session identity, missing IDs and occurrence window, not trace IDs', async () => {
    await check(a); await check(b); await check(prod);
    await diagnosis(a,'e1', { sessionId: 'same' });
    await diagnosis(a,'e2', { sessionId: 'same' });
    await diagnosis(a,'e3', { sessionId: null, sessionKey: 'same' });
    await diagnosis(a,'e4', { engine: 'OC', sessionId: 'same' });
    await diagnosis(a,'e5', { sessionId: null, sessionKey: null });
    await diagnosis(b,'e6', { sessionId: null, sessionKey: null });
    const [summary, missing, zero] = await repo.summaries([a,b,prod], window);
    expect(summary).toMatchObject({ diagnosisCount: 5, alertCount: 5, diagnosedSessionCount: 3, unidentifiedSessionDiagnosisCount: 1 });
    expect(missing?.diagnosedSessionCount).toBeNull(); expect(zero?.diagnosedSessionCount).toBe(0);
    expect((await repo.summaries([a], { startMs: now, endMs: now + 1000 }))[0]?.diagnosisCount).toBe(0);
    await db.exec('DELETE FROM insight_monitoring_bot_check WHERE entity_id = ?', ['002']);
    expect(await repo.summaries([b], window)).toEqual([null]);
  });
  it('preserves v1 alias ACK and event idempotency but rejects ambiguous bare IDs without writes', async () => {
    await bot(a); await bot(b);
    const resolver = createTargetResolver(directory, scope, [{ reportedBotId: 'instance-001', target: a }]);
    const service = createMonitoringService(repo, resolver, () => now);
    const wire = { ...fixture, botId: 'instance-001' };
    expect(await service.reportDiagnosis(wire, wire.eventId)).toMatchObject({ eventId: wire.eventId, duplicate: false });
    expect(await service.reportDiagnosis(wire, wire.eventId)).toMatchObject({ eventId: wire.eventId, duplicate: true });
    expect(await service.bots()).toEqual({ items: [{ botId: 'instance-001' }] });
    await expect(service.reportDiagnosis({ ...wire, botId: 'default' }, wire.eventId)).rejects.toMatchObject({ code: 'TARGET_AMBIGUOUS' });
    await expect(service.reportDiagnosis({ ...wire, botId: 'missing' }, wire.eventId)).rejects.toMatchObject({ code: 'TARGET_UNRESOLVED' });
    expect((await repo.readStatus(a)).count).toBe(1); expect((await repo.readStatus(b)).count).toBe(0);
    await db.exec('DELETE FROM ac_bots WHERE owner_id = ?', ['002']);
    await expect(resolver.resolve('default')).rejects.toMatchObject({ code: 'TARGET_BINDING_CONFLICT' });
    expect(() => createTargetResolver(directory, scope, [
      { reportedBotId: 'alias-a', target: a }, { reportedBotId: 'alias-b', target: a },
    ])).toThrow();
  });
});

describe('directory, authorization and browser contracts', () => {
  it('queries owner, not entity/collaborator, preserves leading zeros and scopes tenant/env/deletion', async () => {
    await bot(a); await bot(b); await bot(prod, '001');
    await bot({ ...a, botId: 'owned-by-other' }, '002');
    await bot({ ...a, botId: 'wrong-tenant' }, '001', '默认', 'two');
    await bot({ ...a, botId: 'deleted' }, '001', '默认', 'one', 1);
    await bot({ ...a, botId: 'staging', env: 'staging' });
    const page = await browser().options(member, all);
    expect(page.items).toHaveLength(2); expect(new Set(page.items.map(x => x.botRef)).size).toBe(2);
    expect((await browser().options({ ...member, staffId: '1' }, all)).items).toEqual([]);
    expect((await browser().options(admin, { ...all, scope: 'all' })).items).toHaveLength(4);
    await expect(browser().options(member, { ...all, scope: 'all' })).rejects.toMatchObject({ code: 'FORBIDDEN' });
    await expect(browser().options({ ...member, tenant: 'two' }, all)).rejects.toMatchObject({ code: 'FORBIDDEN' });
    await expect(browser().options({ ...member, isAuthenticated: false }, all)).rejects.toMatchObject({ code: 'UNAUTHENTICATED' });
  });
  it('supports name/ID search and only administrator staff search with literal LIKE characters', async () => {
    await bot(a, '001', '中文%_!Alpha'); await bot(b, '002', '中文其他');
    const search = (q: string, principal = member) => browser().options(principal, { ...all, q });
    expect((await search('%_!aLpHa')).items).toHaveLength(1);
    expect((await search('DEFAULT')).items).toHaveLength(1);
    expect((await search('001')).items).toHaveLength(0);
    expect((await search('001', admin)).items).toHaveLength(1);
    expect((await browser().options(admin, { ...all, scope: 'all', q: '002' })).items[0]?.ownerId).toBe('002');
    expect((await search("' OR 1=1 --")).items).toEqual([]);
  });
  it('rechecks ownership and environment for every target read; references are not permissions', async () => {
    await bot(a); await check(a); await diagnosis(a);
    const ref = refs.encode(a, scope.tenant);
    expect((await browser().diagnoses(member, ref, all)).total).toBe(1);
    await db.exec('UPDATE ac_bots SET owner_id = ?', ['002']);
    await expect(browser().status(member, ref, all)).rejects.toMatchObject({ code: 'BOT_NOT_FOUND' });
    await expect(browser().diagnoses(member, ref, all)).rejects.toMatchObject({ code: 'BOT_NOT_FOUND' });
    await expect(browser().enroll(member, { botRef: ref })).rejects.toMatchObject({ code: 'BOT_NOT_FOUND' });
    expect((await browser().status(admin, ref, all)).enrollmentState).toBe('ENROLLED');
    await expect(browser().status({ ...admin, allowedTargetEnvs: ['prod'] }, ref, all)).rejects.toMatchObject({ code: 'BOT_NOT_FOUND' });
    await expect(browser().status(admin, ref + 'x', all)).rejects.toMatchObject({ code: 'BOT_NOT_FOUND' });
    expect(() => refs.decode(ref, 'two')).toThrow();
  });
  it('rejects a valid hand-crafted locator for another owner; unsigned references are never credentials', async () => {
    await bot(a); await bot(b); await check(b);
    const forged = Buffer.from(JSON.stringify({ v: 1, kind: 'target', tenant: scope.tenant, ...b })).toString('base64url');
    // The locator is intentionally decodable. Only a fresh directory ACL may authorize it.
    expect(refs.decode(forged, scope.tenant)).toEqual(b);
    await expect(browser().status(member, forged, all)).rejects.toMatchObject({ code: 'BOT_NOT_FOUND' });
    await expect(browser().diagnoses(member, forged, all)).rejects.toMatchObject({ code: 'BOT_NOT_FOUND' });
    await expect(browser().enroll(member, { botRef: forged })).rejects.toMatchObject({ code: 'BOT_NOT_FOUND' });
    expect((await browser().status(admin, forged, all)).ownerId).toBe('002');
  });
  it('does not expose diagnosis-only history as enrollment, and enrollment placeholder performs no writes', async () => {
    await bot(a); await diagnosis(a);
    const ref = refs.encode(a, scope.tenant);
    expect((await browser().options(member, all)).items[0]).toMatchObject({ enrollmentState: 'NOT_ENROLLED', monitoring: null });
    await expect(browser().diagnoses(member, ref, all)).rejects.toMatchObject({ code: 'NOT_ENROLLED' });
    const exec = vi.spyOn(db, 'exec');
    await expect(browser().enroll(member, { botRef: ref })).rejects.toMatchObject({ code: 'ENROLLMENT_NOT_IMPLEMENTED' });
    expect(exec).not.toHaveBeenCalled();
    await check(a);
    await expect(browser().enroll(member, { botRef: ref })).rejects.toMatchObject({ code: 'ALREADY_ENROLLED' });
  });
  it('uses checked targets as the monitored source and keeps cursor boundaries stable', async () => {
    for (let i = 0; i < 205; i++) await bot({ ...a, botId: `bot-${i}` });
    await check({ ...a, botId: 'bot-0' }); await check({ ...a, botId: 'bot-1' });
    const q = { ...all, scope: 'monitored', limit: '1' };
    const first = await browser().options(admin, q);
    expect(first.items[0]?.botId).toBe('bot-1'); expect(first.nextCursor).toBeTruthy();
    const second = await browser().options(admin, { ...q, cursor: first.nextCursor! });
    expect(second.items[0]?.botId).toBe('bot-0'); expect(second.nextCursor).toBeNull();
    expect((await browser().options(admin, { ...q, q: 'bot-0' })).items.map(item => item.botId)).toEqual(['bot-0']);
    for (const change of [{ q: 'x' }, { start: String(now), end: String(now+1000) }, { scope: 'all' }, { limit: '2' }]) {
      await expect(browser().options(admin, { ...q, ...change, cursor: first.nextCursor! })).rejects.toMatchObject({ code: 'INVALID_EVENT' });
    }
    await expect(browser().options({ ...admin, staffId: '002' }, { ...q, cursor: first.nextCursor! })).rejects.toMatchObject({ code: 'INVALID_EVENT' });
    let clock = now; const timed = createMonitoringReferences(() => clock);
    const cursor = timed.cursor('42', 'context'); clock += 15*60_000;
    expect(() => timed.readCursor(cursor, 'context')).toThrow();
    expect(() => refs.readCursor(first.nextCursor! + 'x', 'context')).toThrow();
  });
  it('keeps BIGINT directory seek positions exact beyond JavaScript safe integers', async () => {
    await bot(a); await bot(b);
    await db.exec("UPDATE ac_bots SET id = '9007199254740993' WHERE entity_id = '001'");
    await db.exec("UPDATE ac_bots SET id = '9007199254740994' WHERE entity_id = '002'");
    const first = await browser().options(admin, { ...all, scope: 'all', limit: '1' });
    expect(first.items[0]?.ownerId).toBe('002');
    const second = await browser().options(admin, { ...all, scope: 'all', limit: '1', cursor: first.nextCursor! });
    expect(second.items[0]?.ownerId).toBe('001'); expect(second.nextCursor).toBeNull();
  });
  it('fails closed on incomplete/unavailable directory and strictly validates windows', async () => {
    await bot(a); await db.exec('UPDATE ac_bots SET entity_id = NULL');
    await expect(browser().options(member, all)).rejects.toMatchObject({ code: 'NOT_READY' });
    await db.exec('DROP TABLE ac_bots');
    await expect(createTargetResolver(directory, scope).resolve('default')).rejects.toMatchObject({ code: 'NOT_READY' });
    expect(parseWindow(all, now)).toEqual(window);
    expect(parseWindow({}, now)).toEqual({ startMs: Date.parse('2026-09-10T00:00:00+08:00'), endMs: Date.parse('2026-09-11T00:00:00+08:00') });
    for (const query of [{ start: '0' }, { start: '5', end: '4' }, { start: '0', end: String(367*86400_000) }, { start: '-1', end: 'all' }]) {
      expect(() => parseWindow(query, now)).toThrow();
    }
    await expect(browser().options(member, { ownerId: '002' })).rejects.toMatchObject({ code: 'INVALID_EVENT' });
  });
});
