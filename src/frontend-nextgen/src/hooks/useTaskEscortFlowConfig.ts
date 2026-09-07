import type { TaskEscortWorkflowItem, TaskEscortWorkflowSpec } from '@/components/BotWorkshop/TaskEscort/types';
import type { CreateWorkflowFromYamlInput, WorkflowImportErrorField } from '@/services/taskEscort';
import { taskEscortService, WorkflowImportValidationError } from '@/services/taskEscort';
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
  isCreatingWorkflow: boolean;
  isSaving: boolean;
  error: string | null;
  selectWorkflow: (workflowId: string) => void;
  refreshList: () => Promise<void>;
  createWorkflowFromYaml: (
    input: Pick<CreateWorkflowFromYamlInput, 'yaml' | 'command' | 'remark'>,
  ) => Promise<{ ok: true } | { ok: false; field: WorkflowImportErrorField; message: string }>;
  updateSpec: (spec: TaskEscortWorkflowSpec) => void;
  saveSpec: () => Promise<{ ok: true } | { ok: false; message: string }>;
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
  const [isCreatingWorkflow, setIsCreatingWorkflow] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
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

  const createWorkflowFromYaml = useCallback(
    async (input: Pick<CreateWorkflowFromYamlInput, 'yaml' | 'command' | 'remark'>) => {
      setIsCreatingWorkflow(true);
      setError(null);
      try {
        const created = await taskEscortService.createWorkflowFromYaml({ ...input, botOwnerId, botId }, workflows);
        setSelectedWorkflowId(created.id);
        setSpec(created as TaskEscortWorkflowSpec);
        await loadList();
        return { ok: true } as const;
      } catch (cause: unknown) {
        if (cause instanceof WorkflowImportValidationError) {
          return { ok: false, field: cause.field, message: cause.message } as const;
        }
        setError(cause instanceof Error ? cause.message : '创建工作流失败');
        return {
          ok: false,
          field: 'yaml' as const,
          message: cause instanceof Error ? cause.message : '创建工作流失败',
        };
      } finally {
        setIsCreatingWorkflow(false);
      }
    },
    [botId, botOwnerId, loadList, workflows],
  );

  const updateSpec = useCallback((nextSpec: TaskEscortWorkflowSpec) => {
    setSpec(nextSpec);
  }, []);

  const saveSpec = useCallback(async () => {
    if (!spec || !selectedWorkflowId) {
      return { ok: false, message: '没有可保存的工作流' } as const;
    }
    setIsSaving(true);
    setError(null);
    try {
      await taskEscortService.saveWorkflowSpec(
        selectedWorkflowId,
        spec as unknown as Parameters<typeof taskEscortService.saveWorkflow>[0]['spec'],
        botOwnerId,
        botId,
      );
      return { ok: true } as const;
    } catch (cause: unknown) {
      const message = cause instanceof Error ? cause.message : '保存工作流失败';
      setError(message);
      return { ok: false, message } as const;
    } finally {
      setIsSaving(false);
    }
  }, [botId, botOwnerId, selectedWorkflowId, spec]);

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
    isCreatingWorkflow,
    isSaving,
    error,
    selectWorkflow,
    refreshList,
    createWorkflowFromYaml,
    updateSpec,
    saveSpec,
  };
}
