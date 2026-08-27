import type { TaskEscortWorkflowItem, TaskEscortWorkflowSpec } from '@/components/BotWorkshop/TaskEscort/types';
import { taskEscortService } from '@/services/taskEscort';
import { useCallback, useEffect, useState } from 'react';

export interface UseTaskEscortFlowConfigOptions {
  botOwnerId?: string;
  botId?: string;
  enabled: boolean;
}

export interface UseTaskEscortFlowConfigReturn {
  workflows: TaskEscortWorkflowItem[];
  selectedWorkflowId: string | null;
  spec: TaskEscortWorkflowSpec | null;
  isLoadingList: boolean;
  isLoadingSpec: boolean;
  error: string | null;
  selectWorkflow: (workflowId: string) => void;
  refreshList: () => Promise<void>;
}

export function useTaskEscortFlowConfig({
  botOwnerId,
  botId,
  enabled,
}: UseTaskEscortFlowConfigOptions): UseTaskEscortFlowConfigReturn {
  const [workflows, setWorkflows] = useState<TaskEscortWorkflowItem[]>([]);
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(null);
  const [spec, setSpec] = useState<TaskEscortWorkflowSpec | null>(null);
  const [isLoadingList, setIsLoadingList] = useState(false);
  const [isLoadingSpec, setIsLoadingSpec] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadList = useCallback(async () => {
    if (!botOwnerId && !botId) return;
    setIsLoadingList(true);
    setError(null);
    try {
      const data = await taskEscortService.listWorkflows(botOwnerId, botId);
      setWorkflows(data as TaskEscortWorkflowItem[]);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '加载工作流列表失败');
    } finally {
      setIsLoadingList(false);
    }
  }, [botOwnerId, botId]);

  const loadSpec = useCallback(async (workflowId: string) => {
    setIsLoadingSpec(true);
    setError(null);
    try {
      const data = await taskEscortService.getWorkflow(workflowId);
      setSpec(data as TaskEscortWorkflowSpec);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '加载工作流定义失败');
    } finally {
      setIsLoadingSpec(false);
    }
  }, []);

  const selectWorkflow = useCallback(
    (workflowId: string) => {
      setSelectedWorkflowId(workflowId);
      loadSpec(workflowId);
    },
    [loadSpec],
  );

  const refreshList = useCallback(async () => {
    await loadList();
  }, [loadList]);

  useEffect(() => {
    if (enabled) {
      loadList();
    }
  }, [enabled, loadList]);

  return {
    workflows,
    selectedWorkflowId,
    spec,
    isLoadingList,
    isLoadingSpec,
    error,
    selectWorkflow,
    refreshList,
  };
}
