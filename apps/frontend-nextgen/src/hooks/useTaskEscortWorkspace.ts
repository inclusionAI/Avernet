import type {
  DomainRunDetail,
  PageId,
  Period,
  TaskEscortOperationResult,
  TaskEscortWorkspaceController,
} from '@/domain/taskEscortWorkspace';
import { readTaskEscortRoute, taskEscortSearch } from '@/services/taskEscortWorkspace/navigation';
import { createTaskEscortWorkspaceService } from '@/services/taskEscortWorkspace/service';
import { history, useLocation } from '@umijs/max';
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { toast } from 'sonner';

interface WorkspaceReadiness {
  loadWorkspace?: boolean;
  ready?: boolean;
  loading?: boolean;
  error?: string;
  retry?: () => void;
}
export interface TaskEscortWorkspaceState extends TaskEscortWorkspaceController {
  loadRunDetail: (id: string) => Promise<DomainRunDetail>;
  resetVersion: number;
  workspaceKey: string;
}

/** One live service per identity/space. View and legacy query changes never recreate read state. */
export function useTaskEscortWorkspace(scopeKey: string, readiness: WorkspaceReadiness = {}): TaskEscortWorkspaceState {
  const location = useLocation();
  const route = readTaskEscortRoute(location.search);
  const ready = readiness.ready ?? true;
  const loadWorkspace = readiness.loadWorkspace ?? true;
  const service = useMemo(() => createTaskEscortWorkspaceService(), [scopeKey]);
  const snapshot = useSyncExternalStore(service.subscribe, service.getSnapshot, service.getSnapshot);
  const [query, setQuery] = useState('');
  const [issueFilter, setIssueFilter] = useState('all');
  const [runFilter, setRunFilter] = useState('all');
  const [workflowId, selectWorkflow] = useState('all');
  const [selectedIssue, selectIssue] = useState<string | null>(null);
  const [selectedRun, selectRun] = useState<string | null>(null);
  const clearViewState = useCallback(() => {
    setQuery('');
    setIssueFilter('all');
    setRunFilter('all');
    selectWorkflow('all');
    selectIssue(null);
    selectRun(null);
  }, []);

  useEffect(() => {
    if (ready && loadWorkspace) void service.load();
    return service.cancelPending;
  }, [service, ready, loadWorkspace]);
  useEffect(() => {
    clearViewState();
  }, [service, clearViewState]);
  useEffect(() => {
    // Listen only to committed router transitions: a blocked editor navigation
    // must not discard filters. Synchronous listeners preserve follow-up target filters.
    let current = readTaskEscortRoute(history.location.search);
    return history.listen(({ location: next }) => {
      const target = readTaskEscortRoute(next.search);
      if (target.page !== current.page) clearViewState();
      current = target;
    });
  }, [clearViewState]);

  const writeRoute = useCallback(
    (next: typeof route) => {
      history.push(`${location.pathname}${taskEscortSearch(location.search, next)}${location.hash}`);
    },
    [location.pathname, location.search, location.hash],
  );
  const navigate = useCallback(
    (page: PageId) => {
      if (page !== route.page) writeRoute({ ...route, page });
    },
    [route.page, route.period, writeRoute],
  );
  const setPeriod = useCallback(
    (period: Period) => {
      if (period !== route.period) writeRoute({ ...route, period });
    },
    [route.page, route.period, writeRoute],
  );
  const notify = useCallback((result: TaskEscortOperationResult) => {
    if (result.ok) toast.success(result.message);
    else toast.error(result.message);
    return result.ok;
  }, []);
  const setSelectedIssue = useCallback(
    (id: string | null) => {
      selectIssue(id !== null && service.getSnapshot().issues.some((issue) => issue.id === id) ? id : null);
      selectRun(null);
    },
    [service],
  );
  const setSelectedRun = useCallback(
    (id: string | null) => {
      selectRun(id !== null && service.getSnapshot().runs.some((run) => run.id === id) ? id : null);
      selectIssue(null);
    },
    [service],
  );
  const setWorkflowId = useCallback(
    (id: string) => {
      if (id === 'all' || service.getSnapshot().workflows.some((workflow) => workflow.id === id)) selectWorkflow(id);
    },
    [service],
  );
  const reset = useCallback(() => {
    toast.error('真实模式不支持重置数据');
  }, []);
  const refresh = useCallback(() => {
    if (ready && loadWorkspace) void service.load();
    else if (!ready) readiness.retry?.();
  }, [service, ready, loadWorkspace, readiness.retry]);

  return {
    ...snapshot,
    page: route.page,
    period: route.period,
    mode: service.mode,
    loading: snapshot.loading || (!ready && !!readiness.loading),
    error: !ready ? readiness.error ?? null : snapshot.error,
    navigate,
    setPeriod,
    metrics: service.getMetrics(route.period),
    support: service.support,
    query,
    setQuery,
    issueFilter,
    setIssueFilter,
    runFilter,
    setRunFilter,
    workflowId,
    setWorkflowId,
    selectedIssue,
    setSelectedIssue,
    selectedRun,
    setSelectedRun,
    applySuggestion: (id) => {
      notify(service.applySuggestion(id));
    },
    verifyIssue: (id) => {
      notify(service.verifyIssue(id));
    },
    saveExperience: (id) => {
      notify(service.saveExperience(id));
    },
    updateSettings: (patch) => {
      const result = service.updateSettings(patch);
      if (!result.ok) notify(result);
    },
    saveSettings: () => notify(service.saveSettings()),
    notice: (message) => {
      toast(message);
    },
    reset,
    refresh,
    loadRunDetail: service.getRunDetail,
    resetVersion: 0,
    workspaceKey: `${scopeKey}:${service.mode}`,
  };
}
