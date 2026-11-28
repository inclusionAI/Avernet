import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fixture, type Fixture, NOW } from './fixture.js';
import type { CompensationEvidence } from '../types.js';
let f: Fixture;
beforeEach(() => { f = fixture(); f.run(); f.config(); });
afterEach(async () => { vi.restoreAllMocks(); await f.close(); });
const input = { configId: 'callback-string-id', flowIds: ['flow-1'] };

describe('strict callback compensation eligibility', () => {
  it('without trusted proof/default provider succeeds only as a redacted review scan', async () => {
    const s = f.service({ evidence: undefined, protectionKey: undefined });
    expect(await s.capabilities('wf')).toMatchObject({ canPreview: true, executionEnabled: false });
    const p = await s.preview('wf','admin',input);
    expect(p.items[0]).toMatchObject({ status: 'review' });
    expect(p.items[0].reasons).toContain('TRUSTED_EVIDENCE_MISSING');
    const text = JSON.stringify(p); expect(text).not.toMatch(/SENSITIVE|SIGNING-SECRET|NEVER-COPY-CREDENTIAL|secret_param/);
    expect(text).not.toContain('https://cortex.example.test/exact');
    await expect(s.create('wf','admin',{ previewId:p.previewId,itemIds:[p.items[0].itemId],reason:'reviewed test reason',requestKey:'request1' }))
      .rejects.toMatchObject({ code: 'EXECUTION_DISABLED' });
    expect(f.calls).toHaveLength(0);
  });
  it.each(['completed','running','waiting','blocked','failed','cancelled','Succeeded','succeeded '])('rejects exact non-succeeded status %j', async status => {
    f.raw.prepare('UPDATE flow_runs SET status=?').run(status); await f.authorize();
    expect((await f.service().preview('wf','admin',input)).items[0].status).toBe('ineligible');
  });
  it('excludes trial based on server-owned release relation, not a caller checkbox', async () => {
    f.raw.exec("INSERT INTO workflow_repair_release_records VALUES ('flow-1','trial_run')"); await f.authorize();
    expect((await f.service().preview('wf','admin',input)).items[0]).toMatchObject({ status:'ineligible',reasons:['TRIAL_RUN'] });
  });
  it.each([null,'prod','prepub'])('does not guess environment %s', async env => {
    f.raw.prepare('UPDATE flow_runs SET env=?').run(env); await f.authorize();
    expect((await f.service().preview('wf','admin',input)).items[0].reasons).toContain('RUN_ENVIRONMENT_UNVERIFIED');
  });
  it.each([
    { generationEvidenceRef:'' }, { snapshotHash:'untrusted-fingerprint' }, { retryEpoch:1 }, { receiverId:'' },
    { environment:'prod' }, { workflowId:'other' }, { flowId:'other' },
    { configIds:['unrelated'] }, { urls:['https://unapproved.example.test'] },
    { url:'https://unapproved.example.test' }, { phaseNullable:false }, { validUntil:'2026-11-28T03:59:00Z' },
    { historicalRoute: { evidenceRef:'route-review',validFrom:'2026-11-28T04:00:00Z',validUntil:'2026-12-01T00:00:00Z' } },
  ] satisfies Partial<CompensationEvidence>[])('fails closed for incomplete/mismatched trusted manifest: %j', async change => {
    await f.authorize('flow-1',change);
    expect((await f.service().preview('wf','admin',input)).items[0].status).toBe('review');
  });
  it.each(['evidenceRef','legacySenderContract','generationContract','lateArrivalContract','keyContract'])('requires independent %s contract reference', async field => {
    const e = await f.authorize(); e.legacyIdempotency[field as keyof CompensationEvidence['legacyIdempotency']] = '';
    expect((await f.service().preview('wf','admin',input)).items[0].reasons).toContain('LEGACY_LATE_RETENTION_IDEMPOTENCY_UNVERIFIED');
  });
  it.each([
    { earliestCompletion:'2026-11-29T00:00:00Z' }, { latestCompletion:'2026-11-27T00:00:00Z' },
    { retentionUntil:'2026-11-28T00:00:00Z' }, { lateArrivalUntil:'2026-11-28T04:00:00Z' },
  ])('requires legacy coverage + late arrival + retained business dedup: %j', async change => {
    const e = await f.authorize(); Object.assign(e.legacyIdempotency,change);
    expect((await f.service().preview('wf','admin',input)).items[0].status).toBe('review');
  });
  it('queries ALL receiver history past 100 records and both URL and config aliases', async () => {
    await f.authorize();
    f.log(); for (let i = 0; i < 120; i++) f.log({ status:'failed',config_id:'old-config-version' });
    const query = vi.spyOn(f.db,'query');
    expect((await f.service().preview('wf','admin',input)).items[0].status).toBe('delivered');
    const logQueries = query.mock.calls.filter(([sql])=>sql.includes('FROM http_callback_logs'));
    expect(logQueries[0][0]).not.toMatch(/LIMIT/);
    f.raw.exec('DELETE FROM http_callback_logs');
    f.log({ config_id:'deleted-old-config-id',callback_url:'https://cortex-old.example.test/legacy' });
    expect((await f.service().preview('wf','admin',input)).items[0].status).toBe('delivered');
  });
  it.each(['failed','sent','skipped','suppressed','unknown'])('any non-delivered receiver attempt %s needs review', async status => {
    await f.authorize(); f.log({ status });
    expect((await f.service().preview('wf','admin',input)).items[0]).toMatchObject({ status:'review',reasons:['EXISTING_RECEIVER_ATTEMPT'] });
  });
  it('does not attribute a different receiver or a different event to this receiver', async () => {
    await f.authorize(); f.log({ config_id:'other',callback_url:'https://other.example.test',status:'failed' });
    f.log({ notify_event:'workflow_failed' });
    expect((await f.service().preview('wf','admin',input)).summary.eligible).toBe(1);
  });
  it.each(['result_json','params_json','input_json'])('corrupt %s requires review', async field => {
    f.raw.exec(`UPDATE flow_runs SET ${field} = 'INVALID JSON'`); await f.authorize();
    expect((await f.service().preview('wf','admin',input)).items[0].reasons).toContain('INVALID_RUN_JSON');
  });
  it('database read failure is not evidence of missing delivery', async () => {
    await f.authorize(); f.raw.exec('DROP TABLE http_callback_logs');
    await expect(f.service().preview('wf','admin',input)).rejects.toThrow(/no such table/);
    expect(f.calls).toHaveLength(0);
  });
  it('enforces bounded ISO completion-time scan; >100 explicitly rejected', async () => {
    const s = f.service();
    await expect(s.preview('wf','admin',{ configId:'callback-string-id',from:'2026-10-01T00:00:00Z',to:'2026-11-28T00:00:00Z' }))
      .rejects.toMatchObject({ code:'INVALID_COMPLETION_RANGE' });
    await expect(s.preview('wf','admin',{ ...input,flowIds:Array.from({length:101},(_,i)=>`run-${i}`) }))
      .rejects.toMatchObject({ code:'INVALID_FLOW_SELECTION' });
    for (let i=0;i<100;i++) f.run(`another-${i}`);
    await expect(s.preview('wf','admin',{ configId:'callback-string-id',from:new Date(NOW-86_400_000).toISOString(),to:new Date(NOW).toISOString() }))
      .rejects.toMatchObject({ code:'RANGE_TOO_LARGE' });
  });
});
