import type { TaskEscortFlowRun, TaskEscortWorkflowType } from '@/components/BotWorkshop/TaskEscort/types';
import { taskEscortService } from '@/services/taskEscort';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

const POLL_INTERVAL = 5000;
const LIVE_RUN_STATUSES = new Set(['running', 'waiting']);

export function isLiveRunStatus(status?: string | null): boolean {
  return !!status && LIVE_RUN_STATUSES.has(status);
}

export interface UseTaskEscortOptions {
  botOwnerId?: string;
  botId?: string;
  enabled: boolean;
}

export interface UseTaskEscortReturn {
  workflowTypes: TaskEscortWorkflowType[];
  flowRuns: TaskEscortFlowRun[];
  isLoadingTypes: boolean;
  isLoadingRuns: boolean;
  isRefreshingDashboard: boolean;
  isRefreshingDetail: boolean;
  error: string | null;
  selectedWorkflowId: string | null;
  loadTypes: () => Promise<void>;
  refreshDashboard: () => Promise<void>;
  refreshDetail: () => Promise<void>;
  navigateToDetail: (workflowId: string) => void;
  backToDashboard: () => void;
}

export function useTaskEscort({ botOwnerId, botId, enabled }: UseTaskEscortOptions): UseTaskEscortReturn {
  const [workflowTypes, setWorkflowTypes] = useState<TaskEscortWorkflowType[]>([]);
  const [flowRuns, setFlowRuns] = useState<TaskEscortFlowRun[]>([]);
  const [isLoadingTypes, setIsLoadingTypes] = useState(false);
  const [isLoadingRuns, setIsLoadingRuns] = useState(false);
  const [isRefreshingDashboard, setIsRefreshingDashboard] = useState(false);
  const [isRefreshingDetail, setIsRefreshingDetail] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadTypes = useCallback(async () => {
    if (!botOwnerId && !botId) return;
    setIsLoadingTypes(true);
    setError(null);
    try {
      const data = await taskEscortService.listWorkflowTypes(botOwnerId, botId);
      setWorkflowTypes(data as TaskEscortWorkflowType[]);
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : '加载工作流列表失败';
      setError(message);
    } finally {
      setIsLoadingTypes(false);
    }
  }, [botOwnerId, botId]);

  const loadRuns = useCallback(
    async (workflowId: string) => {
      setIsLoadingRuns(true);
      setError(null);
      try {
        const runs = await taskEscortService.listFlowRuns({
          workflowId,
          botOwnerId,
          botId,
          limit: 50,
        });
        setFlowRuns(runs as TaskEscortFlowRun[]);
      } catch (e: unknown) {
        const message = e instanceof Error ? e.message : '加载运行记录失败';
        setError(message);
      } finally {
        setIsLoadingRuns(false);
      }
    },
    [botOwnerId, botId],
  );

  const refreshDashboard = useCallback(async () => {
    setIsRefreshingDashboard(true);
    try {
      await loadTypes();
    } finally {
      setIsRefreshingDashboard(false);
    }
  }, [loadTypes]);

  const refreshDetail = useCallback(async () => {
    if (!selectedWorkflowId) return;
    setIsRefreshingDetail(true);
    try {
      await loadRuns(selectedWorkflowId);
    } finally {
      setIsRefreshingDetail(false);
    }
  }, [selectedWorkflowId, loadRuns]);

  const navigateToDetail = useCallback(
    (workflowId: string) => {
      setSelectedWorkflowId(workflowId);
      loadRuns(workflowId);
    },
    [loadRuns],
  );

  const backToDashboard = useCallback(() => {
    setSelectedWorkflowId(null);
    setFlowRuns([]);
  }, []);

  // Initial load
  useEffect(() => {
    if (enabled) {
      loadTypes();
    }
  }, [enabled, loadTypes]);

  // Polling for live workflows
  const hasRunning = useMemo(() => workflowTypes.some((w) => isLiveRunStatus(w.last_status)), [workflowTypes]);

  useEffect(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    if (enabled && hasRunning) {
      pollRef.current = setInterval(() => {
        loadTypes();
        if (selectedWorkflowId) {
          loadRuns(selectedWorkflowId);
        }
      }, POLL_INTERVAL);
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [enabled, hasRunning, selectedWorkflowId, loadTypes, loadRuns]);

  return {
    workflowTypes,
    flowRuns,
    isLoadingTypes,
    isLoadingRuns,
    isRefreshingDashboard,
    isRefreshingDetail,
    error,
    selectedWorkflowId,
    loadTypes,
    refreshDashboard,
    refreshDetail,
    navigateToDetail,
    backToDashboard,
  };
}
