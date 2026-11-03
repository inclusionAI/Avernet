import type { PanelHandle } from '@tc-chat/core';
import { useTaskConversationNavigationStore } from '@/stores/taskConversationNavigationStore';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import { useEffect } from 'react';

/**
 * AppShell 已确认路由离开导航目标后，由持久副屏宿主强制释放旧 Panel。
 * 此时原路由插槽可能已卸载，必须复用 host 持有的 stable handle 清空 tabs。
 */
export function useStaleTaskPanelRelease(handle: PanelHandle): void {
  const staleRouteReleaseSignal = useTaskConversationNavigationStore((state) => state.staleRouteReleaseSignal);

  useEffect(() => {
    if (staleRouteReleaseSignal === 0) return;
    const taskNavigationStore = useTaskConversationNavigationStore.getState();
    const navigation = taskNavigationStore.navigation;
    if (navigation) taskNavigationStore.completeNavigation(navigation.id);
    handle.closePanelForce();
    taskNavigationStore.resetAll();
    useTaskSidePanelStore.getState().resetAll();
  }, [handle, staleRouteReleaseSignal]);
}
