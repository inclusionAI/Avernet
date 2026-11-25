// @vitest-environment node
import { afterEach, describe, expect, it } from 'vitest';
import { lineageFixture } from '../../../repositories/__tests__/flow-run-lineage.fixture.js';

const closes: Array<() => Promise<void>> = [];
afterEach(async () => { for (const close of closes.splice(0).reverse()) await close(); });
async function fixture() { const f = await lineageFixture(); closes.push(f.close); return f; }
const path = '/api/internal/runs';
const run = { flow_id: 'run', workflow_id: 'wf', status: 'running', started_at: 1 };
const cases = [
  { input: {}, expected: { parent_flow_id: null, root_flow_id: null } },
  { input: { parent_flow_id: null, root_flow_id: null }, expected: { parent_flow_id: null, root_flow_id: null } },
  { input: { parent_flow_id: 'top', root_flow_id: 'top' }, expected: { parent_flow_id: 'top', root_flow_id: 'top' } },
  { input: { parent_flow_id: 'child', root_flow_id: 'top' }, expected: { parent_flow_id: 'child', root_flow_id: 'top' } },
  { input: { parent_flow_id: 'child' }, expected: { parent_flow_id: 'child', root_flow_id: null } },
  { input: { parent_flow_id: ' 父 / e\u0301 ', root_flow_id: '🚀'.repeat(255) }, expected: { parent_flow_id: ' 父 / e\u0301 ', root_flow_id: '🚀'.repeat(255) } },
];

describe('trusted engine run lineage wire contract', () => {
  it('returns stored explicit nulls/IDs on POST, internal detail and internal list, without internal markers', async () => {
    const f = await fixture();
    for (const [i, { input, expected }] of cases.entries()) {
      const flow_id = `run-${i}`;
      const response = await f.request(path, { ...run, flow_id, ...input });
      expect(response).toMatchObject({ status: 201, body: { success: true, data: { flow_id, ...expected } } });
      f.raw.prepare('UPDATE flow_runs SET ordinary_run_marker=? WHERE flow_id=?').run('a'.repeat(64), flow_id);
      const detail = await f.request(`${path}/${flow_id}`);
      expect(detail).toMatchObject({ status: 200, body: { data: { ...expected } } });
      expect(detail.body.data).not.toHaveProperty('ordinary_run_marker');
    }
    const list = await f.request(path); expect(list.status).toBe(200); expect(list.body.data).toHaveLength(cases.length);
    for (const [i, { expected }] of cases.entries()) {
      const item = list.body.data.find((r: { flow_id: string }) => r.flow_id === `run-${i}`);
      expect(item).toMatchObject(expected); expect(item).not.toHaveProperty('ordinary_run_marker');
    }
  });
  it('rejects malformed non-null IDs before persistence with a clear field-specific 400', async () => {
    const f = await fixture();
    for (const field of ['parent_flow_id', 'root_flow_id']) for (const value of ['', ' \n\t', 'x'.repeat(256), '🚀'.repeat(256), 1, false, {}, []]) {
      expect(await f.request(path, { ...run, [field]: value })).toMatchObject({ status: 400,
        body: { success: false, error: 'Bad Request', message: `${field} must be null or a nonblank string of at most 255 characters` } });
    }
    for (const field of ['parent_flow_id', 'root_flow_id']) for (const value of ['\uD800', '\uDFFF', 'before\uD800after', '🚀\uDFFF']) {
      expect(await f.request(path, { ...run, [field]: value })).toMatchObject({ status: 400,
        body: { success: false, error: 'Bad Request', message: `${field} must be valid Unicode (no unpaired surrogates)` } });
    }
    expect(await f.repo.countRuns()).toBe(0);
  });
  it('ignores JSON and alternate-name claims rather than inferring authoritative lineage', async () => {
    const f = await fixture();
    const state = { subworkflowMeta: { parentFlowId: 'forged' }, parent_flow_id: 'forged', rootFlowId: 'forged' };
    expect(await f.request(path, { ...run, params_json: JSON.stringify(state), state_json: JSON.stringify(state), input_json: JSON.stringify(state),
      parentFlowId: 'wrong-casing', rootFlowId: 'wrong-casing' })).toMatchObject({ status: 201, body: { data: { parent_flow_id: null, root_flow_id: null } } });
  });
  it('does not allow PUT bodies or recovery state to overwrite creation lineage', async () => {
    const f = await fixture();
    const expected = { parent_flow_id: 'original-parent', root_flow_id: 'original-root' };
    expect((await f.request(path, { ...run, ...expected })).status).toBe(201);
    const forged = { parent_flow_id: 'overwrite', root_flow_id: null };
    for (const [suffix, body] of [
      ['/phase', { phase: 'P2' }], ['/node-count', { node_count: 3 }],
      ['/state', { state_json: '{"parentFlowId":"overwrite","rootFlowId":"overwrite"}' }],
      ['/completion', { status: 'failed', completed_at: 10, succeeded_count: 0, failed_count: 1 }],
      ['/retry-failed', {}], ['/started-at', { started_at: 11 }], ['/status', { status: 'running' }],
    ] as const) {
      expect((await f.request(`${path}/run${suffix}`, { ...body, ...forged }, 'PUT')).status).toBe(200);
      expect(await f.repo.findFullByFlowId('run')).toMatchObject(expected);
    }
  });
});
