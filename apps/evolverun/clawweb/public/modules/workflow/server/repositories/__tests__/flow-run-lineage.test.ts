// @vitest-environment node
import { afterEach, describe, expect, it } from 'vitest';
import { normalizeFlowRunLineage } from '../../services/flow-run-lineage.js';
import { lineageFixture } from './flow-run-lineage.fixture.js';
import type { FlowRunInsert } from '../flow-run-repository.js';

const closes: Array<() => Promise<void>> = [];
afterEach(async () => { for (const close of closes.splice(0).reverse()) await close(); });
async function fixture(guarded = true) { const f = await lineageFixture(guarded); closes.push(f.close); return f; }
const base: FlowRunInsert = { flowId: 'run', workflowId: 'wf', status: 'running', startedAt: 1, originBotId: 'bot:owner' };
const cases = [
  { label: 'omitted', input: {}, expected: { parent_flow_id: null, root_flow_id: null } },
  { label: 'top-level null', input: { parentFlowId: null, rootFlowId: null }, expected: { parent_flow_id: null, root_flow_id: null } },
  { label: 'first child', input: { parentFlowId: 'top', rootFlowId: 'top' }, expected: { parent_flow_id: 'top', root_flow_id: 'top' } },
  { label: 'nested child', input: { parentFlowId: 'child', rootFlowId: 'top' }, expected: { parent_flow_id: 'child', root_flow_id: 'top' } },
  { label: 'parent known only', input: { parentFlowId: 'child' }, expected: { parent_flow_id: 'child', root_flow_id: null } },
  { label: 'root known only', input: { rootFlowId: 'top' }, expected: { parent_flow_id: null, root_flow_id: 'top' } },
  { label: 'exact Unicode', input: { parentFlowId: ' 父 / e\u0301 %2F ', rootFlowId: ' 根 / é ' }, expected: { parent_flow_id: ' 父 / e\u0301 %2F ', root_flow_id: ' 根 / é ' } },
  { label: 'Unicode replacement character and NUL', input: { parentFlowId: '\uFFFD', rootFlowId: 'a\u0000b' }, expected: { parent_flow_id: '\uFFFD', root_flow_id: 'a\u0000b' } },
  { label: '255 codepoints', input: { parentFlowId: '🚀'.repeat(255), rootFlowId: '根'.repeat(255) }, expected: { parent_flow_id: '🚀'.repeat(255), root_flow_id: '根'.repeat(255) } },
];
const invalidValues = ['', ' \n\t', 'a'.repeat(256), '🚀'.repeat(256), false, 0, 1, {}, []];

describe('creation-only engine lineage', () => {
  it.each(cases)('normalizes $label exactly', ({ input, expected }) => {
    expect(normalizeFlowRunLineage(input)).toEqual(expected);
  });
  it('persists every supported value and returns explicit nulls consistently in light/full/list reads', async () => {
    const f = await fixture();
    for (const [i, { input, expected }] of cases.entries()) {
      const flowId = `run-${i}`;
      expect(await f.repo.insert({ ...base, flowId, ...input })).toBe(true);
      expect(await f.repo.findByFlowId(flowId)).toMatchObject(expected);
      expect(await f.repo.findFullByFlowId(flowId)).toMatchObject(expected);
      expect(await f.repo.findRuns({ query: flowId })).toMatchObject([expected]);
      expect(f.raw.prepare('SELECT parent_flow_id, root_flow_id FROM flow_runs WHERE flow_id=?').get(flowId)).toEqual(expected);
    }
    for (const field of ['parentFlowId', 'rootFlowId']) for (const value of invalidValues) {
      expect(() => normalizeFlowRunLineage({ [field]: value })).toThrow(/nonblank string of at most 255/);
      expect(await f.repo.insert({ ...base, [field]: value } as FlowRunInsert)).toBe(false);
    }
    for (const field of ['parentFlowId', 'rootFlowId']) for (const value of ['\uD800', '\uDFFF', 'before\uD800after', '🚀\uDFFF']) {
      expect(() => normalizeFlowRunLineage({ [field]: value })).toThrow(/valid Unicode/);
      expect(await f.repo.insert({ ...base, [field]: value })).toBe(false);
    }
    expect(await f.repo.countRuns()).toBe(cases.length);
  });
  it('keeps historical unknowns null rather than interpreting params, state, association or snapshot fields', async () => {
    const f = await fixture();
    const json = JSON.stringify({ parent_flow_id: 'fake', root_flow_id: 'fake', parentFlowId: 'fake', rootFlowId: 'fake',
      subworkflowMeta: { parentFlowId: 'fake' }, run_request_parent_flow_id: 'fake' });
    f.raw.prepare('INSERT INTO flow_runs(flow_id,workflow_id,status,started_at,params_json,state_json,input_json) VALUES (?,?,?,?,?,?,?)')
      .run('historical', 'wf', 'succeeded', 1, json, json, json);
    expect(await f.request('/api/internal/runs/historical')).toMatchObject({ status: 200, body: { data: { parent_flow_id: null, root_flow_id: null } } });
    for (const row of [await f.repo.findByFlowId('historical'), await f.repo.findFullByFlowId('historical'), ...(await f.repo.findRuns())]) {
      expect(row).toMatchObject({ parent_flow_id: null, root_flow_id: null });
    }
  });
  it.each([{}, { parentFlowId: 'parent', rootFlowId: 'root' }])('never rewrites lineage in lifecycle, retry or recovery operations (%j)', async lineage => {
    const f = await fixture(); expect(await f.repo.insert({ ...base, ...lineage })).toBe(true);
    const expected = normalizeFlowRunLineage(lineage);
    const check = async (result: unknown, accepted = true) => { if (accepted) expect(result).toBeTruthy(); else expect(result).toBe(false); expect(await f.repo.findFullByFlowId('run')).toMatchObject(expected); };
    await check(await f.repo.incrementNodeCount('run', 'failed_count'));
    await check(await f.repo.updateCurrentPhase('run', 'P2'));
    await check(await f.repo.updateNodeCount('run', 8));
    await check(await f.repo.updateResultJson('run', 'node', { parent_flow_id: 'forged', root_flow_id: 'forged' }), false);
    await check(await f.repo.updateStateJson('run', JSON.stringify({ effectiveFlowTimeoutMinutes: 1, workflowId: 'wf', subworkflowMeta: { parentFlowId: 'forged' }, rootFlowId: 'forged' })));
    await check(await f.repo.updateSessionInfo('run', { originSessionKey: 'new-session', originSessionId: 'new-id' }));
    await check(await f.repo.updateAnalysisStatus('run', 'completed'));
    await check(await f.repo.updateCompletion('run', { status: 'failed', completedAt: 9, inputJson: '{"parent_flow_id":"forged"}', succeededCount: 1, failedCount: 1 }));
    expect(await f.repo.resetFailedForRetry('run', async () => 0)).toEqual({ status: 'conflict' });
    expect(await f.repo.findFullByFlowId('run')).toMatchObject(expected);
    await check(await f.repo.resetStartedAt('run', 10), false);
    expect(await f.repo.updateStatus('run', 'failed')).toBe(false);
    // Deprecated start mutation did not clear the accepted timestamp; exact settlement remains a read-only duplicate.
    expect(await f.repo.updateCompletion('run', { status: 'failed', completedAt: 11, succeededCount: 1, failedCount: 1 })).toBe(true);
    expect(await f.repo.findFullByFlowId('run')).toMatchObject(expected);
    await check(await f.repo.resumeForRetry('run', 1));
    await check(await f.repo.markFailedIfRunning('run', { reason: JSON.stringify({ reason: 'flow_timeout', timeoutSecs: 60, ranForSecs: 61 }), currentPhase: 'failed', completedAt: 11, retryEpoch: 1 }));
    await check(await f.repo.resumeForRetry('run', 2));
    await check(await f.repo.abort('run', undefined, 2));
  });
  it.each([false, true])('does not expose lineage in actual public list/detail (guarded: %s) or scoped summaries', async guarded => {
    const f = await fixture(guarded);
    expect(await f.repo.insert({ ...base, parentFlowId: 'secret-ancestor', rootFlowId: 'secret-root' })).toBe(true);
    const listed = await f.request('/api/runs'); expect(listed.status).toBe(200);
    const detail = await f.request('/api/runs/run'); expect(detail.status).toBe(200);
    for (const row of [listed.body.runs[0], detail.body.run, ...(await f.scoped.listRuns({ botId: 'bot', ownerId: 'owner' })).items]) {
      expect(row).not.toHaveProperty('parent_flow_id'); expect(row).not.toHaveProperty('root_flow_id');
      expect(JSON.stringify(row)).not.toContain('secret-ancestor'); expect(JSON.stringify(row)).not.toContain('secret-root');
    }
  });
});
