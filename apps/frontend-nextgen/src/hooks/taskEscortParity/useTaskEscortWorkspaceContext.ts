import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import type { PageId } from '@/domain/taskEscortWorkspace/types';
import {
  readWorkspacePage,
  workspaceNavigationSearch,
  workspaceSelectionSearch,
} from '@/services/taskEscortParity/workspaceNavigation';
import { readTaskEscortWorkflows } from '@/services/taskEscortParity/workspaceService';
import { history, useLocation } from '@umijs/max';
import { useCallback, useEffect, useMemo, useRef } from 'react';
import { useTaskEscortRead } from './useTaskEscortRead';

interface WorkspaceScope {
  key: string;
  ready: boolean;
  loading?: boolean;
  error?: string;
  userId?: string;
  retry?: () => void;
}

export function useTaskEscortWorkspaceContext(scope: WorkspaceScope) {
  const location = useLocation();
  const page = readWorkspacePage(location.search);
  const query = useMemo(() => Object.fromEntries(new URLSearchParams(location.search)), [location.search]);
  // List readiness is identity-bound, not workflow-bound: selecting a workflow must not reload the list.
  const listContext: TaskEscortParityContext = {
    scopeKey: scope.key,
    ready: scope.ready,
    userId: scope.userId,
    workflowId: '',
    workflow: null,
    query: {},
    navigate: () => undefined,
    refreshWorkflows: () => undefined,
  };
  const read = useCallback((signal: AbortSignal) => readTaskEscortWorkflows(scope.userId, signal), [scope.userId]);
  const resource = useTaskEscortRead(listContext, 'workspace-workflows', read);
  const workflows = resource.data ?? [];
  const explicitSelection = query.workflowId !== undefined;
  // An explicit deep-link target must never borrow another workflow's evidence.
  const workflow = explicitSelection
    ? workflows.find((row) => row.id === query.workflowId) ?? null
    : workflows[0] ?? null;
  const selectionError =
    resource.data && explicitSelection && !workflow
      ? '所选工作流不存在或当前身份无权访问，请选择其他工作流或刷新。'
      : undefined;
  const refreshWorkflows = useCallback(() => {
    if (scope.ready) resource.refresh();
    else scope.retry?.();
  }, [scope.ready, scope.retry, resource.refresh]);
  const navigate = useCallback(
    (target: PageId, params?: Record<string, string | undefined>) => {
      // Never capture render-time location: dashboard may navigate then select in a single event.
      const current = history.location;
      const currentId = new URLSearchParams(current.search).get('workflowId');
      const selected =
        currentId && workflows.some((row) => row.id === currentId) ? {} : workflow ? { workflowId: workflow.id } : {};
      const nextId = params?.workflowId;
      const search =
        nextId !== undefined
          ? workspaceSelectionSearch(current.search, nextId, currentId ?? workflow?.id ?? '')
          : current.search;
      history.push(
        `${current.pathname}${workspaceNavigationSearch(search, target, { ...selected, ...params })}${current.hash}`,
      );
    },
    [workflows, workflow?.id],
  );
  const pendingCreated = useRef<{ scopeKey: string; userId: string | undefined; id: string } | null>(null);
  const openCreatedWorkflow = useCallback(
    (id: string) => {
      pendingCreated.current = { scopeKey: scope.key, userId: scope.userId, id };
      navigate('editor', { workflowId: id });
    },
    [scope.key, scope.userId, navigate],
  );
  useEffect(() => {
    const pending = pendingCreated.current;
    if (!pending) return;
    if (pending.scopeKey !== scope.key || pending.userId !== scope.userId) {
      pendingCreated.current = null;
      return;
    }
    if (scope.ready && page === 'editor' && query.workflowId === pending.id) {
      pendingCreated.current = null;
      resource.refresh();
    }
  }, [scope.key, scope.userId, scope.ready, page, query.workflowId, resource.refresh]);
  const selectWorkflow = useCallback(
    (id: string) => {
      if (!workflows.some((row) => row.id === id)) return;
      const current = history.location;
      const selectedId = new URLSearchParams(current.search).get('workflowId') ?? workflow?.id ?? '';
      history.push(`${current.pathname}${workspaceSelectionSearch(current.search, id, selectedId)}${current.hash}`);
    },
    [workflows, workflow?.id],
  );
  const context: TaskEscortParityContext = {
    scopeKey: scope.key,
    ready: scope.ready && !resource.loading && !resource.error && !!workflow,
    creationReady: scope.ready && !!scope.userId?.trim(),
    loading: (!scope.ready && !!scope.loading) || resource.loading,
    error: !scope.ready ? scope.error : resource.error ?? selectionError,
    userId: scope.userId,
    workflowId: workflow?.id ?? '',
    workflow,
    query,
    navigate,
    refreshWorkflows,
    openCreatedWorkflow,
  };
  // Workflow readiness is false for both empty and unavailable collections; only a successful
  // identity-bound list without an explicit target may enter the product introduction.
  const showIntroduction =
    scope.ready && !resource.loading && !resource.error && resource.data?.length === 0 && !explicitSelection;
  return { context, page, workflows, selectWorkflow, showIntroduction };
}
