import { sessionService } from '@/services/workspace/sessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCallback } from 'react';

/** 创建协作群后优先打开后端返回的初始会话；缺失时回退到第一个会话。 */
export function useOpenDefaultGroupSession(): (groupId: string, initialSessionId?: string) => Promise<void> {
  return useCallback(async (groupId: string, initialSessionId?: string) => {
    let sessions: Awaited<ReturnType<typeof sessionService.loadSessionsByIds>> = [];
    if (!initialSessionId) {
      try {
        sessions = await sessionService.loadSessionsByIdsOrBcs(groupId, 0);
      } catch {
        return;
      }
    }
    const firstSessionId = initialSessionId ?? sessions[0]?.sessionId;
    const store = useWorkspaceStore.getState();
    if (!store.expandedGroupIds[groupId]) store.toggleGroupExpanded(groupId);
    if (store.selectedGroupId !== groupId) store.selectGroup(groupId);
    if (firstSessionId) {
      useWorkspaceStore.getState().selectSession(firstSessionId);
      useWorkspaceStore.getState().bumpHistoryRefresh();
    }
  }, []);
}

export default useOpenDefaultGroupSession;
