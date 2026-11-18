import { overviewTimeRange, runListParams } from '@/domain/taskEscortParity/overview';
import type { OverviewDays, OverviewRunFilters } from '@/domain/taskEscortParity/overviewTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { overviewService } from '@/services/taskEscortParity/overviewService';
import { useCallback, useMemo } from 'react';
import { useTaskEscortRead } from './useTaskEscortRead';

export function useTaskEscortOverview(
  context: TaskEscortParityContext,
  days: OverviewDays,
  filters: OverviewRunFilters,
) {
  const { workflowId, userId } = context;
  const range = useMemo(() => overviewTimeRange(days), [days, workflowId, context.scopeKey, userId]);
  const params = useMemo(() => runListParams(workflowId, userId, filters), [workflowId, userId, filters]);
  const apiDays = days === 'today' || days === 'yesterday' ? 1 : Number(days);
  const readMetrics = useCallback(
    (signal: AbortSignal) => overviewService.runs({ workflowId, user_id: userId, limit: 1, ...range }, signal),
    [workflowId, userId, range],
  );
  const readRuns = useCallback((signal: AbortSignal) => overviewService.runs(params, signal), [params]);
  const readHealth = useCallback(
    (signal: AbortSignal) => overviewService.health(workflowId, { user_id: userId, days: apiDays }, signal),
    [workflowId, userId, apiDays],
  );
  const readTrend = useCallback(
    (signal: AbortSignal) => overviewService.trend(workflowId, { user_id: userId, days: apiDays }, signal),
    [workflowId, userId, apiDays],
  );
  const readNodes = useCallback(
    (signal: AbortSignal) => overviewService.nodeStats(workflowId, { user_id: userId }, signal),
    [workflowId, userId],
  );
  const metrics = useTaskEscortRead(context, `overview:metrics:${days}`, readMetrics);
  const runs = useTaskEscortRead(context, 'overview:runs', readRuns);
  const health = useTaskEscortRead(context, `overview:health:${days}`, readHealth);
  const trend = useTaskEscortRead(context, `overview:trend:${days}`, readTrend);
  const nodes = useTaskEscortRead(context, 'overview:nodeStats', readNodes);
  const refresh = () => {
    metrics.refresh();
    runs.refresh();
    health.refresh();
    trend.refresh();
    nodes.refresh();
  };
  return { metrics, runs, health, trend, nodes, refresh };
}
