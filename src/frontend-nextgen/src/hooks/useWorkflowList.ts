/**
 * useWorkflowList —— 「+」号工作流菜单的按需加载。
 *
 * 只有鼠标进入「工作流任务」时才按当前 Bot 请求 clawweb /api/workflows；
 * ownerBotId/ownerUserId 变化时清空已加载标记，避免沿用上一个 Bot 的工作流。
 * Controller 调用收口在 service 层（taskService.fetchWorkflows），Hook 不直接 import controller。
 */
import type { TaskComposerContext } from '@/services/tasks/taskMapper';
import { fetchWorkflows, type WorkflowListItem } from '@/services/tasks/taskService';
import { useCallback, useEffect, useRef, useState } from 'react';

export interface UseWorkflowListResult {
  workflows: WorkflowListItem[];
  workflowsLoading: boolean;
  /** 鼠标进入工作流菜单时按当前 Bot 懒加载工作流列表。 */
  loadWorkflows: () => Promise<void>;
}

export function useWorkflowList(context: TaskComposerContext | null): UseWorkflowListResult {
  const [workflows, setWorkflows] = useState<WorkflowListItem[]>([]);
  const [workflowsLoading, setWorkflowsLoading] = useState(false);
  const workflowRequestKeyRef = useRef<string | null>(null);
  const loadedWorkflowKeyRef = useRef<string | null>(null);

  const loadWorkflows = useCallback(async () => {
    const ownerUserId = context?.ownerUserId?.trim();
    const ownerBotId = context?.ownerBotId?.trim();
    if (!ownerUserId || !ownerBotId) {
      setWorkflows([]);
      setWorkflowsLoading(false);
      loadedWorkflowKeyRef.current = null;
      return;
    }
    const requestKey = `${ownerUserId}:${ownerBotId}`;
    if (loadedWorkflowKeyRef.current === requestKey || workflowRequestKeyRef.current === requestKey) return;
    workflowRequestKeyRef.current = requestKey;
    setWorkflowsLoading(true);
    try {
      const list = await fetchWorkflows(ownerUserId, ownerBotId);
      if (workflowRequestKeyRef.current === requestKey) {
        setWorkflows(list);
        loadedWorkflowKeyRef.current = requestKey;
      }
    } catch {
      if (workflowRequestKeyRef.current === requestKey) setWorkflows([]);
    } finally {
      if (workflowRequestKeyRef.current === requestKey) {
        workflowRequestKeyRef.current = null;
        setWorkflowsLoading(false);
      }
    }
  }, [context?.ownerBotId, context?.ownerUserId]);

  // ownerBotId/ownerUserId 变化时清空已加载标记，避免沿用上一个 Bot 的工作流。
  useEffect(() => {
    loadedWorkflowKeyRef.current = null;
    setWorkflows([]);
  }, [context?.ownerBotId, context?.ownerUserId]);

  return { workflows, workflowsLoading, loadWorkflows };
}
