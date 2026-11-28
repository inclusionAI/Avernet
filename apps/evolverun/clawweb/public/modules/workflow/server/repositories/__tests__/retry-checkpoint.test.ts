import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { FlowRunRepository } from '../flow-run-repository.js';
import { retryCheckpointFixture, priorRetryState, nextRetryState } from './retry-checkpoint.fixture.js';

let f: ReturnType<typeof retryCheckpointFixture>;
beforeEach(() => { f = retryCheckpointFixture(); });
afterEach(async () => { await f.close(); vi.restoreAllMocks(); });
const resume = (stateJson?: string) => f.repo.resumeForRetry('flow', 3, stateJson);
for (const kind of ['canonical', 'embedded-string', 'embedded-object', 'both', 'none'] as const) {
  it(`atomically admits next checkpoint from ${kind} storage without changing other fields`, async () => {
    f.seed(kind); const before = f.row(); const serialized = JSON.stringify(nextRetryState, null, 1);
    expect(await resume(serialized)).toBe('resumed');
    const row = f.row(); expect(row.state_json).toBe(serialized);
    const params = JSON.parse(row.params_json as string); expect(params.input).toEqual({ keep: 7 });
    if (kind === 'canonical' || kind === 'none') expect(params).not.toHaveProperty('_clawmind_state');
    else {
      const embedded = params._clawmind_state;
      expect(typeof embedded).toBe(kind === 'embedded-object' ? 'object' : 'string');
      expect(typeof embedded === 'string' ? JSON.parse(embedded) : embedded).toEqual(nextRetryState);
    }
    expect(row).toMatchObject({ status: 'running', retry_epoch: 3, completed_at: null, started_at: before.started_at,
      current_phase: before.current_phase, result_json: before.result_json, input_json: before.input_json,
      succeeded_count: before.succeeded_count, failed_count: before.failed_count });
    expect(f.raw.prepare('SELECT * FROM retry_audit').all()).toEqual([{ epoch: 3, status: 'running', state_json: row.state_json, params_json: row.params_json }]);
    expect(f.raw.prepare('SELECT status FROM node_executions').get()).toEqual({ status: 'failed' });
  });
}
it.each(['{', 'null', '[]', 'true', '"state"', '{}', '{"retryEpoch":2}', '{"retryEpoch":4}', '{"retryEpoch":"3"}', '{"retryEpoch":null}'])(
  'invalid supplied checkpoint %s is rejected before SQL', async invalid => {
    f.seed(); const before = f.row(); const exec = vi.spyOn(f.db, 'exec'); const query = vi.spyOn(f.db, 'query');
    await expect(resume(invalid)).rejects.toThrow(/state_json|checkpoint/i);
    expect(exec).not.toHaveBeenCalled(); expect(query).not.toHaveBeenCalled(); expect(f.row()).toEqual(before);
  });
it('supplied checkpoint workflow identity cannot change the run identity', async () => {
  f.seed(); const before = f.row();
  await expect(resume(JSON.stringify({ ...nextRetryState, workflowId: 'WF' }))).rejects.toThrow(/identity|workflow/i);
  expect(f.row()).toEqual(before); expect(f.raw.prepare('SELECT * FROM retry_audit').all()).toEqual([]);
});
it.each([
  ['state_json', '{'], ['state_json', JSON.stringify({ ...priorRetryState, retryEpoch: 1 })],
  ['state_json', JSON.stringify({ ...priorRetryState, workflowId: 'other' })],
  ['params_json', '{'], ['params_json', '[]'],
  ['params_json', JSON.stringify({ _clawmind_state: JSON.stringify({ ...priorRetryState, checkpoint: 'different' }) })],
  ['params_json', JSON.stringify({ _clawmind_state: { ...priorRetryState, retryEpoch: 3 } })],
] as const)('contradictory saved checkpoint in %s conflicts with no partial writes', async (column, value) => {
  f.seed(); f.raw.prepare(`UPDATE flow_runs SET ${column} = ?`).run(value); f.raw.exec('DELETE FROM retry_audit'); const before = f.row();
  expect(await resume(JSON.stringify(nextRetryState))).toBe('conflict');
  expect(f.row()).toEqual(before); expect(f.raw.prepare('SELECT * FROM retry_audit').all()).toEqual([]);
});
it('omitted state remains backward compatible and never changes Mind or historical params', async () => {
  f.seed(); f.raw.prepare('UPDATE flow_runs SET state_json = ?').run('{"recoveryEpoch":2,"checkpoint":"mind"}');
  f.raw.exec('DELETE FROM retry_audit'); const before = f.row();
  expect(await resume()).toBe('resumed');
  expect(f.row()).toMatchObject({ state_json: before.state_json, params_json: before.params_json, retry_epoch: 3 });
});
it('replaying an accepted retry never replaces a later running checkpoint', async () => {
  f.seed(); const payload = JSON.stringify(nextRetryState); expect(await resume(payload)).toBe('resumed');
  f.raw.prepare('UPDATE flow_runs SET state_json = ?').run(JSON.stringify({ ...nextRetryState, checkpoint: 'progressed' }));
  f.raw.exec('DELETE FROM retry_audit'); const before = f.row();
  f.raw.exec("CREATE TRIGGER no_rewrite BEFORE UPDATE ON flow_runs BEGIN SELECT RAISE(ABORT, 'replayed update'); END");
  expect(await resume(payload)).toBe('resumed'); expect(f.row()).toEqual(before);
  expect(f.raw.prepare('SELECT * FROM retry_audit').all()).toEqual([]);
});
it.each(['cancelled', ' CANCELED ', '\tcancelled\n'])('checkpoint retry cannot undo cancellation %s', async status => {
  f.seed(); f.raw.prepare('UPDATE flow_runs SET status = ?').run(status); f.raw.exec('DELETE FROM retry_audit'); const before = f.row();
  expect(await resume(JSON.stringify(nextRetryState))).toBe('conflict'); expect(f.row()).toEqual(before);
  expect(f.raw.prepare('SELECT * FROM retry_audit').all()).toEqual([]);
});
it('a stale captured checkpoint cannot replace a newer running generation', async () => {
  f.seed(); f.raw.prepare('UPDATE flow_runs SET retry_epoch = 4, status = ?, state_json = ?').run('running', JSON.stringify({ ...nextRetryState, retryEpoch: 4 }));
  f.raw.exec('DELETE FROM retry_audit'); const before = f.row();
  expect(await resume(JSON.stringify(nextRetryState))).toBe('conflict'); expect(f.row()).toEqual(before);
  expect(f.raw.prepare('SELECT * FROM retry_audit').all()).toEqual([]);
});
it('database failure rolls back epoch, canonical/embedded checkpoint and terminal fields together', async () => {
  f.seed(); const before = f.row();
  f.raw.exec("CREATE TRIGGER reject_checkpoint BEFORE UPDATE ON flow_runs BEGIN SELECT RAISE(ABORT, 'checkpoint unavailable'); END");
  await expect(resume(JSON.stringify(nextRetryState))).rejects.toThrow('checkpoint unavailable');
  expect(f.row()).toEqual(before); expect(f.raw.prepare('SELECT * FROM retry_audit').all()).toEqual([]);
});

it.each(['state_json', 'params_json', 'status', 'retry_epoch', 'workflow_id'])('retry CAS rejects a changed selected %s even under NOCASE collation', async column => {
  f.seed(); let raced = false;
  const changed = column === 'retry_epoch' ? 4 : column === 'status' ? 'cancelled'
    : column === 'workflow_id' ? 'WF' : column === 'state_json'
      ? JSON.stringify({ ...priorRetryState, checkpoint: 'PRIOR' }) : JSON.stringify({ input: 'changed by peer' });
  const wrap = (db: IDatabase): IDatabase => ({ dbType: db.dbType, dialect: db.dialect,
    query: async <T>(sql: string, params?: unknown[]) => {
      const rows = await db.query<T>(sql, params);
      if (!raced && /^\s*SELECT\b/i.test(sql) && /params_json/.test(sql)) {
        raced = true; f.raw.prepare(`UPDATE flow_runs SET ${column} = ?`).run(changed);
        f.raw.exec('DELETE FROM retry_audit');
      }
      return rows;
    }, exec: (sql, params) => db.exec(sql, params), transaction: fn => db.transaction(tx => fn(wrap(tx))), close: async () => {},
  });
  expect(await new FlowRunRepository(wrap(f.db)).resumeForRetry('flow', 3, JSON.stringify(nextRetryState))).toBe('conflict');
  expect(raced).toBe(true); expect(f.row()[column]).toBe(changed);
  expect(f.row().completed_at).toBe(456); expect(f.raw.prepare('SELECT * FROM retry_audit').all()).toEqual([]);
});
it('checkpoint admission keeps read errors as thrown database failures', async () => {
  f.seed(); const before = f.row(); vi.spyOn(f.db, 'transaction').mockRejectedValueOnce(new Error('database read unavailable'));
  await expect(resume(JSON.stringify(nextRetryState))).rejects.toThrow('database read unavailable'); expect(f.row()).toEqual(before);
});
it('checkpoint schema incompatibility never downgrades to a state-only or epochless write', async () => {
  f.seed(); f.raw.exec('DROP TRIGGER retry_audit; ALTER TABLE flow_runs DROP COLUMN retry_epoch'); const before = f.row();
  await expect(resume(JSON.stringify(nextRetryState))).rejects.toThrow(/retry_epoch/);
  expect(f.row()).toEqual(before); expect(f.raw.prepare('SELECT * FROM retry_audit').all()).toEqual([]);
});

it.each(['retryEpoch', 'recoveryEpoch', 'both'])('accepts explicit %s generation and preserves its field shape atomically', async field => {
  f.seed();
  const state = (epoch: number) => ({ workflowId: 'wf', ...(field !== 'recoveryEpoch' ? { retryEpoch: epoch } : {}),
    ...(field !== 'retryEpoch' ? { recoveryEpoch: epoch } : {}), checkpoint: epoch === 2 ? 'prior' : 'next' });
  f.raw.prepare('UPDATE flow_runs SET state_json=?, params_json=?').run(JSON.stringify(state(2)), JSON.stringify({ _clawmind_state: state(2), keep: 7 }));
  f.raw.exec('DELETE FROM retry_audit'); const json = JSON.stringify(state(3));
  expect(await resume(json)).toBe('resumed'); expect(f.row().state_json).toBe(json);
  expect(JSON.parse(f.row().params_json as string)).toEqual({ _clawmind_state: state(3), keep: 7 });
});
it.each([
  { recoveryEpoch: 2 }, { recoveryEpoch: '3' }, { recoveryEpoch: null },
  { retryEpoch: 3, recoveryEpoch: 2 }, { retryEpoch: 3, recoveryEpoch: null },
  { recoveryEpoch: 3, retryEpoch: '3' }, { recoveryEpoch: 3, retryEpoch: null },
])('invalid or contradictory supplied epoch aliases %j cannot fallback', async aliases => {
  f.seed(); const before = f.row();
  await expect(resume(JSON.stringify(aliases))).rejects.toThrow(/epoch|Epoch/); expect(f.row()).toEqual(before);
});
it.each([
  { recoveryEpoch: 1 }, { recoveryEpoch: '2' }, { recoveryEpoch: null },
  { retryEpoch: 2, recoveryEpoch: 1 }, { retryEpoch: 2, recoveryEpoch: null },
  { recoveryEpoch: 2, retryEpoch: '2' },
])('invalid saved epoch aliases %j conflict instead of trusting a valid sibling', async aliases => {
  f.seed(); const prior = JSON.stringify({ workflowId: 'wf', ...aliases });
  f.raw.prepare('UPDATE flow_runs SET state_json=?, params_json=?').run(prior, JSON.stringify({ _clawmind_state: prior }));
  const before = f.row(); expect(await resume(JSON.stringify({ recoveryEpoch: 3 }))).toBe('conflict'); expect(f.row()).toEqual(before);
});
for (const key of ['_fanoutIdentity', '_fanoutRevision']) {
  it.each(['saved', 'incoming'])(`retry checkpoint cannot bypass managed child ${key} in %s`, async where => {
    f.seed(); const marker = { [key]: key === '_fanoutRevision' ? 1 : { parentFlowId: 'parent' } };
    if (where === 'saved') {
      const prior = JSON.stringify({ ...priorRetryState, ...marker });
      f.raw.prepare('UPDATE flow_runs SET state_json=?, params_json=?').run(prior, JSON.stringify({ _clawmind_state: prior }));
    }
    const next = { ...nextRetryState, ...(where === 'incoming' ? marker : {}) }; const before = f.row();
    expect(await resume(JSON.stringify(next))).toBe('conflict'); expect(f.row()).toEqual(before);
  });
}
it('retry checkpoint cannot change immutable fanout origin', async () => {
  f.seed(); const prior = { ...priorRetryState, fanoutOrigin: { parentFlowId: 'parent', itemId: 'item' } };
  f.raw.prepare('UPDATE flow_runs SET state_json=?, params_json=?').run(JSON.stringify(prior), JSON.stringify({ _clawmind_state: prior }));
  const before = f.row();
  expect(await resume(JSON.stringify({ ...nextRetryState, fanoutOrigin: { parentFlowId: 'other', itemId: 'item' } }))).toBe('conflict');
  expect(f.row()).toEqual(before);
});
it.each(['omitted', 'changed', 'same'])('retry checkpoint retains authoritative groups and origin with %s incoming groups', async mode => {
  f.seed(); const origin = { parentFlowId: 'parent', itemId: 'item' }; const groups = { fanout: { revision: 4, status: 'running' } };
  const prior = { ...priorRetryState, fanoutOrigin: origin, subworkflowFanouts: groups };
  f.raw.prepare('UPDATE flow_runs SET state_json=?, params_json=?').run(JSON.stringify(prior), JSON.stringify({ _clawmind_state: JSON.stringify(prior) }));
  const next = { ...nextRetryState, ...(mode === 'omitted' ? {} : { subworkflowFanouts: mode === 'same' ? groups : { forged: { revision: 0 } } }) };
  expect(await resume(JSON.stringify(next))).toBe('resumed'); const saved = JSON.parse(f.row().state_json as string);
  expect(saved).toEqual({ ...nextRetryState, fanoutOrigin: origin, subworkflowFanouts: groups });
  expect(JSON.parse(JSON.parse(f.row().params_json as string)._clawmind_state)).toEqual(saved);
});
it('retry cannot author a new authoritative group through checkpoint', async () => {
  f.seed(); expect(await resume(JSON.stringify({ ...nextRetryState, subworkflowFanouts: { forged: {} } }))).toBe('resumed');
  expect(JSON.parse(f.row().state_json as string)).not.toHaveProperty('subworkflowFanouts');
});
