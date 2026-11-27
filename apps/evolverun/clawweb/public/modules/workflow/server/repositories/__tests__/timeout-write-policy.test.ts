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

for (const budget of [1440, 0, null, -1, '120', false, {}, 'invalid']) {
  it(`rejects legacy 7200s completion against saved ${JSON.stringify(budget)} without falling back`, async () => {
    f.seed({ effectiveFlowTimeoutMinutes: budget, workflowSnapshot: { flowTimeoutMinutes: 120 } });
    const before = f.row();
    expect(await f.repo.updateCompletion('flow', completion())).toBe(false);
    expect(f.row()).toEqual(before);
  });
}

it.each([timeoutEvidence(), timeoutEvidence(7200, 7395, 'timeout'), structuredTimeout()])(
  'recognizes direct and deployed message-JSON timeout evidence: %s', async resultJson => {
    f.seed({ effectiveFlowTimeoutMinutes: 1440 });
    const before = f.row();
    expect(await f.repo.updateCompletion('flow', completion(resultJson))).toBe(false);
    expect(f.row()).toEqual(before);
  });

it.each([undefined, {}, { workflowSnapshot: {} }, { timeoutMinutes: 120 }, { workflowSnapshot: { flowTimeoutMinutes: 0 } }])(
  'fails closed for unprovable legacy state %j', async state => {
    f.seed(state, state === undefined ? { stateJson: null } : {});
    expect(await f.repo.updateCompletion('flow', completion())).toBe(false);
  });
it.each(['not-json', 'null', '[]'])('fails closed for malformed saved state %s', async stateJson => {
  f.seed({}, { stateJson });
  expect(await f.repo.markFailedIfRunning('flow', claim())).toBe(false);
});
it.each([{ flowTimeoutMinutes: 120 }, { timeoutMinutes: 120 }])('accepts provable legacy snapshot %j', async workflowSnapshot => {
  f.seed({ workflowSnapshot });
  expect(await f.repo.updateCompletion('flow', completion())).toBe(true);
  expect(f.row()).toMatchObject({ status: 'failed', completed_at: NOW_SECONDS });
});

it.each([
  ['wall has not reached budget', 7199, timeoutEvidence(7200, 7200), undefined],
  ['claim exceeds wall time', 7400, timeoutEvidence(7200, 7401), undefined],
  ['claim below budget', 7400, timeoutEvidence(7200, 7199), undefined],
  ['mismatched claimed budget', 7400, timeoutEvidence(3600, 7395), undefined],
  ['missing elapsed evidence', 7400, JSON.stringify({ reason: 'flow_timeout', timeoutSecs: 7200 }), undefined],
  ['string evidence', 7400, JSON.stringify({ reason: 'flow_timeout', timeoutSecs: '7200', ranForSecs: 7395 }), undefined],
  ['negative active duration', 7400, timeoutEvidence(), -1],
  ['active duration exceeds wall', 7400, timeoutEvidence(), 7_400_001],
  ['invalid active duration', 7400, timeoutEvidence(), Number.NaN],
] as const)('rejects %s, even with a future caller completedAt', async (_label, elapsed, reason, totalDurationMs) => {
  f.seed(undefined, { elapsed });
  const before = f.row();
  expect(await f.repo.updateCompletion('flow', completion(reason, { totalDurationMs }))).toBe(false);
  expect(await f.repo.markFailedIfRunning('flow', claim(reason, { totalDurationMs }))).toBe(false);
  expect(f.row()).toEqual(before);
});

it('allows recovered active elapsed below wall and uses the server completion timestamp', async () => {
  f.seed(undefined, { elapsed: 9000 });
  expect(await f.repo.markFailedIfRunning('flow', claim(timeoutEvidence(7200, 7300), { totalDurationMs: 7_300_000 }))).toBe(true);
  expect(f.row()).toMatchObject({ status: 'failed', completed_at: NOW_SECONDS, total_duration_ms: 7_300_000 });
});
it('accepts exactly at the server budget boundary', async () => {
  f.seed(undefined, { elapsed: 7200 });
  expect(await f.repo.updateCompletion('flow', completion(timeoutEvidence(7200, 7200)))).toBe(true);
  expect(f.row().completed_at).toBe(NOW_SECONDS);
});
it.each(['unclassified failure', JSON.stringify({ reason: 'other' }), JSON.stringify({ reason: 'other', timeoutSecs: 7200, ranForSecs: 7395 })])(
  'fail-if-running is always a watchdog path: %s', async reason => {
    f.seed({ effectiveFlowTimeoutMinutes: 1440 });
    expect(await f.repo.markFailedIfRunning('flow', claim(reason))).toBe(false);
    expect(f.row().status).toBe('running');
  });
it('requires evidence even when an unclassified watchdog reason has a matching saved budget', async () => {
  f.seed();
  expect(await f.repo.markFailedIfRunning('flow', claim('timeout'))).toBe(false);
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
it('preserves non-timeout failure settlement and failed-row supplements', async () => {
  f.seed({}, { status: 'failed', epoch: 2 });
  expect(await f.repo.updateCompletion('flow', completion('settled details'))).toBe(true);
  expect(f.row().result_json).toBe('settled details');
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
    it.each(mutations)(`${path} loses when %s changes after policy SELECT`, async (_field, sql) => {
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
  expect(outcomes.filter(outcome => outcome === 'duplicate')).toHaveLength(2);
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
it('watchdog reason classification is not required, but matching evidence is', async () => {
  f.seed();
  expect(await f.repo.markFailedIfRunning('flow', claim(timeoutEvidence(7200, 7395, 'other')))).toBe(true);
});
it('does not hide genuine policy-read errors behind legacy-schema fallback', async () => {
  f.seed();
  const broken = { dbType: f.db.dbType, dialect: f.db.dialect,
    query: async <T>(): Promise<T[]> => { throw new Error('policy database unavailable'); },
    exec: f.db.exec.bind(f.db), transaction: f.db.transaction.bind(f.db), close: async () => {},
  };
  await expect(new FlowRunRepository(broken).markFailedIfRunning('flow', claim())).rejects.toThrow('policy database unavailable');
  expect(f.row().status).toBe('running');
});
it('a saved 1440 minute run can time out only with its actual day-long budget', async () => {
  f.seed({ effectiveFlowTimeoutMinutes: 1440 }, { elapsed: 90_000, epoch: 2 });
  expect(await f.repo.updateCompletion('flow', completion(timeoutEvidence(86_400, 86_500)))).toBe(true);
  expect(f.row()).toMatchObject({ status: 'failed', completed_at: NOW_SECONDS, retry_epoch: 2 });
});

// Rebuild only the empty fixture table's status collation. Keep every other
// column/constraint identical, including the NOCASE policy used by CAS tests.
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
  it.each(failureCases)(
    'settled $status with saved epoch $storedEpoch and request $requestEpoch is never another transition',
    async ({ status, storedEpoch, requestEpoch }) => {
      f.seed(undefined, { status, epoch: storedEpoch });
      f.raw.prepare('UPDATE flow_runs SET completed_at = ?').run(NOW_SECONDS - 1);
      const before = f.row();
      const allowed = requestEpoch === undefined || ((storedEpoch ?? 0) === 0 && requestEpoch === 0);
      expect(await f.repo.updateCompletionOutcome('flow', completion('supplement details', {
        completedAt: NOW_SECONDS, retryEpoch: requestEpoch,
      }))).toBe(allowed ? 'duplicate' : 'rejected');
      if (allowed) {
        expect(f.row()).toMatchObject({ status: 'failed', result_json: 'supplement details', completed_at: NOW_SECONDS });
      } else {
        expect(f.row()).toEqual(before);
      }
    });
  it.each(failureCases)(
    'unsettled $status with saved epoch $storedEpoch and request $requestEpoch keeps first-settlement semantics',
    async ({ status, storedEpoch, requestEpoch }) => {
      f.seed(undefined, { status, epoch: storedEpoch });
      f.raw.exec('UPDATE flow_runs SET completed_at = NULL');
      const before = f.row();
      const allowed = requestEpoch === undefined || (storedEpoch ?? 0) === requestEpoch;
      const first = completion('first settlement', { completedAt: NOW_SECONDS, retryEpoch: requestEpoch });
      expect(await f.repo.updateCompletionOutcome('flow', first)).toBe(allowed ? 'updated' : 'rejected');
      if (!allowed) { expect(f.row()).toEqual(before); return; }
      expect(f.row()).toMatchObject({ status: 'failed', result_json: 'first settlement', completed_at: NOW_SECONDS });
      const settled = f.row();
      const later = completion('later supplement', { completedAt: NOW_SECONDS, retryEpoch: requestEpoch });
      expect(await f.repo.updateCompletionOutcome('flow', later)).toBe(requestEpoch === 2 ? 'rejected' : 'duplicate');
      if (requestEpoch === 2) expect(f.row()).toEqual(settled);
      else expect(f.row().result_json).toBe('later supplement');
    });

  const observerCases = [
    { status: 'FAILED', storedEpoch: null, requestEpoch: undefined },
    { status: ' failed ', storedEpoch: 0, requestEpoch: 0 },
    { status: '\tfailed\n', storedEpoch: 2, requestEpoch: 2 },
    { status: '\u00a0failed\ufeff', storedEpoch: 2, requestEpoch: undefined },
  ].flatMap(test => [null, NOW_SECONDS - 1].map(completedAt => ({ ...test, completedAt })));
  it.each(observerCases)(
    'HTTP $status epoch $storedEpoch/$requestEpoch completed $completedAt observes only its first settlement',
    async ({ status, storedEpoch, requestEpoch, completedAt }) => {
      f.seed(undefined, { status, epoch: storedEpoch });
      f.raw.prepare('UPDATE flow_runs SET completed_at = ?').run(completedAt);
      const put = await f.http();
      const body = { status: 'failed', result_json: 'ordinary failure details', completed_at: NOW_SECONDS,
        succeeded_count: 0, failed_count: 1, retry_epoch: requestEpoch };
      const firstAllowed = completedAt === null || requestEpoch !== 2;
      const before = f.row();
      expect((await put('completion', body)).status).toBe(firstAllowed ? 200 : 409);
      expect(f.onTerminal).toHaveBeenCalledTimes(completedAt === null ? 1 : 0);
      if (firstAllowed) expect(f.row().result_json).toBe(body.result_json);
      else expect(f.row()).toEqual(before);
      const settled = f.row();
      expect((await put('completion', { ...body, result_json: 'later details' })).status).toBe(requestEpoch === 2 ? 409 : 200);
      expect(f.onTerminal).toHaveBeenCalledTimes(completedAt === null ? 1 : 0);
      if (requestEpoch === 2) expect(f.row()).toEqual(settled);
      else expect(f.row().result_json).toBe('later details');
    });

});

describe('fractional timeout deadline and whole-second server completion', () => {
  const minutes = 1.125;
  const budget = minutes * 60; // 67.5 seconds; deadline is NOW_SECONDS + 0.5.
  const reason = timeoutEvidence(budget, budget);

  it.each(['completion', 'watchdog'])('acknowledges a rounded %s settlement without another write', async path => {
    f.seed({ effectiveFlowTimeoutMinutes: minutes }, { elapsed: 67 });
    vi.mocked(Date.now).mockReturnValue(NOW_SECONDS * 1000 + 500);
    if (path === 'completion') {
      expect(await f.repo.updateCompletionOutcome('flow', completion(reason))).toBe('updated');
    } else {
      expect(await f.repo.markFailedIfRunning('flow', claim(reason))).toBe(true);
    }
    const settled = f.row();
    expect(settled.completed_at).toBe(NOW_SECONDS);
    expect(await f.repo.updateCompletionOutcome('flow', completion(reason))).toBe('duplicate');
    vi.mocked(Date.now).mockReturnValue((NOW_SECONDS + 10) * 1000);
    expect(await f.repo.updateCompletionOutcome('flow', completion(reason))).toBe('duplicate');
    expect(await f.repo.markFailedIfRunning('flow', claim(reason))).toBe(false);
    expect(f.row()).toEqual(settled);
  });
  it('does not round down the wall deadline for the first write', async () => {
    f.seed({ effectiveFlowTimeoutMinutes: minutes }, { elapsed: 67 });
    vi.mocked(Date.now).mockReturnValue(NOW_SECONDS * 1000 + 499);
    const before = f.row();
    expect(await f.repo.updateCompletionOutcome('flow', completion(reason))).toBe('rejected');
    expect(await f.repo.markFailedIfRunning('flow', claim(reason))).toBe(false);
    expect(f.row()).toEqual(before);
    vi.mocked(Date.now).mockReturnValue(NOW_SECONDS * 1000 + 500);
    expect(await f.repo.updateCompletionOutcome('flow', completion(reason))).toBe('updated');
  });
  it.each([
    ['earlier second', NOW_SECONDS - 1, reason],
    ['premature fractional timestamp', NOW_SECONDS + 0.25, reason],
    ['future timestamp', NOW_SECONDS + 1, reason],
    ['missing timestamp', null, reason],
    ['insufficient saved elapsed', NOW_SECONDS, timeoutEvidence(budget, budget - 0.001)],
    ['missing saved evidence', NOW_SECONDS, 'plain failure'],
  ] as const)('does not acknowledge a settled timeout with %s', async (_label, completedAt, savedReason) => {
    f.seed({ effectiveFlowTimeoutMinutes: minutes }, { status: 'failed', elapsed: 67 });
    vi.mocked(Date.now).mockReturnValue(NOW_SECONDS * 1000 + 500);
    f.raw.prepare('UPDATE flow_runs SET completed_at = ?, result_json = ?').run(completedAt, savedReason);
    const before = f.row();
    expect(await f.repo.updateCompletionOutcome('flow', completion(reason))).toBe('rejected');
    expect(f.row()).toEqual(before);
  });
  it('rejects even a rounded saved timestamp before the precise wall deadline', async () => {
    f.seed({ effectiveFlowTimeoutMinutes: minutes }, { status: 'failed', elapsed: 67 });
    vi.mocked(Date.now).mockReturnValue(NOW_SECONDS * 1000 + 499);
    f.raw.prepare('UPDATE flow_runs SET completed_at = ?, result_json = ?').run(NOW_SECONDS, reason);
    const before = f.row();
    expect(await f.repo.updateCompletionOutcome('flow', completion(reason))).toBe('rejected');
    expect(f.row()).toEqual(before);
  });
  it('acknowledges concurrent fractional-deadline completions after only one transition', async () => {
    f.seed({ effectiveFlowTimeoutMinutes: minutes }, { elapsed: 67 });
    vi.mocked(Date.now).mockReturnValue(NOW_SECONDS * 1000 + 500);
    const outcomes = await Promise.all(Array.from({ length: 3 }, () =>
      f.repo.updateCompletionOutcome('flow', completion(reason))));
    expect(outcomes.filter(outcome => outcome === 'updated')).toHaveLength(1);
    expect(outcomes.filter(outcome => outcome === 'duplicate')).toHaveLength(2);
    expect(f.row().completed_at).toBe(NOW_SECONDS);
  });
});

it.each(['mysql', 'zdas'] as const)('plain-failure %s SQL fences both writes against the raw saved status', async dbType => {
  f.seed();
  const writes: Array<{ sql: string; params?: unknown[] }> = [];
  const db = {
    dbType, dialect: { ...f.db.dialect, driver: 'mysql2' as const },
    query: f.db.query.bind(f.db), transaction: f.db.transaction.bind(f.db), close: async () => {},
    exec: async (sql: string, params?: unknown[]) => { writes.push({ sql, params }); return { affectedRows: 0 }; },
  };
  expect(await new FlowRunRepository(db).updateCompletionOutcome('flow', completion('plain failure', { retryEpoch: 0 }))).toBe('rejected');
  expect(writes).toHaveLength(2);
  for (const { sql, params } of writes) {
    expect(sql).toContain('BINARY status = BINARY ?');
    expect(sql).not.toContain('COLLATE BINARY');
    expect(params?.slice(-3)).toEqual(['flow', 'running', 0]);
  }
});
