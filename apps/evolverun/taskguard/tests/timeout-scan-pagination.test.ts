import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { test, type TestContext } from 'node:test';
import { ApiClient } from '../src/db/api-client.js';
import { FlowRunApiRepository } from '../src/db/api-repositories/flow-run-api-repository.js';
import { FlowRunRepository } from '../src/db/repositories/flow-run-repository.js';
import type { FlowRunReapFields } from '../src/db/repositories/types.js';
import { SqliteDatabase } from '../src/db/sqlite-database.js';
import { withFlowScan } from '../src/recovery/scan.js';
import { reapTimedOutRuns } from '../src/recovery/timeout-sweep.js';

async function fixture(t: TestContext) {
  const db = new SqliteDatabase({ path: ':memory:' });
  await db.connect();
  t.after(() => db.close());
  await db.exec(`CREATE TABLE flow_runs (
    id INTEGER PRIMARY KEY, flow_id TEXT UNIQUE, workflow_id TEXT, status TEXT,
    retry_epoch INTEGER NOT NULL DEFAULT 0, state_json TEXT, started_at INTEGER,
    current_phase TEXT, result_json TEXT, total_duration_ms INTEGER,
    completed_at INTEGER, gmt_modified INTEGER
  )`);
  const runs = new FlowRunRepository(db);
  const pages: { offset: number; limit: number; ids: string[] }[] = [];
  const loaded: string[] = [];
  const attempts: { id: string; fields: FlowRunReapFields }[] = [];
  const claimed: string[] = [];
  const list = runs.findStaleRunning.bind(runs);
  const detail = runs.findByFlowId.bind(runs);
  const claim = runs.markFailedIfRunning.bind(runs);
  runs.findStaleRunning = async (cutoff, limit = 100, offset = 0) => {
    const rows = await list(cutoff, limit, offset);
    pages.push({ offset, limit, ids: rows.map(row => row.flow_id) });
    // Discovery must not substitute for the full detail/state snapshot.
    return rows.map(row => ({ ...row, state_json: undefined }));
  };
  runs.findByFlowId = async id => { loaded.push(id); return detail(id); };
  runs.markFailedIfRunning = async (id, fields) => {
    attempts.push({ id, fields });
    return claim(id, fields);
  };
  async function seed(id: string, minutes: unknown, startedAt = Math.floor(Date.now() / 1000) - 121 * 60, status = 'running') {
    await db.exec(`INSERT INTO flow_runs
      (flow_id, workflow_id, status, retry_epoch, state_json, started_at, current_phase)
      VALUES (?, 'wf', ?, 7, ?, ?, 'P2')`, [id, status, JSON.stringify({
      workflowId: 'wf', retryEpoch: 7, effectiveFlowTimeoutMinutes: minutes,
    }), startedAt]);
  }
  function sweep(limit = 1, globalTimeoutMs = 120 * 60_000) {
    return reapTimedOutRuns({
      repository: runs, limit, globalTimeoutMs,
      onClaim: async (snapshot, fields) => {
        assert.equal(snapshot.retryEpoch, 7);
        assert.equal(fields.retryEpoch, snapshot.retryEpoch);
        assert.equal(snapshot.state?.effectiveFlowTimeoutMinutes, 120);
        claimed.push(snapshot.row.flow_id);
      },
    });
  }
  return { db, runs, pages, loaded, attempts, claimed, seed, sweep };
}

for (const globalTimeoutMs of [120 * 60_000, 0]) {
  test(`limit-one sweep passes frozen zero/long/corrupt rows to claim a later expiry (global ${globalTimeoutMs})`, async t => {
    const f = await fixture(t);
    await f.seed('a-disabled', 0);
    await f.seed('b-long', 8200);
    await f.seed('c-corrupt', 'invalid');
    await f.seed('d-expired', 120);
    const results: number[] = [];
    for (let i = 0; i < 4; i++) results.push(await f.sweep(1, globalTimeoutMs));
    assert.deepEqual(results, [0, 0, 0, 1]);
    assert.deepEqual(f.pages.map(page => page.offset), [0, 1, 2, 3]);
    assert.ok(f.pages.every(page => page.limit === 1 && page.ids.length === 1));
    assert.deepEqual(f.loaded, ['a-disabled', 'b-long', 'c-corrupt', 'd-expired']);
    assert.deepEqual(f.attempts.map(attempt => [attempt.id, attempt.fields.retryEpoch]), [['d-expired', 7]]);
    assert.deepEqual(f.claimed, ['d-expired']);
    for (const id of ['a-disabled', 'b-long', 'c-corrupt']) {
      assert.equal((await f.runs.findByFlowId(id))?.status, 'running');
    }
    const expired = await f.runs.findByFlowId('d-expired');
    assert.equal(expired?.status, 'failed');
    assert.equal(JSON.parse(expired!.result_json!).timeoutSource, 'run-state');
    assert.equal(await f.sweep(1, globalTimeoutMs), 0);
    assert.equal(await f.sweep(1, globalTimeoutMs), 0);
    assert.deepEqual(f.pages.slice(-2), [
      { offset: 4, limit: 1, ids: [] },
      { offset: 0, limit: 1, ids: ['a-disabled'] },
    ]);
  });
}

test('removals can defer a shifted expiry one rotation but cannot starve it', async t => {
  const f = await fixture(t);
  await f.seed('a-disabled', 0);
  for (const id of ['b-expired', 'c-expired', 'd-expired']) await f.seed(id, 120);
  const results: number[] = [];
  for (let i = 0; i < 6; i++) results.push(await f.sweep());
  assert.deepEqual(results, [0, 1, 1, 0, 0, 1]);
  assert.deepEqual(f.pages.map(page => page.offset), [0, 1, 2, 3, 0, 1]);
  assert.deepEqual(f.loaded, ['a-disabled', 'b-expired', 'd-expired', 'a-disabled', 'c-expired']);
  assert.deepEqual(f.claimed, ['b-expired', 'd-expired', 'c-expired']);
  assert.ok(f.pages.every(page => page.limit === 1 && page.ids.length <= 1));
});

test('short pages wrap without fetching another page in the same bounded sweep', async t => {
  const f = await fixture(t);
  await f.seed('a-disabled', 0);
  await f.seed('b-long', 8200);
  await f.seed('c-disabled', 0);
  for (let i = 0; i < 3; i++) assert.equal(await f.sweep(2), 0);
  assert.deepEqual(f.pages, [
    { offset: 0, limit: 2, ids: ['a-disabled', 'b-long'] },
    { offset: 2, limit: 2, ids: ['c-disabled'] },
    { offset: 0, limit: 2, ids: ['a-disabled', 'b-long'] },
  ]);
  assert.equal(f.loaded.length, 5);
  assert.equal(f.attempts.length, 0);
});

test('rotation cursors are independent for each repository instance', async t => {
  const first = await fixture(t);
  const second = await fixture(t);
  for (const f of [first, second]) {
    await f.seed('a-disabled', 0);
    await f.seed('b-long', 8200);
  }
  await first.sweep();
  await first.sweep();
  await second.sweep();
  await first.sweep();
  await second.sweep();
  assert.deepEqual(first.pages.map(page => page.offset), [0, 1, 2]);
  assert.deepEqual(second.pages.map(page => page.offset), [0, 1]);
});

test('shared scan gate skips a busy sweep without advancing its cursor', async t => {
  const f = await fixture(t);
  await f.seed('a-disabled', 0);
  await f.seed('b-long', 8200);
  await withFlowScan(async () => {
    assert.equal(await f.sweep(), 0);
    assert.equal(f.pages.length, 0);
    return 0;
  }, 0);
  await f.sweep();
  await f.sweep();
  assert.deepEqual(f.pages.map(page => page.offset), [0, 1]);
});

test('a failed page fetch releases the gate and retains the current offset for retry', async t => {
  const f = await fixture(t);
  await f.seed('a-disabled', 0);
  await f.seed('b-long', 8200);
  await f.sweep();
  const list = f.runs.findStaleRunning.bind(f.runs);
  f.runs.findStaleRunning = async (_cutoff, _limit, offset) => {
    assert.equal(offset, 1);
    throw new Error('page fetch failed');
  };
  await assert.rejects(f.sweep(), /page fetch failed/);
  f.runs.findStaleRunning = list;
  await f.sweep();
  assert.deepEqual(f.pages.map(page => page.offset), [0, 1]);
});

for (const race of ['terminal', 'new-epoch']) {
  test(`a later page still uses running-only captured-epoch CAS after a ${race} race`, async t => {
    const f = await fixture(t);
    await f.seed('a-disabled', 0);
    await f.seed('b-expired', 120);
    await f.sweep();
    const claim = f.runs.markFailedIfRunning.bind(f.runs);
    f.runs.markFailedIfRunning = async (id, fields) => {
      await f.db.exec(race === 'terminal'
        ? "UPDATE flow_runs SET status = 'succeeded' WHERE flow_id = ?"
        : 'UPDATE flow_runs SET retry_epoch = 8 WHERE flow_id = ?', [id]);
      return claim(id, fields);
    };
    assert.equal(await f.sweep(), 0);
    assert.deepEqual(f.pages.map(page => page.offset), [0, 1]);
    assert.deepEqual(f.attempts.map(attempt => [attempt.id, attempt.fields.retryEpoch]), [['b-expired', 7]]);
    assert.deepEqual(f.claimed, []);
    const row = await f.runs.findByFlowId('b-expired');
    assert.equal(row?.status, race === 'terminal' ? 'succeeded' : 'running');
    assert.equal(row?.retry_epoch, race === 'terminal' ? 7 : 8);
  });
}

test('SQLite stale lookup binds offset and orders ties by flow_id after started_at', async t => {
  const f = await fixture(t);
  // Insert ties out of order so insertion order cannot masquerade as stable pagination.
  for (const id of ['c', 'a', 'b']) await f.seed(id, 0, 100);
  await f.seed('z-older', 0, 99);
  await f.seed('waiting', 0, 98, 'waiting');
  await f.seed('at-cutoff', 0, 101);
  const ids = async (limit?: number, offset?: number) =>
    (await f.runs.findStaleRunning(101, limit, offset)).map(row => row.flow_id);
  assert.deepEqual(await ids(2), ['z-older', 'a']);
  assert.deepEqual(await ids(2, 2), ['b', 'c']);
  assert.deepEqual(await ids(2, 4), []);
  assert.deepEqual(await ids(), ['z-older', 'a', 'b', 'c']);
});

test('HTTP stale lookup sends bounded limit and offset through the actual ApiClient', async t => {
  const requests: { method: string; path: string; query: Record<string, string> }[] = [];
  const rows = ['a', 'b', 'c'].map(id => ({
    flow_id: id, workflow_id: 'wf', status: 'running', started_at: 1,
    retry_epoch: 7, state_json: '{"retryEpoch":7}',
  }));
  const server = createServer((req, res) => {
    const url = new URL(req.url!, 'http://127.0.0.1');
    requests.push({ method: req.method!, path: url.pathname, query: Object.fromEntries(url.searchParams) });
    const offset = Number(url.searchParams.get('offset') ?? 0);
    const limit = Number(url.searchParams.get('limit'));
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ success: true, data: rows.slice(offset, offset + limit), total: rows.length }));
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve())));
  const { port } = server.address() as { port: number };
  const runs = new FlowRunApiRepository(new ApiClient({ baseUrl: `http://127.0.0.1:${port}`, maxRetries: 1 }));
  const first = await runs.findStaleRunning(10, 1);
  const next = await runs.findStaleRunning(10, 1, 1);
  const last = await runs.findStaleRunning(10, 1, 2);
  const empty = await runs.findStaleRunning(10, 1, 3);
  assert.deepEqual([first, next, last, empty].map(page => page.map(row => row.flow_id)), [['a'], ['b'], ['c'], []]);
  assert.equal(next[0].retry_epoch, 7);
  assert.equal(next[0].state_json, '{"retryEpoch":7}');
  assert.deepEqual(requests, [0, 1, 2, 3].map(offset => ({
    method: 'GET', path: '/api/internal/runs', query: { status: 'running', limit: '1', offset: String(offset) },
  })));
});
