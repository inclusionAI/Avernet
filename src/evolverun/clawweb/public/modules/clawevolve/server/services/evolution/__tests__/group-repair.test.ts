import { expect, it } from 'vitest';
import { repairCandidates, selectRepairCandidates, validateRepairOutcomes } from '../group-repair.js';

const proposal = { summary: 'allow null', operations: [{ path: '/outputContract/type', value: ['string', 'null'] }] };
const group = { workflowId: 'wf', signature: 'sig', inputDigest: 'digest', sources: [
  { sourceId: 'a', proposal }, { sourceId: 'b', proposal }, { sourceId: 'c', proposal: { ...proposal, summary: 'produce string' } }, { sourceId: 'd' },
] } as never;
it('deduplicates proposals without losing provenance and selects server-owned candidates', () => {
  const candidates = repairCandidates(group);
  expect(candidates).toHaveLength(2);
  expect(candidates[0].sources.map(s => s.sourceId)).toEqual(['a', 'b']);
  expect(selectRepairCandidates(group, 'digest', [candidates[1].id]).candidates).toEqual([candidates[1]]);
  expect(() => selectRepairCandidates(group, 'old', [candidates[0].id])).toThrow();
  expect(() => selectRepairCandidates(group, 'digest', ['invented'])).toThrow();
  expect(() => selectRepairCandidates(group, 'digest', [])).toThrow();
});
it('requires exactly one outcome per selected item, including unresolved items', () => {
  const scope = { candidates: [{ id: 'a' }, { id: 'b' }] };
  expect(() => validateRepairOutcomes([{ id: 'a', status: 'applied', reason: 'done' }], scope as never)).toThrow();
  expect(() => validateRepairOutcomes([{ id: 'x', status: 'applied', reason: 'done' }], scope as never)).toThrow();
  expect(validateRepairOutcomes([{ id: 'a', status: 'applied', reason: 'changed' }, { id: 'b', status: 'unresolved', reason: 'conflict' }], scope as never)).toHaveLength(2);
});
