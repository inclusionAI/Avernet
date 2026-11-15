// @vitest-environment node
import { afterEach, beforeEach, expect, it } from 'vitest';
import { FlowRunRepository } from '../flow-run-repository.js';
import { fixture, group } from './fanout-state-fixture.js';

let f: ReturnType<typeof fixture>;
let repo: FlowRunRepository;
beforeEach(() => { f = fixture(); repo = new FlowRunRepository(f.db); });
afterEach(async () => { await f.db.close(); });

it('does not roll back or delete authoritative fanouts when an old mirror arrives', async () => {
  const durable = { ...group(), revision: 8 };
  f.raw.prepare('UPDATE flow_runs SET state_json = ?').run(JSON.stringify({ old: true, subworkflowFanouts: { fanout: durable } }));
  await repo.updateStateJson('parent', JSON.stringify({ new: true, subworkflowFanouts: { fanout: group() } }));
  const saved = JSON.parse(f.raw.prepare('SELECT state_json FROM flow_runs').get().state_json);
  expect(saved).toEqual({ new: true, subworkflowFanouts: { fanout: durable } });
});

it('does not accept a fanout created via an ordinary mirror', async () => {
  await repo.updateStateJson('parent', JSON.stringify({ nodeStates: {}, subworkflowFanouts: { fake: group() } }));
  const saved = JSON.parse(f.raw.prepare('SELECT state_json FROM flow_runs').get().state_json);
  expect(saved).toEqual({ nodeStates: {} });
});

it('propagates real mirror write errors instead of returning false', async () => {
  f.raw.exec(`CREATE TRIGGER refuse_update BEFORE UPDATE ON flow_runs BEGIN SELECT RAISE(ABORT, 'storage unavailable'); END`);
  await expect(repo.updateStateJson('parent', '{}')).rejects.toThrow('storage unavailable');
});

it('rejects forged managed-child identity in an ordinary mirror', async () => {
  await expect(repo.updateStateJson('parent', JSON.stringify({ _fanoutIdentity: { parentFlowId: 'injected' }, _fanoutRevision: 1 })))
    .rejects.toMatchObject({ statusCode: 409 });
});

it('retries a legacy mirror whose read raced with the first committed fanout', async () => {
  let release!: () => void;
  let observed!: () => void;
  const paused = new Promise<void>(resolve => { observed = resolve; });
  const resume = new Promise<void>(resolve => { release = resolve; });
  let first = true;
  const racingDb = new Proxy(f.db, {
    get(target, key) {
      if (key === 'query') return async (sql: string, params?: unknown[]) => {
        const rows = await target.query(sql, params);
        if (first && sql.includes('SELECT flow_id, workflow_id')) { first = false; observed(); await resume; }
        return rows;
      };
      const value = Reflect.get(target, key, target);
      return typeof value === 'function' ? value.bind(target) : value;
    },
  });
  const mirror = new FlowRunRepository(racingDb).updateStateJson('parent', JSON.stringify({ phase: 'after', subworkflowFanouts: {} }));
  await paused;
  try {
    await repo.fanoutState.saveGroup('parent', 'fanout', { group: group(), expectedRevision: null });
  } finally { release(); }
  expect(await mirror).toBe(true);
  const saved = JSON.parse(f.raw.prepare("SELECT state_json FROM flow_runs WHERE flow_id='parent'").get().state_json);
  expect(saved.phase).toBe('after');
  expect(saved.subworkflowFanouts.fanout).toEqual(group());
});

it('retries a fanout write whose read raced with a committed ordinary mirror', async () => {
  let release!: () => void;
  let observed!: () => void;
  const paused = new Promise<void>(resolve => { observed = resolve; });
  const resume = new Promise<void>(resolve => { release = resolve; });
  let first = true;
  const racingDb = new Proxy(f.db, {
    get(target, key) {
      if (key === 'query') return async (sql: string, params?: unknown[]) => {
        const rows = await target.query(sql, params);
        if (first && sql.includes('SELECT flow_id, workflow_id')) { first = false; observed(); await resume; }
        return rows;
      };
      const value = Reflect.get(target, key, target);
      return typeof value === 'function' ? value.bind(target) : value;
    },
  });
  const save = new FlowRunRepository(racingDb).fanoutState.saveGroup('parent', 'fanout', { group: group(), expectedRevision: null });
  await paused;
  try { await repo.updateStateJson('parent', JSON.stringify({ phase: 'new' })); }
  finally { release(); }
  await save;
  const saved = JSON.parse(f.raw.prepare("SELECT state_json FROM flow_runs WHERE flow_id='parent'").get().state_json);
  expect(saved.phase).toBe('new');
  expect(saved.subworkflowFanouts.fanout).toEqual(group());
});
