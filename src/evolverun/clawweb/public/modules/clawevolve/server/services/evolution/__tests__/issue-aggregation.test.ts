import { describe, expect, it } from 'vitest';
import { buildAggregationModelInput, buildIssueGroups, validateIssueSummary } from '../issue-aggregation.js';

const diagnosis = (id: string, signature = 'timeout · cli · fetch') => ({
  diagnosisId: id, flowIds: ['run-a'], nodeId: 'fetch', failureSignature: signature,
  failureMode: 'timeout', severity: 'high' as const, reasoning: id, evidenceEventIds: [`event-${id}`],
});
const analysis = (id: string, flowId: string, time: number, diagnoses: ReturnType<typeof diagnosis>[]) => ({
  analysisId: id, flowId, completedAtMs: time,
  diagnoses: diagnoses.map(d => ({ ...d, flowIds: [flowId] })),
});

describe('issue aggregation', () => {
  it('replaces the entire older analysis, retaining two causes in the latest run and counting unique runs', () => {
    const groups = buildIssueGroups('wf', [
      analysis('old', 'run-a', 1, [diagnosis('obsolete')]),
      analysis('new', 'run-a', 2, [diagnosis('network'), diagnosis('retry')]),
      analysis('other', 'run-b', 3, [diagnosis('network-b')]),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].flowIds).toEqual(['run-a', 'run-b']);
    expect(groups[0].sources.map(s => s.diagnosisId).sort()).toEqual(['network', 'network-b', 'retry']);
  });
  it('does not resurrect an old issue when the latest completed analysis is empty', () => {
    expect(buildIssueGroups('wf', [analysis('old', 'run-a', 1, [diagnosis('old')]), analysis('new', 'run-a', 2, [])])).toEqual([]);
    expect(buildIssueGroups('wf', [analysis('old', 'run-a', 1, [diagnosis('old')]),
      { analysisId: 'batch', flowId: null, flowIds: ['run-a'], completedAtMs: 3, diagnoses: [] }])).toEqual([]);
  });
  it('keeps separate signatures and a stable digest regardless of input order', () => {
    const inputs = [analysis('a', 'run-a', 1, [diagnosis('a')]), analysis('b', 'run-b', 2, [diagnosis('b', 'timeout · approval · review')])];
    expect(buildIssueGroups('wf', inputs)).toEqual(buildIssueGroups('wf', [...inputs].reverse()));
    expect(buildIssueGroups('wf', inputs)).toHaveLength(2);
  });
  it('preserves multiple causes and rejects invented or unaccounted source references', () => {
    const group = buildIssueGroups('wf', [analysis('a', 'run-a', 1, [diagnosis('network'), diagnosis('retry')])])[0];
    const result = { summary: 'Two possible causes', causes: group.sources.map(s => ({
      title: s.diagnosisId, conclusion: s.reasoning, certainty: 'hypothesis', sourceIds: [s.sourceId],
    })), unknowns: ['Needs verification'] };
    expect(validateIssueSummary(result, group).causes).toHaveLength(2);
    expect(() => validateIssueSummary({ ...result, causes: [{ ...result.causes[0], sourceIds: ['invented'] }] }, group)).toThrow();
    expect(() => validateIssueSummary({ ...result, causes: result.causes.slice(0, 1) }, group)).toThrow();
  });
  it('keeps representative diagnoses detailed and compacts repeated tail sources without losing source ids', () => {
    const inputs = Array.from({ length: 500 }, (_, index) => analysis(`a-${index}`, `run-${index}`, index + 1, [{
      ...diagnosis(`d-${index}`),
      reasoning: `共同超时原因 ${index % 3}`.repeat(80),
      proposal: { schemaVersion: 'workflow-patch/v1', workflowId: 'wf', summary: `建议 ${index}`,
        operations: [{ op: 'replace', nodeId: 'fetch', path: '/executor/prompt', value: index % 2 ? '禁止工具调用' : '严格 JSON' }] },
    }]));
    const group = buildIssueGroups('wf', inputs)[0];
    const compact = buildAggregationModelInput(group);
    expect(compact.inputSummary).toMatchObject({ totalSources: 500, fullSources: 24, compactSources: 476 });
    expect(compact.sources.filter(source => source.detailLevel === 'full')).toHaveLength(24);
    expect(compact.sources.filter(source => source.detailLevel === 'compact')).toHaveLength(476);
    expect(compact.sources.map(source => source.sourceId).sort()).toEqual(group.sources.map(source => source.sourceId).sort());
    expect(compact.sources.find(source => 'flowId' in source && source.flowId === 'run-499')?.detailLevel).toBe('full');
    expect(compact.sources.some(source => source.detailLevel === 'compact' && !!source.representativeSourceId)).toBe(true);
    expect(Buffer.byteLength(JSON.stringify(compact), 'utf8')).toBeLessThan(180_000);
  });
  it('keeps distinct causes representative even when they recommend the same repair action', () => {
    const sharedProposal = { schemaVersion: 'workflow-patch/v1', workflowId: 'wf', summary: 'Increase timeout',
      operations: [{ op: 'replace', nodeId: 'fetch', path: '/timeoutMs', value: 30_000 }] };
    const inputs = Array.from({ length: 25 }, (_, index) => analysis(`a-${index}`, `run-${index}`, index + 1, [{
      ...diagnosis(`d-${index}`), proposal: sharedProposal,
      reasoning: index === 0 ? 'Old service deadlock requires separate investigation' : 'Shared upstream network latency',
    }]));
    const group = buildIssueGroups('wf', inputs)[0];
    const uniqueSource = group.sources.find(source => source.reasoning.startsWith('Old service deadlock'))!;
    const compact = buildAggregationModelInput(group);
    expect(compact.sources.find(source => source.sourceId === uniqueSource.sourceId)?.detailLevel).toBe('full');
  });
});
