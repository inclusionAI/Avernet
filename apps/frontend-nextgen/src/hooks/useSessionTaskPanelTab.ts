import type { PanelTab } from '@tc-chat/core';
import { useTaskConversationNavigationStore } from '@/stores/taskConversationNavigationStore';
import { useTaskSessionAssociationStore } from '@/stores/taskSessionAssociationStore';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';

/**
 * 当前会话可重开的 TaskPanel 快照。
 *
 * by_session 只给 task ref，Monitor 重开不能依赖消息里的 AixUI 标记。
 * 这里取：
 * 1. 当前会话关联的 task id
 * 2. `TaskPanelAdapterWithUser` 挂载时写进 `tabsByTask` 的真实 tab snapshot
 *    （same task opens/closes/reopens 全程保留）
 */
export function useSessionTaskPanelTab(
  sessionId: string | null,
): PanelTab | null {
  const activeSessionId = useTaskSessionAssociationStore((state) => state.activeSessionId);
  const sessionTaskIds = useTaskSessionAssociationStore((state) => state.activeSessionTaskIds);
  const activeTaskId = useTaskSidePanelStore((state) => state.activeTaskId);
  const tabsByTask = useTaskConversationNavigationStore((state) => state.tabsByTask);

  if (!sessionId || activeSessionId !== sessionId || sessionTaskIds.length === 0) return null;
  const taskIds = sessionTaskIds.filter(Boolean);
  const effectiveTaskId =
    activeTaskId && taskIds.includes(activeTaskId) ? activeTaskId : taskIds[0];
  if (!effectiveTaskId) return null;
  const tab = tabsByTask[effectiveTaskId];
  return tab ?? null;
}

export default useSessionTaskPanelTab;
