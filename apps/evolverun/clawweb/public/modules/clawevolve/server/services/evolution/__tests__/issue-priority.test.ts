import { expect, it } from 'vitest';
import { issuePriority } from '../issue-priority.js';
const now = 1_800_000_000_000;
it('makes affected runs and complete sample hits independently reconcilable', () => {
  const group = { signature: 's', flowIds: Array.from({ length: 33 }, (_, i) => `r${i}`),
    sources: [{ analysisId: 'real', severity: 'high', nodeId: 'n', diagnosisId: 'd' }] } as never;
  const runs = Array.from({ length: 168 }, (_, i) => ({ flowId: `r${i}`, startedAtMs: now - 1000, nodes: ['n'], analyzed: false,
    coverage: { whole: { at: now, id: i, completed: i < 46, complete: i < 14 || i >= 33 && i < 46 }, nodes: {}, legacyNodes: [] } }));
  expect(issuePriority(group, runs, now).week).toMatchObject({ affected: 33, related: 168, analyzed: 46, complete: 27,
    sampleHits: 14, sampleHitRate: 14 / 27, occurrenceRate: null });
});
it('deduplicates actual run times, separates analyzed samples, and preserves unknown severity', () => {
  const group = { signature: 's', flowIds: ['a', 'a', 'old', 'missing'], sources: [{ analysisId: 'legacy', severity: null, flowId: 'a', nodeId: 'n' }] } as never;
  const value = issuePriority(group, [
    { flowId: 'a', startedAtMs: now - 100, nodes: ['n'], analyzed: true },
    { flowId: 'b', startedAtMs: now - 200, nodes: ['n'], analyzed: false },
    { flowId: 'old', startedAtMs: now - 9 * 86_400_000, nodes: ['n'], analyzed: true },
    { flowId: 'other', startedAtMs: now - 100, nodes: ['other'], analyzed: true },
  ], now);
  expect(value.severity).toBeNull();
  expect(value.day).toMatchObject({ affected: 1, related: 2, analyzed: 1, sampleHitRate: 1, occurrenceRate: null });
  expect(value.week.affected).toBe(1);
  expect(value.missingRunTimes).toBe(1);
  expect(value.trend).toBeNull(); // prior coverage is incomplete
});
it('reports a trend only when both periods have complete relevant analysis coverage', () => {
  const group = { signature: 's', flowIds: ['a'], sources: [{ analysisId: 'real', diagnosisId: 'd', severity: 'critical', flowId: 'a', nodeId: 'n' }] } as never;
  const value = issuePriority(group, [
    { flowId: 'a', startedAtMs: now - 100, nodes: ['n'], analyzed: true },
    { flowId: 'b', startedAtMs: now - 8 * 86_400_000, nodes: ['n'], analyzed: true },
  ], now);
  expect(value.severity).toBe('critical'); expect(value.severitySources).toEqual(['d']);
  expect(value.trend).toBe('worsening'); expect(value.week.occurrenceRate).toBe(1);
});
it('does not reuse the current week as a historical comparison when prior runs are absent', () => {
  const group = { signature: 's', flowIds: ['a'], sources: [{ analysisId: 'real', severity: 'high', flowId: 'a', nodeId: 'n' }] } as never;
  const value = issuePriority(group, [{ flowId: 'a', startedAtMs: now - 100, nodes: ['n'], analyzed: true }], now);
  expect(value.previousWeek.related).toBe(0); expect(value.trend).toBeNull();
});
