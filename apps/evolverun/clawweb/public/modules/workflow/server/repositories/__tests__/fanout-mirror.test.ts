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
