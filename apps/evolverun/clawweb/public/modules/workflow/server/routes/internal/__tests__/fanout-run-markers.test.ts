// @vitest-environment node
import express from 'express';
import { createHash } from 'node:crypto';
import type { Server } from 'node:http';
import { afterEach, describe, expect, it } from 'vitest';
import { childInput, fixture, group } from '../../../repositories/__tests__/fanout-state-fixture.js';
import { FlowRunRepository } from '../../../repositories/flow-run-repository.js';
import { createInternalRunsRouter } from '../runs.js';

const cleanups: Array<() => Promise<void>> = [];
afterEach(async () => { for (const close of cleanups.splice(0).reverse()) await close(); });
const cases = [
  { label: 'custom', state: { env: ' test ', tag: '回归 / v1' }, expected: { env: ' test ', tag: '回归 / v1' } },
  { label: 'null', state: { env: null, tag: null }, expected: { env: null, tag: null } },
  { label: 'omitted', state: {}, expected: { env: 'prod', tag: '' } },
  { label: 'null env', state: { env: null }, expected: { env: null, tag: '' } },
  { label: 'null tag', state: { tag: null }, expected: { env: 'prod', tag: null } },
];

async function setup() {
  const f = fixture(); const repo = new FlowRunRepository(f.db); const g = group();
  // The parent labels must never supply the child's missing labels.
  f.raw.exec("UPDATE flow_runs SET env='parent-only', tag='parent-label' WHERE flow_id='parent'");
  await repo.fanoutState.saveGroup('parent', 'fanout', { group: g, expectedRevision: null });
  const app = express(); app.use(express.json()); app.use('/runs', createInternalRunsRouter(repo));
  const server = await new Promise<Server>(resolve => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
  cleanups.push(async () => { await new Promise<void>(resolve => { server.closeAllConnections(); server.close(() => resolve()); }); await f.db.close(); });
  const url = `http://127.0.0.1:${(server.address() as { port: number }).port}/runs/parent/fanout-children/child-fanout`;
  const write = async (body: unknown, suffix = '') => {
    const response = await fetch(url + suffix, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    return { status: response.status, body: await response.json() };
  };
  const stored = () => f.raw.prepare('SELECT env, tag, status, state_json FROM flow_runs WHERE flow_id=?').get('child-fanout');
  return { ...f, write, stored, input: childInput(g) };
}

// Pin the pre-existing start identity definition; defaulting SQL columns must not alter it.
const digest = (value: unknown) => createHash('sha256').update(JSON.stringify(value, (_key, item) =>
  item !== null && typeof item === 'object' && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item)).digest('hex');

describe.each([false, true])('legacy managed child state markers (JSON string: %s)', encoded => {
  it.each(cases)('round-trips $label and preserves launch state/digest on replay', async ({ state, expected }) => {
    const f = await setup();
    const initial = { ...f.input.stateJson, ...state };
    const body = { ...f.input, stateJson: encoded ? JSON.stringify(initial) : initial };
    const created = await f.write(body);
    expect(created).toMatchObject({ status: 201, body: { data: { created: true } } });
    expect(f.stored()).toMatchObject(expected);
    const persisted = JSON.parse(created.body.data.run.stateJson);
    const { originCredentials: _credentials, ...expectedState } = initial;
    const launch = { ...expectedState, _fanoutStarted: false };
    const { _fanoutIdentity, _fanoutRevision, ...storedLaunch } = persisted;
    expect(storedLaunch).toEqual(launch); // In particular, omission never injects defaults into the state.
    expect(_fanoutIdentity.startDigest).toBe(digest(launch));
    expect(_fanoutRevision).toBe(1);
    expect(f.stored()).toMatchObject({ state_json: created.body.data.run.stateJson });

    const advanced = { ...persisted, currentPhase: 'done' };
    expect(await f.write({ expectedRevision: 1, stateJson: advanced, status: 'succeeded' }, '/state'))
      .toMatchObject({ status: 200 });
    const before = f.stored();
    expect(await f.write(body)).toMatchObject({ status: 200, body: { data: { created: false, run: { revision: 2, status: 'succeeded' } } } });
    expect(f.stored()).toEqual(before);
    expect(JSON.parse((before as { state_json: string }).state_json)._fanoutIdentity).toEqual(_fanoutIdentity);

    // The existing full-launch digest already covers state fields. Do not redefine it for labels.
    const changed = { ...initial, env: 'different', tag: 'different' };
    expect((await f.write({ ...body, stateJson: encoded ? JSON.stringify(changed) : changed })).status).toBe(409);
    expect(f.stored()).toEqual(before);
  });
});

it('does not overwrite previously stored labels when replaying a legacy state without markers', async () => {
  const f = await setup();
  expect((await f.write(f.input)).status).toBe(201);
  f.raw.exec("UPDATE flow_runs SET env=NULL, tag='existing-label' WHERE flow_id='child-fanout'");
  const before = f.stored();
  expect((await f.write(f.input)).status).toBe(200);
  expect(f.stored()).toEqual(before);
});

it.each([{ env: '' }, { env: ' \t' }, { env: 'x'.repeat(33) }, { env: 12 }, { tag: 'x'.repeat(256) }, { tag: false }])(
  'rejects invalid state labels before any child or link write: %j', async markers => {
    const f = await setup();
    expect(await f.write({ ...f.input, stateJson: { ...f.input.stateJson, ...markers } }))
      .toMatchObject({ status: 400, body: { success: false } });
    expect(f.stored()).toBeUndefined();
    expect(f.raw.prepare('SELECT * FROM flow_run_links').all()).toEqual([]);
  });
