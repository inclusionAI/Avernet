// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { FanoutStateRepository } from '../../repositories/fanout-state-repository.js';
import { RunLogRepository } from '../../repositories/run-log-repository.js';
import { fixture, group } from '../../repositories/__tests__/fanout-state-fixture.js';
import { saveFanoutGroupWithDiagnostics } from '../fanout-group-save-service.js';

let f: ReturnType<typeof fixture>;
let repo: FanoutStateRepository;
beforeEach(() => {
  f = fixture(); repo = new FanoutStateRepository(f.db);
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});
afterEach(async () => { vi.restoreAllMocks(); await f.db.close(); });
const runLogs = () => new RunLogRepository(f.db);
const save = (g = group(), expectedRevision: number | null = null) =>
  saveFanoutGroupWithDiagnostics(repo, runLogs(), 'parent', 'fanout', { group: g, expectedRevision });
const logs = () => f.raw.prepare('SELECT * FROM run_logs ORDER BY id').all() as Array<{
  flow_id: string; node_id: string; source: string; level: string; message: string;
}>;
const diagnostic = () => JSON.parse(logs().at(-1)!.message);
function changed() {
  const g = group(); g.children[0].status = 'running'; return g;
}
function reverseKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(reverseKeys);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).reverse().map(([k, v]) => [k, reverseKeys(v)]));
  return value;
}

it('persists a structured diagnostic for a real revision 409 without changing durable state', async () => {
  await save();
  await expect(save(changed())).rejects.toMatchObject({ statusCode: 409, message: 'Group revision conflict' });
  expect(logs()).toHaveLength(1);
  expect(logs()[0]).toMatchObject({ flow_id: 'parent', node_id: 'fanout', source: 'fanout-server', level: 'warn' });
  expect(diagnostic()).toMatchObject({
    event: 'fanout.save.conflict', eventId: expect.any(String), stage: 'save_group', code: 'FANOUT_REVISION_CONFLICT',
    httpStatus: 409, parentFlowId: 'parent', nodeId: 'fanout', expectedRevision: null, submittedRevision: 0,
    observedRevision: 0, parentStatus: 'running', errorMessage: 'Group revision conflict',
    snapshotReadStatus: 'ok', observationTiming: 'after_conflict', recovery: 'conflict_retained',
    possibleCause: expect.any(String), recoveryAction: expect.any(String),
    submitted: { children: [{ childFlowId: 'child-fanout', status: 'running', hasDispatchClaim: false }] },
    observed: { children: [{ childFlowId: 'child-fanout', status: 'dispatch_pending' }] },
  });
  expect((await repo.getGroup('parent', 'fanout')).group).toEqual(group());
  expect(console.error).not.toHaveBeenCalled();
});

it('reloads and returns current revision for canonical equivalent state without another write', async () => {
  await save();
  await save({ ...group(), revision: 1 }, 0);
  await save({ ...group(), revision: 2 }, 1);
  const exec = vi.spyOn(f.db, 'exec');
  const replay = reverseKeys({ ...group(), revision: 1 }) as ReturnType<typeof group>;
  expect(await save(replay, 0)).toEqual({ group: { ...group(), revision: 2 }, parentStatus: 'running' });
  expect(exec.mock.calls.filter(([sql]) => /UPDATE flow_runs/.test(sql))).toHaveLength(0);
  expect(diagnostic()).toMatchObject({ expectedRevision: 0, submittedRevision: 1, observedRevision: 2, recovery: 'reloaded_equivalent_state' });
});

it('keeps non-equivalent concurrent progress authoritative instead of refreshing revision and overwriting it', async () => {
  await save(); await save({ ...changed(), revision: 1 }, 0);
  await expect(save({ ...group(), revision: 1 }, 0)).rejects.toMatchObject({ statusCode: 409 });
  expect((await repo.getGroup('parent', 'fanout')).group).toEqual({ ...changed(), revision: 1 });
  expect(diagnostic().recovery).toBe('conflict_retained');
});

it.each(['dispatch_pending', 'running'])('does not acknowledge a repeated one-shot claim even with equivalent %s state', async status => {
  const initial = group(); initial.children[0].status = 'queued'; await save(initial);
  const claimed = structuredClone(initial); claimed.revision = 1;
  claimed.children[0].status = 'dispatch_pending'; claimed.children[0].dispatchClaimToken = 'private-claim-token';
  claimed.cleanupPendingChildIds = ['child-fanout']; await save(claimed, 0);
  if (status === 'running') { claimed.revision = 2; claimed.children[0].status = status; await save(claimed, 1); }
  await expect(save(claimed, claimed.revision - 1)).rejects.toMatchObject({ statusCode: 409 });
  expect(diagnostic().recovery).toBe('conflict_retained');
  expect(logs()[0].message).not.toContain('private-claim-token');
});

it('writes the claim rejection diagnostic outside the rolled-back transaction', async () => {
  const initial = group(); initial.children[0].status = 'queued'; await save(initial);
  f.raw.exec("UPDATE flow_runs SET status='waiting'");
  const proposed = structuredClone(initial); proposed.revision = 1;
  proposed.children[0].status = 'dispatch_pending'; proposed.children[0].dispatchClaimToken = 'private-token';
  proposed.cleanupPendingChildIds = ['child-fanout'];
  const original = f.db.exec.bind(f.db);
  vi.spyOn(f.db, 'exec').mockImplementation(async (sql, params) => {
    if (sql.startsWith('INSERT INTO run_logs')) expect(f.raw.inTransaction).toBe(false);
    return original(sql, params);
  });
  await expect(save(proposed, 0)).rejects.toMatchObject({ statusCode: 409, message: 'Dispatch claim must be isolated and parent must be running' });
  expect(diagnostic()).toMatchObject({ code: 'FANOUT_DISPATCH_CLAIM_CONFLICT', parentStatus: 'waiting', recovery: 'conflict_retained' });
  expect((await repo.getGroup('parent', 'fanout')).group).toEqual(initial);
});

it.each(['waiting', 'cancelled'])('does not recover stale equivalent state when parent is %s', async status => {
  await save(); f.raw.prepare('UPDATE flow_runs SET status=?').run(status);
  await expect(save()).rejects.toMatchObject({ statusCode: 409 });
  expect(diagnostic().recovery).toBe('conflict_retained');
});

it('does not recover an equivalent group whose deadline has expired', async () => {
  const expired = group(); expired.deadlineAt = new Date(Date.now() - 1000).toISOString();
  await save(expired);
  await expect(save(expired)).rejects.toMatchObject({ statusCode: 409 });
  expect(diagnostic().recovery).toBe('conflict_retained');
});

it('logs immutable identity conflicts, not just revision conflicts', async () => {
  await save(); const different = group(); different.revision = 1; different.inputDigest = 'different';
  await expect(save(different, 0)).rejects.toMatchObject({ statusCode: 409, message: 'Group launch identity conflict' });
  expect(diagnostic().code).toBe('FANOUT_IDENTITY_CONFLICT');
});

it('logs exhausted CAS failures without treating an absent group as saved', async () => {
  f.raw.exec('CREATE TRIGGER race BEFORE UPDATE ON flow_runs BEGIN SELECT RAISE(IGNORE); END');
  await expect(save()).rejects.toMatchObject({ statusCode: 409 });
  expect(diagnostic()).toMatchObject({ code: 'FANOUT_CAS_OR_DEADLINE_CONFLICT', observedRevision: null, recovery: 'conflict_retained' });
  expect((await repo.getGroup('parent', 'fanout')).group).toBeNull();
});

it('preserves the original 409 and records reload failure without disclosing the database error', async () => {
  await save(); vi.spyOn(repo, 'getGroup').mockRejectedValueOnce(new Error('private-read-detail'));
  await expect(save(changed())).rejects.toMatchObject({ statusCode: 409, message: 'Group revision conflict' });
  expect(diagnostic()).toMatchObject({ snapshotReadStatus: 'failed', recovery: 'reload_failed', observedRevision: null });
  expect(logs()[0].message).not.toContain('private-read-detail');
});

it.each(['zero_rows', 'database_error', 'committed_then_error'])('attempts the log insert once and safely falls back on %s', async failure => {
  await save(); const original = f.db.exec.bind(f.db); let insertAttempts = 0;
  vi.spyOn(f.db, 'exec').mockImplementation(async (sql, params) => {
    if (!sql.startsWith('INSERT INTO run_logs')) return original(sql, params);
    insertAttempts++;
    if (failure === 'zero_rows') return { affectedRows: 0 };
    if (failure === 'committed_then_error') await original(sql, params);
    throw new Error('SQL private-db-detail');
  });
  await expect(save(changed())).rejects.toMatchObject({ statusCode: 409, message: 'Group revision conflict' });
  expect(insertAttempts).toBe(1);
  expect(logs()).toHaveLength(failure === 'committed_then_error' ? 1 : 0);

  expect(console.error).toHaveBeenCalledTimes(1);
  const fallback = JSON.parse(vi.mocked(console.error).mock.calls[0][1] as string);
  expect(fallback).toMatchObject({ logPersistence: 'failed', code: 'FANOUT_REVISION_CONFLICT', recovery: 'conflict_retained' });
  expect(JSON.stringify(fallback)).not.toContain('private-db-detail');
  if (failure === 'committed_then_error') expect(fallback.eventId).toBe(diagnostic().eventId);
});

it('treats a positive insert count as success without another insert or fallback', async () => {
  await save(); const insert = vi.spyOn(RunLogRepository.prototype, 'insertBatch').mockResolvedValue(2);
  await expect(save(changed())).rejects.toMatchObject({ statusCode: 409 });
  expect(insert).toHaveBeenCalledTimes(1); expect(console.error).not.toHaveBeenCalled();
});

it('does not lose equivalent-state recovery if the log insert fails', async () => {
  await save(); vi.spyOn(RunLogRepository.prototype, 'insertBatch').mockResolvedValue(0);
  expect(await save()).toEqual({ group: group(), parentStatus: 'running' });
  expect(console.error).toHaveBeenCalledTimes(1);
});

it('allowlists diagnostic fields instead of logging raw groups, params, results, error bodies, snapshots or tokens', async () => {
  const g = group();
  g.privateExtra = 'raw-group-secret'; g.children[0].params = { password: 'params-secret' };
  g.children[0].item = { sensitive: 'item-secret' }; g.children[0].sessionKey = 'session-secret';
  g.children[0].result = { password: 'result-secret' };
  g.children[0].error = { code: 'error-secret', category: 'category-secret', message: 'error-message-secret' };
  g.childWorkflow.nodes[0].executor.prompt = 'workflow-detail';
  g.children[0].originCredentials = 'runtime-redacted-value'; await save(g);
  const next = structuredClone(g); next.children[0].status = 'running'; next.children[0].dispatchClaimToken = 'token-detail';
  await expect(save(next)).rejects.toMatchObject({ statusCode: 409 });
  expect(logs()[0].message).not.toMatch(/dispatchClaimToken|childWorkflow|originCredentials|sessionKey|runtime-redacted-value|private-db-detail|private-read-detail/);
  expect(diagnostic().submitted.children[0].hasDispatchClaim).toBe(true);
});

it('accepts an injected log sink without requiring SQL or repository-prototype patches', async () => {
  await save();
  const entries: Array<Record<string, unknown>> = [];
  const fakeSink = { insertBatch: async (batch: Array<Record<string, unknown>>) => { entries.push(...batch); return batch.length; } };
  await expect(saveFanoutGroupWithDiagnostics(repo, fakeSink, 'parent', 'fanout',
    { group: changed(), expectedRevision: null })).rejects.toMatchObject({ statusCode: 409 });
  expect(entries).toHaveLength(1);
  expect(entries[0]).toMatchObject({ flow_id: 'parent', node_id: 'fanout', level: 'warn', source: 'fanout-server' });
  const diagnostic = JSON.parse(entries[0].message as string);
  expect(diagnostic).toMatchObject({ event: 'fanout.save.conflict', httpStatus: 409, code: 'FANOUT_REVISION_CONFLICT' });
  expect(console.error).not.toHaveBeenCalled();
});

it('keeps oversized escaped/Unicode children summaries as valid JSON below the database TEXT limit', async () => {
  const g = group(); const large = '\u0001'.repeat(256);
  g.children = Array.from({ length: 40 }, (_, i) => ({ ...g.children[0], itemIndex: i,
    childFlowId: `${large}${i}`, nativeFlowId: `${large}native${i}`, itemId: `${large}项目${i}` }));
  await save(g); const next = structuredClone(g); next.children[0].status = 'running';
  await expect(save(next)).rejects.toMatchObject({ statusCode: 409 });
  expect(Buffer.byteLength(logs()[0].message, 'utf8')).toBeLessThanOrEqual(60_000);
  for (const summary of [diagnostic().submitted, diagnostic().observed]) {
    expect(summary.childCount).toBe(40); expect(summary.children.length + summary.childrenOmitted).toBe(40);
    expect(summary.childrenOmitted).toBeGreaterThan(20);
  }
});

it('does not turn non-409 validation failures into conflict logs', async () => {
  await expect(save({ ...group(), revision: 3 })).rejects.toMatchObject({ statusCode: 400 });
  expect(logs()).toHaveLength(0);
});
