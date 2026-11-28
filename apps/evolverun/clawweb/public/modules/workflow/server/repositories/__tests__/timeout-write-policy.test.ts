import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FlowRunRepository } from '../flow-run-repository.js';
import { NOW_SECONDS, structuredTimeout, timeoutEvidence, timeoutFixture } from './timeout-write.fixture.js';

let f: ReturnType<typeof timeoutFixture>;
beforeEach(() => { f = timeoutFixture(); });
afterEach(async () => { await f.close(); });
const completion = (resultJson = timeoutEvidence(), extra: Record<string, unknown> = {}) => ({
  status: 'failed', resultJson, completedAt: NOW_SECONDS + 99_999, succeededCount: 0, failedCount: 1, ...extra,
});
const claim = (reason = timeoutEvidence(), extra: Record<string, unknown> = {}) => ({
  reason, currentPhase: 'timeout', completedAt: NOW_SECONDS + 99_999, ...extra,
});

const classified = JSON.stringify({ reason: 'flow_timeout' });

for (const path of ['completion', 'watchdog']) {
  const settle = (reason: string, extra: Record<string, unknown> = {}) => path === 'completion'
    ? f.repo.updateCompletion('flow', completion(reason, extra)) : f.repo.markFailedIfRunning('flow', claim(reason, extra));

  it.each([
    [null, classified], ['not-json', structuredTimeout(classified)], ['{}', timeoutEvidence()],
    ['{"effectiveFlowTimeoutMinutes":0}', JSON.stringify({ reason: 'timeout', ranForSecs: 'unknown' })],
    ['{"effectiveFlowTimeoutMinutes":1440}', timeoutEvidence(7200, 1)],
    ['{"workflowSnapshot":{"flowTimeoutMinutes":1.125}}', structuredTimeout(timeoutEvidence(60, 9000))],
  ] as const)(`${path} records engine timeout without proving state %j or duration %j`, async (stateJson, reason) => {
    f.seed({}, { elapsed: -1, stateJson }); // Clock skew is not a write conflict.
    expect(await settle(reason, { totalDurationMs: 7_400_001 })).toBe(true);
    expect(f.row()).toMatchObject({ status: 'failed', completed_at: NOW_SECONDS, result_json: reason, total_duration_ms: 7_400_001 });
  });

  it.each([-1, Number.NaN, Infinity, '1'])(`${path} still rejects malformed total_duration_ms %j`, async totalDurationMs => {
    f.seed();
    const before = f.row();
    expect(await settle(classified, { totalDurationMs })).toBe(false);
    expect(f.row()).toEqual(before);
  });
  it.each(['running', 'blocked', 'waiting'])(`${path} cannot overwrite settled but nonterminal %s`, async status => {
    f.seed(undefined, { status });
    f.raw.exec('UPDATE flow_runs SET completed_at = 0');
    const before = f.row();
    expect(await settle(timeoutEvidence())).toBe(false);
    expect(f.row()).toEqual(before);
  });
  it(`${path} still fences explicit epochs even on a schema without retry_epoch`, async () => {
    f.seed({}, { epoch: 2 });
    expect(await settle(classified, { retryEpoch: 0 })).toBe(false);
    f.raw.exec('ALTER TABLE flow_runs DROP COLUMN retry_epoch');
    expect(await settle(classified, { retryEpoch: 1 })).toBe(false);
    expect(await settle(classified, { retryEpoch: 0 })).toBe(true);
    expect(f.row().completed_at).toBe(NOW_SECONDS);
  });
}
it.each(['', '   ', 42, {}])('dedicated watchdog still needs a nonempty string reason: %j', async reason => {
  f.seed();
  const before = f.row();
  expect(await f.repo.markFailedIfRunning('flow', claim('unused', { reason }))).toBe(false);
  expect(f.row()).toEqual(before);
});

for (const terminal of ['succeeded', 'cancelled', 'canceled']) {
  for (const epoch of [null, 0, 2]) {
    it(`protects ${terminal}, stored epoch ${epoch}, from timeout and plain failed writes`, async () => {
      f.seed(undefined, { status: terminal, epoch });
      const before = f.row();
      for (const retryEpoch of [undefined, 0, 2]) {
        expect(await f.repo.updateCompletion('flow', completion(timeoutEvidence(), { retryEpoch }))).toBe(false);
        expect(await f.repo.updateCompletion('flow', completion('plain failure', { retryEpoch }))).toBe(false);
        expect(await f.repo.updateStatus('flow', 'failed', retryEpoch)).toBe(false);
        expect(await f.repo.markFailedIfRunning('flow', claim(timeoutEvidence(), { retryEpoch }))).toBe(false);
      }
      expect(f.row()).toEqual(before);
    });
  }
}
it.each(['running', 'waiting', 'blocked'])('/status failed cannot settle ambiguous %s failures', async status => {
  f.seed(undefined, { status });
  const before = f.row();
  expect(await f.repo.updateStatus('flow', 'failed')).toBe(false);
  expect(f.row()).toEqual(before);
  expect(await f.repo.updateCompletion('flow', completion('normal controller failure'))).toBe(true);
});
it('protects a settled ordinary failure from detail supplements', async () => {
  f.seed({}, { status: 'failed', epoch: 2 });
  const before = f.row();
  expect(await f.repo.updateCompletion('flow', completion('settled details', { retryEpoch: 2 }))).toBe(false);
  expect(f.row()).toEqual(before);
});

describe('exact atomic snapshot CAS', () => {
  const mutations = [
    ['started_at', "UPDATE flow_runs SET started_at = started_at + 1"],
    ['status', "UPDATE flow_runs SET status = 'waiting'"],
    ['policy', `UPDATE flow_runs SET state_json = '{"effectiveFlowTimeoutMinutes":1440}'`],
    ['byte-exact state', `UPDATE flow_runs SET state_json = '{"effectiveFlowTimeoutMinutes":120,"label":"A"}'`],
    ['raw NULL epoch versus zero', 'UPDATE flow_runs SET retry_epoch = 0'],
    ['positive epoch', 'UPDATE flow_runs SET retry_epoch = 2'],
  ];
  for (const path of ['completion', 'watchdog']) {
    it.each(mutations)(`${path} loses when %s changes after snapshot SELECT`, async (_field, sql) => {
      f.seed({ effectiveFlowTimeoutMinutes: 120, label: 'a' }, { epoch: null });
      let concurrent: Record<string, unknown> | undefined;
      const racing = f.afterPolicyRead(() => { f.raw.exec(sql); concurrent = f.row(); });
      const repo = new FlowRunRepository(racing.db);
      const accepted = path === 'completion'
        ? await repo.updateCompletion('flow', completion()) : await repo.markFailedIfRunning('flow', claim());
      expect(accepted).toBe(false);
      expect(racing.hit()).toBe(true);
      expect(f.row()).toEqual(concurrent);
    });
  }
});

it('concurrent repository completions acknowledge only one true CAS transition', async () => {
  f.seed();
  const outcomes = await Promise.all([
    f.repo.updateCompletionOutcome('flow', completion()),
    f.repo.updateCompletionOutcome('flow', completion(structuredTimeout())),
    f.repo.updateCompletionOutcome('flow', completion()),
  ]);
  expect(outcomes.filter(outcome => outcome === 'updated')).toHaveLength(1);
  expect(outcomes.filter(outcome => outcome === 'duplicate')).toHaveLength(1);
  expect(outcomes.filter(outcome => outcome === 'rejected')).toHaveLength(1);
  expect(f.row().completed_at).toBe(NOW_SECONDS);
});
it('concurrent dedicated watchdogs claim exactly once', async () => {
  f.seed();
  const claims = await Promise.all(Array.from({ length: 4 }, () => f.repo.markFailedIfRunning('flow', claim())));
  expect(claims.filter(Boolean)).toHaveLength(1);
});
it('an already-settled timeout duplicate must still match the exact snapshot', async () => {
  f.seed(undefined, { epoch: null });
  expect(await f.repo.updateCompletion('flow', completion())).toBe(true);
  const racing = f.afterPolicyRead(() => f.raw.exec('UPDATE flow_runs SET retry_epoch = 0'));
  expect(await new FlowRunRepository(racing.db).updateCompletion('flow', completion())).toBe(false);
  expect(racing.hit()).toBe(true);
});
it('a concurrent classified timeout in another epoch is not a duplicate of this completion', async () => {
  f.seed(undefined, { epoch: 2 });
  const racing = f.afterPolicyRead(() => f.raw.prepare(
    "UPDATE flow_runs SET retry_epoch = 3, status = 'failed', completed_at = ?, result_json = ?",
  ).run(NOW_SECONDS, classified));
  expect(await new FlowRunRepository(racing.db).updateCompletionOutcome('flow', completion(classified, { retryEpoch: 2 }))).toBe('rejected');
  expect(racing.hit()).toBe(true);
  expect(f.row()).toMatchObject({ retry_epoch: 3, result_json: classified });
});
it('legacy watchdog duplicate recognition fences the saved timeout phase', async () => {
  f.seed();
  expect(await f.repo.markFailedIfRunning('flow', claim(timeoutEvidence(1, 2, 'other')))).toBe(true);
  const racing = f.afterPolicyRead(() => f.raw.exec("UPDATE flow_runs SET current_phase = 'P1'"));
  expect(await new FlowRunRepository(racing.db).updateCompletionOutcome('flow', completion())).toBe('rejected');
  expect(racing.hit()).toBe(true);
  expect(f.row().current_phase).toBe('P1');
});
it.each(['completed_at = 1', "result_json = 'concurrent node result'"])(
  'CAS also matches selected %s', async change => {
    f.seed();
    const racing = f.afterPolicyRead(() => f.raw.exec(`UPDATE flow_runs SET ${change}`));
    expect(await new FlowRunRepository(racing.db).markFailedIfRunning('flow', claim())).toBe(false);
    expect(racing.hit()).toBe(true);
    expect(f.row().status).toBe('running');
  });
it.each(['mysql', 'zdas'] as const)('renders exact MySQL-driver SQL for %s without NULL/zero coalescing', async dbType => {
  f.seed(undefined, { epoch: null });
  let updateSql = '';
  const db = {
    dbType, dialect: { ...f.db.dialect, driver: 'mysql2' as const },
    query: f.db.query.bind(f.db), transaction: f.db.transaction.bind(f.db), close: async () => {},
    exec: async (sql: string) => { updateSql = sql; return { affectedRows: 0 }; },
  };
  expect(await new FlowRunRepository(db).markFailedIfRunning('flow', claim())).toBe(false);
  expect(updateSql).toContain('BINARY state_json = BINARY ?');
  expect(updateSql).toContain('BINARY status = BINARY ?');
  expect(updateSql).toContain('retry_epoch IS NULL');
  expect(updateSql).not.toContain('COALESCE');
});
it('a dedicated claim accepts opaque text verbatim, but a later generic retry cannot infer its category', async () => {
  f.seed();
  expect(await f.repo.markFailedIfRunning('flow', claim('engine stopped the run'))).toBe(true);
  const settled = f.row();
  expect(settled.result_json).toBe('engine stopped the run');
  expect(await f.repo.updateCompletionOutcome('flow', completion())).toBe('rejected');
  expect(f.row()).toEqual(settled);
});
it('does not equate a different timeout result with an unclassified watchdog envelope', async () => {
  f.seed({});
  const reason = JSON.stringify({ reason: 'other', timeoutSecs: null, ranForSecs: -1 });
  expect(await f.repo.markFailedIfRunning('flow', claim(reason))).toBe(true);
  const settled = f.row();
  expect(settled.result_json).toBe(reason);
  expect(await f.repo.updateCompletionOutcome('flow', completion(classified))).toBe('rejected');
  expect(f.row()).toEqual(settled);
});
it('does not acknowledge an ordinary failure just because it contains duration fields', async () => {
  f.seed();
  expect(await f.repo.updateCompletionOutcome('flow', completion(timeoutEvidence(60, 90, 'node_failure')))).toBe('updated');
  const settled = f.row();
  expect(await f.repo.updateCompletionOutcome('flow', completion())).toBe('rejected');
  expect(f.row()).toEqual(settled);
});
it('does not hide genuine snapshot-read errors behind legacy-schema fallback', async () => {
  f.seed();
  const broken = { dbType: f.db.dbType, dialect: f.db.dialect,
    query: async <T>(): Promise<T[]> => { throw new Error('database unavailable'); },
    exec: f.db.exec.bind(f.db), transaction: f.db.transaction.bind(f.db), close: async () => {},
  };
  await expect(new FlowRunRepository(broken).markFailedIfRunning('flow', claim())).rejects.toThrow('database unavailable');
  expect(f.row().status).toBe('running');
});

// Rebuild only the empty fixture table's status collation. Keep every other
// column/constraint identical, including the NOCASE state used by CAS tests.
describe.each(['NOCASE', 'BINARY'])('persisted status protection with SQLite %s', collation => {
  beforeEach(() => {
    if (collation === 'BINARY') {
      const { sql } = f.raw.prepare("SELECT sql FROM sqlite_master WHERE name = 'flow_runs'").get() as { sql: string };
      expect(sql).toContain('status TEXT COLLATE NOCASE');
      f.raw.exec(`DROP TABLE flow_runs; ${sql.replace('status TEXT COLLATE NOCASE', 'status TEXT COLLATE BINARY')}`);
    }
  });
  const variants = (status: string) => [status.toUpperCase(), `${status[0].toUpperCase()}${status.slice(1)}`,
    ` ${status}`, `${status} `, `\t${status}\n`, `\u00a0${status}\ufeff`];
  const terminals = ['succeeded', 'cancelled', 'canceled', 'failed'].flatMap(variants);

  it.each(terminals)('watchdogs cannot reopen saved terminal %j', async status => {
    f.seed(undefined, { status });
    f.raw.prepare('UPDATE flow_runs SET completed_at = ?').run(NOW_SECONDS - 1);
    const before = f.row();
    expect(await f.repo.updateCompletionOutcome('flow', completion())).toBe('rejected');
    expect(await f.repo.markFailedIfRunning('flow', claim())).toBe(false);
    expect(f.row()).toEqual(before);
  });
  it.each(['RUNNING', ' running ', 'running\t'])('watchdogs reject noncanonical nonterminal %j too', async status => {
    f.seed(undefined, { status });
    const before = f.row();
    expect(await f.repo.updateCompletionOutcome('flow', completion())).toBe('rejected');
    expect(await f.repo.markFailedIfRunning('flow', claim())).toBe(false);
    expect(f.row()).toEqual(before);
  });
  it.each(['running', 'failed'])('plain failure loses a byte-only %s status race', async status => {
    f.seed(undefined, { status });
    let hit = false;
    let concurrent: Record<string, unknown> | undefined;
    const db = {
      dbType: f.db.dbType, dialect: f.db.dialect,
      query: async <T>(sql: string, params?: unknown[]) => {
        const rows = await f.db.query<T>(sql, params);
        if (!hit && /^\s*SELECT status\b/i.test(sql)) {
          hit = true;
          f.raw.prepare('UPDATE flow_runs SET status = ?').run(status.toUpperCase());
          concurrent = f.row();
        }
        return rows;
      },
      exec: f.db.exec.bind(f.db), transaction: f.db.transaction.bind(f.db), close: async () => {},
    };
    expect(await new FlowRunRepository(db).updateCompletionOutcome('flow', completion('plain failure'))).toBe('rejected');
    expect(hit).toBe(true);
    expect(f.row()).toEqual(concurrent);
  });
  it.each(['succeeded', 'cancelled', 'canceled'].flatMap(variants))(
    'plain failure cannot overwrite saved success/cancellation %j', async status => {
      f.seed(undefined, { status, epoch: 2 });
      f.raw.prepare('UPDATE flow_runs SET completed_at = ?').run(NOW_SECONDS - 1);
      const before = f.row();
      for (const retryEpoch of [undefined, 0, 2]) {
        expect(await f.repo.updateCompletionOutcome('flow', completion('plain failure', { retryEpoch }))).toBe('rejected');
        expect(f.row()).toEqual(before);
      }
    });

  const failureCases = ['failed', 'FAILED', ' failed ', '\tfailed\n', '\u00a0failed\ufeff'].flatMap(status =>
    ([
      [null, undefined], [null, 0], [0, undefined], [0, 0], [2, undefined],
      [2, 2], [2, 0], [0, 2], [null, 2],
    ] as const).map(([storedEpoch, requestEpoch]) => ({ status, storedEpoch, requestEpoch })));
  for (const completedAt of [null, NOW_SECONDS - 1]) {
    it.each(failureCases)(
      `terminal $status with epoch $storedEpoch/$requestEpoch and completed_at=${completedAt} rejects supplements`,
      async ({ status, storedEpoch, requestEpoch }) => {
        f.seed(undefined, { status, epoch: storedEpoch });
        f.raw.prepare('UPDATE flow_runs SET completed_at = ?').run(completedAt);
        const before = f.row();
        expect(await f.repo.updateCompletionOutcome('flow', completion('different details', {
          retryEpoch: requestEpoch,
        }))).toBe('rejected');
        expect(f.row()).toEqual(before);
      });
  }
  it.each(['FAILED', ' failed ', '\tfailed\n', '\u00a0failed\ufeff'])(
    'HTTP noncanonical terminal %j cannot be supplemented or observed', async status => {
      f.seed(undefined, { status });
      const before = f.row(); const put = await f.http();
      expect((await put('completion', { status: 'failed', result_json: 'details' })).status).toBe(409);
      expect(f.row()).toEqual(before);
      expect(f.onTerminal).not.toHaveBeenCalled();
    });

});

it.each(['completion', 'watchdog'])('acknowledges a settled %s after a clock rollback without another write', async path => {
  f.seed({});
  vi.mocked(Date.now).mockReturnValue(NOW_SECONDS * 1000 + 999);
  if (path === 'completion') expect(await f.repo.updateCompletionOutcome('flow', completion(classified))).toBe('updated');
  else expect(await f.repo.markFailedIfRunning('flow', claim(classified))).toBe(true);
  const settled = f.row();
  expect(settled.completed_at).toBe(NOW_SECONDS);
  vi.mocked(Date.now).mockReturnValue((NOW_SECONDS - 1) * 1000);
  expect(await f.repo.updateCompletionOutcome('flow', completion(classified, { succeededCount: undefined, failedCount: undefined }))).toBe('duplicate');
  expect(await f.repo.markFailedIfRunning('flow', claim())).toBe(false);
  expect(f.row()).toEqual(settled);
});
it('a failed row without a completion timestamp is not a settled timeout', async () => {
  f.seed(undefined, { status: 'failed' });
  f.raw.prepare('UPDATE flow_runs SET completed_at = NULL, result_json = ?').run(classified);
  const before = f.row();
  expect(await f.repo.updateCompletionOutcome('flow', completion())).toBe('rejected');
  expect(f.row()).toEqual(before);
});

it.each(['mysql', 'zdas'] as const)('plain-failure %s SQL fences its one write against the raw saved status', async dbType => {
  f.seed();
  const writes: Array<{ sql: string; params?: unknown[] }> = [];
  const db = {
    dbType, dialect: { ...f.db.dialect, driver: 'mysql2' as const },
    query: f.db.query.bind(f.db), transaction: f.db.transaction.bind(f.db), close: async () => {},
    exec: async (sql: string, params?: unknown[]) => { writes.push({ sql, params }); return { affectedRows: 0 }; },
  };
  expect(await new FlowRunRepository(db).updateCompletionOutcome('flow', completion('plain failure', { retryEpoch: 0 }))).toBe('rejected');
  expect(writes).toHaveLength(1);
  for (const { sql, params } of writes) {
    expect(sql).toContain('BINARY status = BINARY ?');
    expect(sql).not.toContain('COLLATE BINARY');
    expect(params).toContain('flow');
    expect(params).toContain('running');
    expect(sql).toContain('retry_epoch = ?');
  }
});
