import { describe, expect, it, vi } from 'vitest';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { createRepairSourcePort, type RepairSourceReaders } from '../repair-source-adapter.js';

const db = {} as IDatabase;
const proposal = (summary = '修复输出类型') => ({ schemaVersion: 'workflow-patch/v1', workflowId: 'wf', summary, operations: [{ op: 'replace', nodeId: 'llm', path: '/outputContract/type', value: 'number' }] });
const diagnosis = (id = 'd1', value = proposal()) => ({ diagnosisId: id, flowId: `run-${id}`, analysisId: `an-${id}`, sourceId: `src-${id}`, completedAtMs: 1,
  failureSignature: 'output-contract|llm', failureMode: 'output-contract', nodeId: 'llm', reasoning: `原因 ${id}`, evidenceEventIds: [`ev-${id}`], proposal: value });
const readers = (overrides: Partial<RepairSourceReaders> = {}): RepairSourceReaders => ({
  groups: async () => [{ workflowId: 'wf', signature: 'output-contract|llm', inputDigest: 'a'.repeat(64), sources: [diagnosis()] }],
  suggestions: async () => [],
  evidence: async (_db, _workflow, ids) => ids.map(event_id => ({ event_id, workflow_id: 'wf', flow_id: `run-${event_id.slice(3)}`, node_id: 'llm', event_type: 'node.failed', payload_digest: 'b'.repeat(64), payload_json: '{"error":"expected number"}', occurred_at_ms: 1 })),
  ...overrides,
});

describe('trusted workflow repair source adapter', () => {
  it('bounds source reads to the active window unless history is requested', async () => {
    const groups = vi.fn(readers().groups);
    const suggestions = vi.fn(readers().suggestions);
    const source = createRepairSourcePort(readers({ groups, suggestions }));
    await source.load(db, 'wf', 'summary');
    expect(groups).toHaveBeenLastCalledWith(db, 'wf', { sinceMs: expect.any(Number) });
    expect(suggestions).toHaveBeenLastCalledWith(db, 'wf', { sinceMs: expect.any(Number) });
    await source.load(db, 'wf', 'summary', { includeHistorical: true });
    expect(groups).toHaveBeenLastCalledWith(db, 'wf', { sinceMs: undefined });
    expect(suggestions).toHaveBeenLastCalledWith(db, 'wf', { sinceMs: undefined });
  });
  it('builds a lightweight inbox without reading evidence payloads', async () => {
    const evidence = vi.fn(readers().evidence);
    const result = await createRepairSourcePort(readers({ evidence })).load(db, 'wf', 'summary');
    expect(evidence).not.toHaveBeenCalled();
    expect(result[0].item.context).toMatchObject({
      diagnoses: [{ diagnosisId: 'd1', evidenceEventIds: ['ev-d1'], evidenceCount: 1 }],
    });
    expect(JSON.stringify(result)).not.toContain('expected number');
  });
  it('freezes each problem, independent proposal and cited evidence, not an aggregate summary', async () => {
    const result = await createRepairSourcePort(readers()).load(db, 'wf');
    expect(result).toHaveLength(1);
    expect(result[0].item.context).toMatchObject({ diagnoses: [{ reasoning: '原因 d1', evidence: [{ payload: { error: 'expected number' } }] }] });
    expect(result[0].item.sources[0]).toMatchObject({ kind: 'diagnosis_candidate', diagnosisId: 'd1', flowId: 'run-d1' });
    expect(result[0].item.proposal).toEqual(proposal());
  });
  it('hydrates evidence only for the requested repair item', async () => {
    const distinct = { ...proposal('修复脚本兼容性'), operations: [{ op: 'replace', nodeId: 'llm', path: '/outputContract/type', value: 'string' }] };
    const groups = async () => [{ workflowId: 'wf', signature: 'output-contract|llm', inputDigest: 'a'.repeat(64),
      sources: [diagnosis('d1'), diagnosis('d2', distinct)] }];
    const summary = await createRepairSourcePort(readers({ groups })).load(db, 'wf', 'summary');
    const selected = summary.find(row => row.item.instruction === '修复输出类型')!;
    const evidence = vi.fn(async (_db: IDatabase, _workflow: string, eventIds: string[]) => eventIds.map(event_id => ({
      event_id, workflow_id: 'wf', flow_id: `run-${event_id.slice(3)}`, node_id: 'llm', event_type: 'node.failed',
      payload_json: event_id === 'ev-d2' ? '{bad' : '{"error":"expected number"}',
    })));
    const result = await createRepairSourcePort(readers({ groups, evidence })).load(db, 'wf', 'full', { itemIds: [selected.item.itemId] });
    expect(evidence).toHaveBeenCalledTimes(1);
    expect(evidence).toHaveBeenCalledWith(db, 'wf', ['ev-d1']);
    expect(result.find(row => row.item.itemId === selected.item.itemId)?.item.context).toMatchObject({
      diagnoses: [{ evidence: [{ payload: { error: 'expected number' } }] }],
    });
    expect(result.find(row => row.item.itemId !== selected.item.itemId)?.item.context).toMatchObject({
      diagnoses: [{ evidenceEventIds: ['ev-d2'], evidenceCount: 1 }],
    });
  });
  it('merges proposals with identical patch operations despite different summaries', async () => {
    const source = createRepairSourcePort(readers({ groups: async () => [{ workflowId: 'wf', signature: 'output-contract|llm', inputDigest: 'a'.repeat(64), sources: [diagnosis(), diagnosis('d2'), diagnosis('d3', proposal('修复脚本兼容性'))] }] }));
    const result = await source.load(db, 'wf');
    expect(result).toHaveLength(1);
    expect(result[0].item.sources).toHaveLength(3);
  });
  it('preserves proposals that target the same prompt with different patch values as variants', async () => {
    const changed = { ...proposal('允许工具调用'), operations: [{ op: 'replace', nodeId: 'llm', path: '/executor/prompt', value: '允许工具调用' }] };
    const original = { ...proposal('禁止工具调用'), operations: [{ op: 'replace', nodeId: 'llm', path: '/executor/prompt', value: '禁止工具调用' }] };
    const result = await createRepairSourcePort(readers({ groups: async () => [{ workflowId: 'wf', signature: 'output-contract|llm', inputDigest: 'a'.repeat(64),
      sources: [diagnosis('d1', original), diagnosis('d2', changed)] }] })).load(db, 'wf');
    expect(result).toHaveLength(2);
  });
  it('new evidence preserves an unchanged item identity but changes its frozen source snapshot', async () => {
    const first = (await createRepairSourcePort(readers()).load(db, 'wf'))[0];
    const second = (await createRepairSourcePort(readers({ groups: async () => [{ workflowId: 'wf', signature: 'output-contract|llm', inputDigest: 'c'.repeat(64), sources: [diagnosis(), diagnosis('d2')] }] })).load(db, 'wf'))[0];
    expect(second.item.itemId).toBe(first.item.itemId);
    expect(second.item.proposalKey).toBe(first.item.proposalKey);
    expect(second.item.context).not.toEqual(first.item.context);
  });
  it('keeps suggestion-only lifecycle entries and does not present old applied suggestions as new work', async () => {
    const result = await createRepairSourcePort(readers({ groups: async () => [], suggestions: async () => [{ id: 42, workflow_id: 'wf', failure_signature: 'timeout', fix_spec: '增加超时', proposal_json: null, status: 'applied_unverified', source_diagnosis_ids: '["d"]', impact_run_ids: '["run"]' }] })).load(db, 'wf');
    expect(result[0].initialState).toBe('awaiting_verification');
    expect(result[0].item.sources[0]).toMatchObject({ kind: 'suggestion', suggestionId: '42' });
    expect(result[0].item.context).toMatchObject({ suggestions: [{ status: 'applied_unverified', diagnosisIds: ['d'], runIds: ['run'] }] });
  });
  it('hides old pending suggestions by default but keeps them available as history', async () => {
    const suggestions = async () => [{ id: 42, workflow_id: 'wf', failure_signature: 'timeout', fix_spec: '增加超时',
      proposal_json: null, status: 'pending', gmt_modified: 1 }];
    expect(await createRepairSourcePort(readers({ groups: async () => [], suggestions })).load(db, 'wf', 'summary')).toEqual([]);
    expect(await createRepairSourcePort(readers({ groups: async () => [], suggestions })).load(db, 'wf', 'summary', { includeHistorical: true })).toHaveLength(1);
  });
  it.each(['ignored', 'resolved', 'unknown-future-state'])('does not requeue legacy %s suggestions', async status => {
    const result = await createRepairSourcePort(readers({ groups: async () => [], suggestions: async () => [{ id: 42, workflow_id: 'wf', failure_signature: 'timeout', fix_spec: '增加超时', status }] })).load(db, 'wf');
    expect(result[0].initialState).toBe('no_action');
    expect(result[0].item.context).toMatchObject({ suggestions: [{ status }] });
  });
  it('keeps merged legacy processing state independent of source order', async () => {
    const rows = ['applying', 'verified'].map((status, id) => ({ id, workflow_id: 'wf', failure_signature: 'output-contract|llm', fix_spec: '修复输出类型', proposal_json: JSON.stringify(proposal()), status }));
    const load = (suggestions: typeof rows) => createRepairSourcePort(readers({ suggestions: async () => suggestions })).load(db, 'wf');
    const first = await load(rows);
    expect(first).toEqual(await load([...rows].reverse()));
    expect(first[0].initialState).toBe('processing');
  });
  it('does not silently truncate 39 independent candidates to the old 20-item limit', async () => {
    const sources = Array.from({ length: 39 }, (_, index) => diagnosis(`d${index}`, { ...proposal(`修复 ${index}`),
      operations: [{ op: 'replace', nodeId: 'llm', path: '/outputContract/type', value: `type-${index}` }] }));
    expect(await createRepairSourcePort(readers({ groups: async () => [{ workflowId: 'wf', signature: 'output-contract|llm', inputDigest: 'a'.repeat(64), sources }] })).load(db, 'wf')).toHaveLength(39);
  });
  it('includes the referenced problem and evidence for a text-only suggestion without inventing missing diagnoses', async () => {
    const result = await createRepairSourcePort(readers({ suggestions: async () => [{ id: 7, workflow_id: 'wf', failure_signature: 'output-contract|llm', status: 'pending', fix_spec: '兼容旧数据', source_diagnosis_ids: '["d1","gone"]', impact_run_ids: '["run-d1"]' }] })).load(db, 'wf');
    const item = result.find(row => row.item.instruction === '兼容旧数据')!.item;
    expect(item.context).toMatchObject({ diagnoses: [{ diagnosisId: 'd1', reasoning: '原因 d1', evidence: [{ payload: { error: 'expected number' } }] }], suggestions: [{ missingDiagnosisIds: ['gone'] }] });
    expect(item.sources).toHaveLength(1); // Proposal ownership still belongs to suggestion 7.
  });
  it('does not merge independently authored text-only instructions just because their wording matches', async () => {
    const suggestions = [1, 2].map(id => ({ id, workflow_id: 'wf', failure_signature: 'timeout', fix_spec: '增加超时', proposal_json: null, status: 'pending' }));
    expect(await createRepairSourcePort(readers({ groups: async () => [], suggestions: async () => suggestions })).load(db, 'wf')).toHaveLength(2);
  });
  it('skips one malformed legacy suggestion without hiding valid repair items', async () => {
    const warning = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const suggestions = async () => [
      { id: 1, workflow_id: 'wf', failure_signature: 'bad', fix_spec: '损坏来源', proposal_json: '{bad', status: 'pending' },
      { id: 2, workflow_id: 'wf', failure_signature: 'timeout', fix_spec: '增加超时', proposal_json: null, status: 'pending' },
    ];
    const result = await createRepairSourcePort(readers({ groups: async () => [], suggestions })).load(db, 'wf');
    expect(result).toHaveLength(1);
    expect(result[0].item.instruction).toBe('增加超时');
    expect(warning).toHaveBeenCalledWith('[workflow-repair] skipped incompatible suggestion source', expect.objectContaining({ workflowId: 'wf', suggestionId: '1' }));
    warning.mockRestore();
  });
  it('skips one legacy suggestion that exceeds the repair item contract without hiding valid items', async () => {
    const warning = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const suggestions = async () => [
      { id: 1, workflow_id: 'wf', failure_signature: 'bad', fix_spec: 'x'.repeat(20_001), proposal_json: null, status: 'pending' },
      { id: 2, workflow_id: 'wf', failure_signature: 'timeout', fix_spec: '增加超时', proposal_json: null, status: 'pending' },
    ];
    const result = await createRepairSourcePort(readers({ groups: async () => [], suggestions })).load(db, 'wf');
    expect(result).toHaveLength(1);
    expect(result[0].item.instruction).toBe('增加超时');
    expect(warning).toHaveBeenCalledWith('[workflow-repair] skipped incompatible suggestion source', expect.objectContaining({ workflowId: 'wf', suggestionId: '1' }));
    warning.mockRestore();
  });
  it('skips one oversized legacy suggestion without hiding valid items', async () => {
    const warning = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const suggestions = async () => [
      { id: 1, workflow_id: 'wf', failure_signature: 'bad', fix_spec: '过大建议', proposal_json: JSON.stringify({ ...proposal(), details: 'x'.repeat(1_100_000) }), status: 'pending' },
      { id: 2, workflow_id: 'wf', failure_signature: 'timeout', fix_spec: '增加超时', proposal_json: null, status: 'pending' },
    ];
    const result = await createRepairSourcePort(readers({ groups: async () => [], suggestions })).load(db, 'wf');
    expect(result).toHaveLength(1);
    expect(result[0].item.instruction).toBe('增加超时');
    expect(warning).toHaveBeenCalledWith('[workflow-repair] skipped incompatible suggestion source', expect.objectContaining({ workflowId: 'wf', suggestionId: '1', code: 'PAYLOAD_TOO_LARGE' }));
    warning.mockRestore();
  });
  it('rejects mismatched workflow groups and proposals', async () => {
    await expect(createRepairSourcePort(readers({ groups: async () => [{ workflowId: 'other', signature: 'x', inputDigest: 'a'.repeat(64), sources: [diagnosis()] }] })).load(db, 'wf')).rejects.toMatchObject({ code: 'CONTENT_MISMATCH' });
    await expect(createRepairSourcePort(readers({ groups: async () => [{ workflowId: 'wf', signature: 'x', inputDigest: 'a'.repeat(64), sources: [diagnosis('d', { ...proposal(), workflowId: 'other' })] }] })).load(db, 'wf')).rejects.toMatchObject({ code: 'CONTENT_MISMATCH' });
  });
  it('never leaks evidence from another workflow or run through guessed event ids', async () => {
    const result = await createRepairSourcePort(readers({ evidence: async () => [{ event_id: 'ev-d1', workflow_id: 'other', flow_id: 'run-d1', payload_json: '{"secret":"hidden"}', event_type: 'node.failed' }] })).load(db, 'wf');
    expect(JSON.stringify(result)).not.toContain('hidden');
    expect(result[0].item.context).toMatchObject({ diagnoses: [{ evidence: [{ eventId: 'ev-d1', missing: true }] }] });
  });
  it('marks missing evidence explicitly instead of inventing a diagnosis', async () => {
    const result = await createRepairSourcePort(readers({ evidence: async () => [] })).load(db, 'wf');
    expect(result[0].item.context).toMatchObject({ diagnoses: [{ evidence: [{ eventId: 'ev-d1', missing: true }] }] });
  });
  it('surfaces invalid evidence JSON and does not convert a failed source query to no problems', async () => {
    await expect(createRepairSourcePort(readers({ groups: async () => { throw new Error('database unavailable'); } })).load(db, 'wf')).rejects.toThrow('database unavailable');
    await expect(createRepairSourcePort(readers({ evidence: async () => [{ event_id: 'ev-d1', workflow_id: 'wf', flow_id: 'run-d1', event_type: 'failed', payload_json: '{bad' }] })).load(db, 'wf')).rejects.toMatchObject({ code: 'INVALID_INPUT' });
  });
});
