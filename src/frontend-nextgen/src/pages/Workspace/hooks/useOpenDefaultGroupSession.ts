import { sessionService } from '@/services/workspace/sessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCallback } from 'react';

/** 创建协作群后打开后端为该群默认生成的第一个会话。 */
export function useOpenDefaultGroupSession(): (groupId: string) => Promise<void> {
  return useCallback(async (groupId: string) => {
    let sessions: Awaited<ReturnType<typeof sessionService.loadSessionsByIds>> = [];
    try {
      sessions = await sessionService.loadSessionsByIdsOrBcs(groupId, 0);
    } catch {
      return;
    }
    const firstSessionId = sessions[0]?.sessionId;
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
