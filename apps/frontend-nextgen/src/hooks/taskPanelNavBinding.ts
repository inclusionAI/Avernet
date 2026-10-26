import { isCurrentTaskConversationOrigin } from '@/services/tasks/taskConversationNavigation';
import { useTaskConversationNavigationStore } from '@/stores/taskConversationNavigationStore';
import { useTaskSidePanelStore, type TaskNavRecord, type TaskNavTarget } from '@/stores/taskSidePanelStore';
import { useCallback } from 'react';

/**
 * 资产层 TaskPanelNav 的结构类型（结构即契约，资产层 v2/taskPanelNav.ts 落地后如有
 * 差异以资产层定义为准；双方互不 import，靠 structural typing 对齐）。
 */
export interface TaskPanelNav {
  selectedNodeId: string | null;
  drillStack: string[];
  selectRootNode: (nodeId: string) => void;
  selectNode: (nodeId: string, target: TaskNavTarget | null) => void;
  drillInto: (nodeId: string, target: TaskNavTarget | null) => void;
  backToOverview: () => void;
}

/**
 * 把 taskSidePanelStore 的指定 task 分片缝合成资产层导航 prop。
 * 回调闭包绑定 taskId，多 tab 并存时不串场（操作永远落在自己的 task 上）。
 */
export function useTaskPanelNavBinding(taskId: string): TaskPanelNav {
  const record: TaskNavRecord | undefined = useTaskSidePanelStore((s) => s.navByTask[taskId]);

  const selectRootNode = useCallback(
    (nodeId: string) => {
      const origin = useTaskConversationNavigationStore.getState().originsByTask[taskId];
      const restoreTarget = origin && !isCurrentTaskConversationOrigin(origin) ? origin : null;
      useTaskSidePanelStore.getState().selectRootNode(taskId, nodeId, restoreTarget);
    },
    [taskId],
  );
  const selectNode = useCallback(
    (nodeId: string, target: TaskNavTarget | null) => {
      useTaskSidePanelStore.getState().selectNode(taskId, nodeId, target);
    },
    [taskId],
  );
  const drillInto = useCallback(
    (nodeId: string, target: TaskNavTarget | null) => {
      useTaskSidePanelStore.getState().drillInto(taskId, nodeId, target);
    },
    [taskId],
  );
  const backToOverview = useCallback(() => {
    useTaskSidePanelStore.getState().backToOverview(taskId);
  }, [taskId]);

  return {
    selectedNodeId: record?.selectedNodeId ?? null,
    drillStack: record?.drillStack ?? [],
    selectRootNode,
    selectNode,
    drillInto,
    backToOverview,
  };
}
