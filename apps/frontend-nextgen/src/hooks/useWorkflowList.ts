/**
 * useWorkflowList —— 「+」菜单与任务护航共用的可执行工作流按需加载。
 * Controller 调用收口在 taskService；认证仍由 backendRequest 负责。
 * 仅缓存当前 ownerUserId/ownerBotId 范围的一次成功结果；切换会话不重复查询。
 */
import type { TaskComposerContext } from '@/services/tasks/taskMapper';
import { fetchWorkflows, type WorkflowListItem } from '@/services/tasks/taskService';
import { useCallback, useRef, useState } from 'react';

export interface UseWorkflowListResult {
  workflows: WorkflowListItem[];
  workflowsLoading: boolean;
  /** 当前范围的加载错误；重新调用 loadWorkflows 可重试。 */
  workflowsError: string | null;
  /** 按当前 Bot 懒加载；并发调用等待同一请求，失败通过 workflowsError 呈现。 */
  loadWorkflows: () => Promise<void>;
}

interface WorkflowListState {
  scopeKey: string | null;
  generation: number;
  workflows: WorkflowListItem[];
  workflowsLoading: boolean;
  workflowsError: string | null;
}

function emptyState(scopeKey: string | null, generation: number): WorkflowListState {
  return { scopeKey, generation, workflows: [], workflowsLoading: false, workflowsError: null };
}

export function useWorkflowList(context: TaskComposerContext | null): UseWorkflowListResult {
  const ownerUserId = context?.ownerUserId?.trim();
  const ownerBotId = context?.ownerBotId?.trim();
  const scopeKey = ownerUserId && ownerBotId ? JSON.stringify([ownerUserId, ownerBotId]) : null;
  const [state, setState] = useState(() => emptyState(scopeKey, 0));
  // 成功的 Promise 保留为当前范围缓存；失败才移除，允许显式重试。最多保存一个请求。
  const requestRef = useRef<{ generation: number; promise: Promise<void> } | null>(null);

  // 在 render 调整本组件状态，避免等 effect 才清理而把旧列表/错误暴露给新范围。
  // 每次范围变化（包括 null）都换代，A→B→A 也不能复用第一次 A 的请求或结果。
  if (state.scopeKey !== scopeKey) setState(emptyState(scopeKey, state.generation + 1));
  const generation = state.generation;

  const loadWorkflows = useCallback((): Promise<void> => {
    if (!ownerUserId || !ownerBotId) return Promise.resolve();
    if (requestRef.current?.generation === generation) return requestRef.current.promise;
    setState((current) => current.generation === generation
      ? { ...current, workflows: [], workflowsLoading: true, workflowsError: null }
      : current);
    const request = {
      generation,
      // 延后到微任务执行，先登记请求，保证并发调用（包括同步失败）也只有一次加载。
      promise: Promise.resolve().then(async () => {
        try {
          const workflows = await fetchWorkflows(ownerUserId, ownerBotId);
          setState((current) => current.generation === generation
            ? { ...current, workflows, workflowsLoading: false, workflowsError: null }
            : current);
        } catch (error) {
          if (requestRef.current === request) requestRef.current = null;
          const workflowsError = error instanceof Error && error.message ? error.message : '工作流加载失败，请重试';
          setState((current) => current.generation === generation
            ? { ...current, workflows: [], workflowsLoading: false, workflowsError }
            : current);
        }
      }),
    };
    requestRef.current = request;
    return request.promise;
  }, [ownerUserId, ownerBotId, generation]);

  const current = state.scopeKey === scopeKey ? state : emptyState(scopeKey, generation);
  return {
    workflows: current.workflows,
    workflowsLoading: current.workflowsLoading,
    workflowsError: current.workflowsError,
    loadWorkflows,
  };
}
