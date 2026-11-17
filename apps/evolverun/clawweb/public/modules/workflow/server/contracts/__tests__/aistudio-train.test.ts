import Database from 'better-sqlite3';
import { describe, expect, it } from 'vitest';
import { aistudioTrainDdl } from '@avernet/clawweb-shared/server/schema-aistudio-train';
import { WATCH_FIELDS, assertWatchRecord, assertWatchCas, toWatchDto } from '../aistudio-train.js';

import { watchFixture } from './aistudio-train.fixture.js';
describe('AIStudio wire and safe user projection', () => {
  it('creates every snake-case business column from the wire contract plus mandatory gmt metadata', () => {
    const raw = new Database(':memory:');
    try {
      for (const ddl of aistudioTrainDdl('sqlite')) raw.exec(ddl);
      const columns = raw.prepare('PRAGMA table_info(aistudio_train_watches)').all() as {name:string}[];
      expect(columns.map(c => c.name)).toEqual(expect.arrayContaining([...WATCH_FIELDS.map(k => k.replace(/[A-Z]/g,c => `_${c.toLowerCase()}`)),'id','gmt_create','gmt_modified']));
    } finally { raw.close(); }
  });
  it('accepts the exact worker wire contract and fences version increments', () => {
    expect(() => assertWatchRecord(watchFixture())).not.toThrow();
    expect(() => assertWatchCas(1, watchFixture({ version: 2 }))).not.toThrow();
    expect(() => assertWatchCas(1, watchFixture({ version: 3 }))).toThrow();
  });
  it.each([{ attempt: 0 }, { nextPollAt: '700' }, { canceled: 1 }, { ownerBotId: 'bot' },
    { phase: 'done' }, { lastPollSource: 'user' }, { version: 0 }, { secret: 'extra' },
    { detailUrl: 'http://127.0.0.1/secret' }, { detailUrl: 'https://host.internal/' }])('rejects malformed or unsafe fields %j', extra => {
    expect(() => assertWatchRecord({ ...watchFixture(), ...extra })).toThrow();
  });
  it('accepts approved record identifiers with dots, colons and hyphens', () => {
    expect(() => assertWatchRecord(watchFixture({ recordId:'run.2026:train-1_a' }))).not.toThrow();
  });
  it.each(['https://example.org/?view=1','https://example.org/#token=secret','https://example.org/a b'])('rejects query/fragment/whitespace detail URLs %s', detailUrl => {
    expect(() => assertWatchRecord(watchFixture({detailUrl}))).toThrow();
  });
  it('projects only allowlisted public fields with ISO seconds and no raw error', () => {
    const dto = toWatchDto(watchFixture({ leaseOwner: 'secret-worker', lastError: 'token=secret' }), 200);
    expect(dto.submittedAt).toBe('1970-01-01T00:01:40.000Z');
    expect(dto.pollInterval).toBe(60); expect(dto.manualPollAllowed).toBe(true);
    expect(JSON.stringify(dto)).not.toMatch(/secret|leaseOwner|ownerBotId|workflowId/);
    expect(toWatchDto(watchFixture({ phase: 'resolved' }), 200).nextPollAt).toBeNull();
  });
  it('projects the required persisted snapshot version on observations and mailbox acceptance', () => {
    const before = watchFixture({ version: 7 });
    const accepted = { ...before, version: 8, manualRequestedAt: 200, lastManualRequestedAt: 200 };
    expect(toWatchDto(before, 200)).toMatchObject({ version: 7, manualPollPending: false, manualPollAllowed: true });
    expect(toWatchDto(accepted, 200)).toMatchObject({ version: 8, manualPollPending: true, manualPollAllowed: false });
  });
  it('refreshes cooldown projection without changing the persisted version or query plan', () => {
    const record = watchFixture({ version: 4, lastManualRequestedAt: 200 });
    const before = toWatchDto(record, 230);
    expect(before).toMatchObject({ version: 4, manualPollAllowed: false, manualPollRetryAt: new Date(260000).toISOString() });
    expect(toWatchDto(record, 260)).toEqual({ ...before, manualPollAllowed: true, manualPollRetryAt: null });
  });
  it('projects timeout eligibility on the same persisted version before engine finalization', () => {
    const record = watchFixture({ version: 4, timeoutAt: 260 });
    const before = toWatchDto(record, 259);
    expect(before).toMatchObject({ version: 4, phase: 'waiting', manualPollAllowed: true });
    expect(toWatchDto(record, 260)).toEqual({ ...before, manualPollAllowed: false });
  });
  it('does not expose arbitrary upstream status text as a credential channel', () => {
    expect(toWatchDto(watchFixture({lastStatus:'token_secret_value'}),200).lastStatus).toBe('unknown');
    expect(toWatchDto(watchFixture({lastStatus:'RUNNING'}),200).lastStatus).toBe('RUNNING');
  });
  it('marks mailbox pending and cooldown without changing the plan', () => {
    const dto = toWatchDto(watchFixture({ manualRequestedAt: 199, lastManualRequestedAt: 199 }), 200);
    expect(dto.manualPollPending).toBe(true); expect(dto.manualPollAllowed).toBe(false);
    expect(dto.manualPollRetryAt).toBe(new Date(259000).toISOString()); expect(dto.nextPollAt).toBe(new Date(700000).toISOString());
  });
});
