import type { PanelHandle, PanelTab } from '@tc-chat/core';
import { shouldPreserveTaskPanelDuringNavigation } from '@/services/tasks/taskConversationNavigation';
import { useTaskConversationNavigationStore } from '@/stores/taskConversationNavigationStore';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import { peekTaskPanelController } from './taskPanelRuntime';

/** SDK handle translation only. Selection/nav policy stays in the shared controller. */
function taskPanelTaskId(tab: PanelTab): string | null {
  if (tab.type !== 'taskPanel.TaskLoopView' || !tab.id.startsWith('task-')) return null;
  const taskId = tab.id.slice('task-'.length);
  return taskId || null;
}

function shouldKeepTaskPanelActiveTask(tab: PanelTab): boolean {
  const store = useTaskConversationNavigationStore.getState();
  if (!store.navigation || !shouldPreserveTaskPanelDuringNavigation()) return false;
  const taskId = taskPanelTaskId(tab);
  if (taskId === null) return false;

  // 显式切换任务先登记 intent；对应 tab 的这一次 openTab 是用户来源，必须放行。
  // 其余 openTab 均视为会话历史恢复，不得抢占或重渲染当前选中的 TaskPanel。
  if (useTaskSidePanelStore.getState().consumeTaskPanelOpenIntent(taskId)) return false;
  return true;
}

/** SDK's internal tab-X and imperative ref.closeTab share this acknowledgement. */
export function onTaskPanelTabClosed(id: string): void {
  const controller = peekTaskPanelController();
  const active = controller?.getSnapshot().activeTaskId;
  const store = useTaskConversationNavigationStore.getState();
  if (active && (id === `task-${active}` || store.tabsByTask[active]?.id === id)) controller?.closeTask();
  if (store.navigation?.tab?.id === id) store.completeNavigation(store.navigation.id);
}

export function taskNavigationAwareHandle(handle: PanelHandle): PanelHandle {
  return {
    // 返回根会话时，历史面板消息可能随 hydration 重新 openTab 旧任务 tab。任务节点
    // handoff 本身已经复用持久 Panel；除用户在切换条显式选择的任务外，会话历史
    // 驱动的 openTab 不能抢占当前选中的 TaskPanel 任务。
    openTab: (tab) => {
      if (shouldKeepTaskPanelActiveTask(tab)) return;
      handle.openTab(tab);
    },
    closeTab: (id) => { onTaskPanelTabClosed(id); handle.closeTab(id); },
    updateTab: (id, update) => handle.updateTab(id, update),
    focusTab: (id) => handle.focusTab(id),
    openPanel: () => handle.openPanel(),
    closePanel: () => {
      peekTaskPanelController()?.cancelNavigation();
      const store = useTaskConversationNavigationStore.getState();
      if (store.navigation) store.completeNavigation(store.navigation.id);
      handle.closePanel();
    },
    closePanelForce: () => {
      if (shouldPreserveTaskPanelDuringNavigation()) return;
      // 普通会话切换会清空全部 tabs，同步丢弃旧任务宿主与焦点任务状态。
      // 否则 activeTaskId 会残留到下一个 session，把 by_session 自动恢复挡在
      // “已展示任务不抢焦点”分支，表现为刷新后能打开、切换后打不开。
      useTaskConversationNavigationStore.getState().resetAll();
      peekTaskPanelController()?.leaveConversation();
      handle.closePanelForce();
    },
    flushContext: () => handle.flushContext(),
    flushInteractions: () => handle.flushInteractions(),
  };
}
