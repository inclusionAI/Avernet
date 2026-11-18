import { dashboardRange, resolveDashboardWidgetRange } from '@/domain/taskEscortWorkspace/dashboard';
import type {
  DashboardRangeKey,
  DashboardScope,
  DashboardWidget,
  DashboardWidgetRange,
} from '@/domain/taskEscortWorkspace/dashboardTypes';
import { dashboardService } from '@/services/taskEscortWorkspace/dashboardService';
import { useCallback, useMemo, useState } from 'react';
import { useTaskEscortDashboardResource } from './useTaskEscortDashboardResource';

export const unresolvedDashboardScope: DashboardScope = { key: 'unresolved-dashboard-identity', ready: false };

/** Scope is supplied by the entry's existing identity hook; this hook must not load identity again. */
export function useTaskEscortDashboard(
  scope: DashboardScope = unresolvedDashboardScope,
  range: DashboardRangeKey = '7d',
) {
  const [widgetRanges, setWidgetRanges] = useState<Record<DashboardWidget, DashboardWidgetRange>>({
    trend: 'global',
    hotspots: 'global',
    workflows: 'global',
  });
  const [clock, setClock] = useState(() => ({ now: Math.floor(Date.now() / 1000), revision: 0 }));
  // An object token, rather than the string key, also isolates A → B → A and readiness revocation.
  const identity = useMemo(() => ({}), [scope.key, scope.ready, scope.loading, scope.error]);
  const [denied, setDenied] = useState<object | null>(null);
  const forbidden = denied === identity;
  const onForbidden = useCallback(() => setDenied(identity), [identity]);
  const enabled = scope.ready && !scope.loading && !scope.error && !forbidden;
  const refresh = useCallback(() => {
    setDenied(null);
    setClock((value) => ({ now: Math.floor(Date.now() / 1000), revision: value.revision + 1 }));
  }, []);
  const setWidgetRange = useCallback((widget: DashboardWidget, value: DashboardWidgetRange) => {
    setWidgetRanges((previous) => ({ ...previous, [widget]: value }));
  }, []);
  const global = dashboardRange(range, clock.now);
  const windows = {
    trend: resolveDashboardWidgetRange(widgetRanges.trend, global, clock.now),
    hotspots: resolveDashboardWidgetRange(widgetRanges.hotspots, global, clock.now),
    workflows: resolveDashboardWidgetRange(widgetRanges.workflows, global, clock.now),
  };
  const overview = useTaskEscortDashboardResource(
    identity,
    enabled,
    global,
    clock.revision,
    dashboardService.overview,
    onForbidden,
  );
  const trend = useTaskEscortDashboardResource(
    identity,
    enabled,
    windows.trend,
    clock.revision,
    dashboardService.dailyTrend,
    onForbidden,
  );
  const hotspots = useTaskEscortDashboardResource(
    identity,
    enabled,
    windows.hotspots,
    clock.revision,
    dashboardService.failureHotspots,
    onForbidden,
  );
  const health = useTaskEscortDashboardResource(
    identity,
    enabled,
    windows.workflows,
    clock.revision,
    dashboardService.workflowHealth,
    onForbidden,
  );
  const releases = useTaskEscortDashboardResource(
    identity,
    enabled,
    windows.workflows,
    clock.revision,
    dashboardService.workflowReleaseStats,
    onForbidden,
  );
  return {
    range,
    rangeLabel: global.label,
    widgetRanges,
    windows,
    setWidgetRange,
    refresh,
    forbidden,
    overview,
    trend,
    hotspots,
    health,
    releases,
  };
}
