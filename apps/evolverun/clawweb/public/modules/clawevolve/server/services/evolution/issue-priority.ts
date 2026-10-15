import type { IssueGroup } from './issue-aggregation.js';
export type IssueRun = { flowId: string; startedAtMs: number; nodes: string[]; analyzed: boolean };
export type IssueWindow = { affected: number; related: number; analyzed: number; sampleHitRate: number | null; occurrenceRate: number | null };
export type IssuePriority = { severity: 'low' | 'medium' | 'high' | 'critical' | null; severitySources: string[];
  day: IssueWindow; week: IssueWindow; previousWeek: IssueWindow; trend: 'worsening' | 'improving' | 'stable' | null;
  lastOccurredAtMs: number | null; missingRunTimes: number; nodes: string[]; sortReason: string };
const DAY = 86_400_000;
const severityRank = { low: 1, medium: 2, high: 3, critical: 4 };
/** All windows use runtime timestamps. Missing analysis is never treated as a clean result. */
export function issuePriority(group: IssueGroup, input: IssueRun[], now = Date.now()): IssuePriority {
  const nodes = [...new Set(group.sources.map(source => source.nodeId).filter((node): node is string => !!node))];
  const runs = [...new Map(input.map(run => [run.flowId, run])).values()];
  const affected = new Set(group.flowIds);
  const known = group.sources.filter(source => source.analysisId !== 'legacy' && source.severity != null);
  const severity = known.reduce<IssuePriority['severity']>((highest, source) => !highest || severityRank[source.severity!] > severityRank[highest] ? source.severity : highest, null);
  const window = (from: number, to: number): IssueWindow => {
    const relevant = runs.filter(run => run.startedAtMs >= from && run.startedAtMs < to
      && (affected.has(run.flowId) || !nodes.length || nodes.some(node => run.nodes.includes(node))));
    const analyzed = relevant.filter(run => run.analyzed), hits = relevant.filter(run => affected.has(run.flowId));
    const analyzedHits = analyzed.filter(run => affected.has(run.flowId));
    return { related: relevant.length, analyzed: analyzed.length, affected: hits.length,
      sampleHitRate: analyzed.length ? analyzedHits.length / analyzed.length : null,
      occurrenceRate: relevant.length && analyzed.length === relevant.length ? hits.length / relevant.length : null };
  };
  const day = window(now - DAY, now), week = window(now - 7 * DAY, now), previousWeek = window(now - 14 * DAY, now - 7 * DAY);
  const trend = week.occurrenceRate == null || previousWeek.occurrenceRate == null ? null
    : week.occurrenceRate > previousWeek.occurrenceRate ? 'worsening' : week.occurrenceRate < previousWeek.occurrenceRate ? 'improving' : 'stable';
  const timestamps = runs.filter(run => affected.has(run.flowId)).map(run => run.startedAtMs);
  return { severity, severitySources: known.filter(source => source.severity === severity).map(source => source.diagnosisId),
    day, week, previousWeek, trend, nodes, lastOccurredAtMs: timestamps.length ? Math.max(...timestamps) : null,
    missingRunTimes: [...affected].filter(id => !runs.some(run => run.flowId === id)).length,
    sortReason: '有依据的影响程度 → 近7天命中率 → 恶化趋势 → 最近运行时间；待评估单独标记，不代表低风险' };
}
export type IssueSort = 'priority' | 'frequency' | 'recent';
export function compareIssuePriority(a: IssuePriority, b: IssuePriority, sort: IssueSort): number {
  const frequency = (b.week.occurrenceRate ?? b.week.sampleHitRate ?? -1) - (a.week.occurrenceRate ?? a.week.sampleHitRate ?? -1);
  const recent = (b.lastOccurredAtMs ?? 0) - (a.lastOccurredAtMs ?? 0);
  if (sort === 'frequency') return frequency || recent;
  if (sort === 'recent') return recent;
  return (b.severity ? severityRank[b.severity] : 0) - (a.severity ? severityRank[a.severity] : 0)
    || frequency || Number(b.trend === 'worsening') - Number(a.trend === 'worsening') || recent;
}
