import type { IssueGroup } from './issue-aggregation.js';
/** Retired denominator/rate fields remain null for existing response consumers. */
export type IssueWindow = { affected: number; related: number | null; analyzed: number | null; complete?: number | null;
  legacyDiagnosed?: number | null; sampleHits?: number | null; sampleHitRate: number | null; occurrenceRate: number | null };
export type IssuePriority = { severity: 'low' | 'medium' | 'high' | 'critical' | null; severitySources: string[];
  day: IssueWindow; week: IssueWindow; previousWeek: IssueWindow | null; trend: 'worsening' | 'improving' | 'stable' | null;
  lastOccurredAtMs: number | null; missingRunTimes: number; nodes: string[]; sortReason: string };
const DAY = 86_400_000;
const severityRank = { low: 1, medium: 2, high: 3, critical: 4 };
/** Observed issue counts use deduplicated affected runs and actual runtime timestamps. */
export function issuePriority(group: IssueGroup, runTimes: ReadonlyMap<string, number>, now = Date.now()): IssuePriority {
  const affected = new Set(group.flowIds);
  const known = group.sources.filter(source => source.analysisId !== 'legacy' && source.severity != null);
  const severity = known.reduce<IssuePriority['severity']>((highest, source) => !highest || severityRank[source.severity!] > severityRank[highest] ? source.severity : highest, null);
  const timestamps: number[] = [];
  let missingRunTimes = 0;
  for (const id of affected) {
    const at = runTimes.get(id);
    if (at == null) missingRunTimes++;
    else if (at <= now) timestamps.push(at);
  }
  const window = (days: number): IssueWindow => ({ affected: timestamps.filter(at => at >= now - days * DAY).length,
    related: null, analyzed: null, complete: null, legacyDiagnosed: null, sampleHits: null, sampleHitRate: null, occurrenceRate: null });
  return { severity, severitySources: known.filter(source => source.severity === severity).map(source => source.diagnosisId),
    day: window(1), week: window(7), previousWeek: null, trend: null,
    nodes: [...new Set(group.sources.map(source => source.nodeId).filter((node): node is string => !!node))],
    lastOccurredAtMs: timestamps.length ? timestamps.reduce((latest, at) => Math.max(latest, at), 0) : null, missingRunTimes,
    sortReason: '有依据的影响程度 → 最近运行时间；发现次数按关联运行 ID 去重，不代表发生率' };
}
export type IssueSort = 'priority' | 'frequency' | 'recent';
export function compareIssuePriority(a: IssuePriority, b: IssuePriority, sort: IssueSort): number {
  const recent = (b.lastOccurredAtMs ?? 0) - (a.lastOccurredAtMs ?? 0);
  if (sort === 'frequency') return b.week.affected - a.week.affected || recent;
  if (sort === 'recent') return recent;
  return (b.severity ? severityRank[b.severity] : 0) - (a.severity ? severityRank[a.severity] : 0) || recent;
}
