import type { OverviewHealth, OverviewNodeStats, OverviewTrendPoint } from '@/domain/taskEscortParity/overviewTypes';
import { count, list, metric, record, requiredText, text } from './overviewValidation';

export function mapOverviewHealth(value: unknown): OverviewHealth {
  const row = record(value);
  return {
    overallScore: metric(row.overallScore, 100),
    p95DurationMs: metric(row.p95DurationMs),
    successRate: metric(row.successRate, 1),
    nodeFailureRate: metric(row.nodeFailureRate, 1),
    retryRate: metric(row.retryRate),
    totalTokens: metric(row.totalTokens),
    bottleneckNode: text(row.bottleneckNode),
    fragileNode: text(row.fragileNode),
    recommendation: text(row.recommendation),
  };
}
export function mapOverviewTrend(value: unknown): OverviewTrendPoint[] {
  return list(value).map((entry) => {
    const row = record(entry),
      date = requiredText(row.date, '趋势日期');
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(Date.parse(date))) throw new Error('趋势日期无效');
    return {
      date,
      totalRuns: count(row.totalRuns),
      succeededRuns: count(row.succeededRuns),
      failedRuns: count(row.failedRuns),
      successRate: metric(row.successRate, 100),
    };
  });
}
export function mapOverviewNodeStats(value: unknown, workflowId: string): OverviewNodeStats {
  const row = record(value);
  if (row.workflowId !== workflowId) throw new Error('节点统计不属于当前工作流');
  return {
    workflowId,
    totalRuns: count(row.totalRuns),
    nodes: list(row.nodes).map((value) => {
      const node = record(value);
      return {
        nodeId: requiredText(node.nodeId, '节点 ID'),
        title: text(node.nodeTitle),
        executor: text(node.executorType),
        totalExecutions: count(node.totalExecutions),
        succeededCount: count(node.succeededCount),
        failedCount: count(node.failedCount),
        failureRate: metric(node.failureRate, 1),
        avgRetryCount: metric(node.avgRetryCount),
        p50DurationMs: metric(node.p50DurationMs),
        p95DurationMs: metric(node.p95DurationMs),
        avgTokens: metric(node.avgTokens),
        lastError: text(node.lastErrorText),
        errorCategories: Array.isArray(node.errorCategories)
          ? node.errorCategories.map((value) => {
              const category = record(value);
              return {
                category: requiredText(category.category, '错误分类'),
                count: count(category.count),
                sampleError: text(category.sampleError),
              };
            })
          : [],
      };
    }),
  };
}
