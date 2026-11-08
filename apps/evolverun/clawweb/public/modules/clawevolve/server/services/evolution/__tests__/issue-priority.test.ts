import { expect, it } from 'vitest';
import { compareIssuePriority, issuePriority } from '../issue-priority.js';
import type { IssueGroup } from '../issue-aggregation.js';
const now = 1_800_000_000_000;
const group = (flowIds: string[], severity: 'high' | 'critical' | null = 'high') => ({ signature: 's', flowIds,
  sources: [{ analysisId: severity ? 'real' : 'legacy', severity, nodeId: 'n', diagnosisId: 'd' }] }) as IssueGroup;
it('deduplicates affected runs using actual run times and excludes missing, future and unrelated runs', () => {
  const value = issuePriority(group(['a', 'a', 'week', 'old', 'missing', 'future']), new Map([
    ['a', now - 100], ['week', now - 2 * 86_400_000], ['old', now - 9 * 86_400_000],
    ['future', now + 100], ['unrelated', now - 1],
  ]), now);
  expect(value).toMatchObject({ severity: 'high', day: { affected: 1 }, week: { affected: 2 }, lastOccurredAtMs: now - 100, missingRunTimes: 1 });
  expect(value.week).toMatchObject({ related: null, analyzed: null, complete: null, sampleHitRate: null, occurrenceRate: null });
  expect(value.trend).toBeNull();
  expect(value.previousWeek).toBeNull();
});
it('preserves unknown severity and unknown run times', () => {
  const value = issuePriority(group(['missing'], null), new Map(), now);
  expect(value.severity).toBeNull();
  expect(value.lastOccurredAtMs).toBeNull();
  expect(value.week.affected).toBe(0);
});
it('sorts impact by severity then recency and frequency by observed seven-day counts', () => {
  const times = new Map([['old', now - 1000], ['recent', now - 100], ['extra', now - 200]]);
  const severe = issuePriority(group(['old'], 'critical'), times, now);
  const recent = issuePriority(group(['recent']), times, now);
  const frequent = issuePriority(group(['old', 'extra']), times, now);
  expect(compareIssuePriority(severe, frequent, 'priority')).toBeLessThan(0);
  expect(compareIssuePriority(recent, frequent, 'priority')).toBeLessThan(0);
  expect(compareIssuePriority(frequent, recent, 'frequency')).toBeLessThan(0);
  expect(compareIssuePriority(recent, frequent, 'recent')).toBeLessThan(0);
});
