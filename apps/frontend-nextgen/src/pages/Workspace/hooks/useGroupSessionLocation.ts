import type { GroupView, SessionView } from '@/domain/collaboration';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCallback, useEffect, useRef, useState } from 'react';

export interface GroupSessionLocation {
  group: GroupView;
  session: SessionView;
  request: number;
}

/** 定位只改变侧栏展示，不调用 selectGroup/openSession，也不刷新会话历史。 */
export function useGroupSessionLocation(
  group: GroupView | null,
  session: SessionView | null,
  openMobileList?: () => void,
) {
  const identityId = useWorkspaceStore((state) => state.activeIdentityId);
  const groupSearch = useWorkspaceStore((state) => state.groupSearchText);
  const kindFilter = useWorkspaceStore((state) => state.groupKindFilter);
  const sessionSearch = useWorkspaceStore((state) => state.sessionSearchText);
  const sessionTab = useWorkspaceStore((state) => (group ? state.sessionTabsByGroup[group.groupId] : undefined));
  const requestSequence = useRef(0);
  const [requested, setRequested] = useState<(GroupSessionLocation & { identityId: string | null }) | undefined>();
  const location =
    !groupSearch &&
    kindFilter === 'all' &&
    !sessionSearch &&
    sessionTab !== 'favorite' &&
    requested?.identityId === identityId &&
    requested?.group.groupId === group?.groupId &&
    requested?.session.sessionId === session?.sessionId
      ? requested
      : undefined;
  const dismissLocation = useCallback(() => setRequested(undefined), []);
  useEffect(() => {
    // 用户继续筛选/搜索、切会话或切身份后，不再恢复上次定位的展示覆盖。
    if (requested && !location) dismissLocation();
  }, [requested, location, dismissLocation]);
  const locate = () => {
    if (!group || !session || session.groupId !== group.groupId) return;
    const store = useWorkspaceStore.getState();
    store.setGroupSearchText('');
    store.setGroupKindFilter('all');
    store.setSessionSearchText('');
    store.setSessionTabForGroup(group.groupId, 'all');
    setRequested({ group, session, identityId, request: ++requestSequence.current });
    if (window.matchMedia?.('(max-width: 1023px)').matches) openMobileList?.();
  };
  return { locate, location, dismissLocation };
}
