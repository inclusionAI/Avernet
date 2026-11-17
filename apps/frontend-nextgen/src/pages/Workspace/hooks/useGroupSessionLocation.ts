import type { WorkspaceSessionLocationRequest, WorkspaceRouteMembership } from '@/domain/workspaceRoute';
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
  routeRequest?: WorkspaceSessionLocationRequest,
  targetUnavailable = false,
) {
  const identityId = useWorkspaceStore((state) => state.activeIdentityId);
  const selectedGroupId = useWorkspaceStore((state) => state.selectedGroupId);
  const selectedSessionId = useWorkspaceStore((state) => state.selectedSessionId);
  const handledRouteRequest = useRef<number>();
  const pendingFilter = useRef<{ request: number; key: string }>();
  const membership = useWorkspaceStore((state) => state.membership);
  const groupSearch = useWorkspaceStore((state) => state.groupSearchText);
  const kindFilter = useWorkspaceStore((state) => state.groupKindFilter);
  const sessionSearch = useWorkspaceStore((state) => state.sessionSearchText);
  const sessionTab = useWorkspaceStore((state) => selectedGroupId ? state.sessionTabsByGroup[selectedGroupId] : undefined);
  const filterKey = JSON.stringify([membership, groupSearch, kindFilter, sessionSearch, sessionTab ?? 'all']);
  const requestSequence = useRef(0);
  const [requested, setRequested] = useState<(GroupSessionLocation & { identityId: string | null; membership: WorkspaceRouteMembership }) | undefined>();
  // 详情重拉会暂时清空 props；定位随 Store 选中态失效，而非随加载骨架失效。
  const location =
    !targetUnavailable &&
    !groupSearch &&
    kindFilter === 'all' &&
    !sessionSearch &&
    sessionTab !== 'favorite' &&
    requested?.identityId === identityId &&
    requested?.membership === membership &&
    requested?.group.groupId === selectedGroupId &&
    requested?.session.sessionId === selectedSessionId
      ? requested
      : undefined;
  const dismissLocation = useCallback(() => {
    if (routeRequest) handledRouteRequest.current = routeRequest.request;
    setRequested(undefined);
  }, [routeRequest]);
  useEffect(() => {
    // 用户继续筛选/搜索、切会话或切身份后，不再恢复上次定位的展示覆盖。
    if (requested && !location) dismissLocation();
  }, [requested, location, dismissLocation]);
  const locate = useCallback(() => {
    if (!group || !session || session.groupId !== group.groupId) return;
    const store = useWorkspaceStore.getState();
    // 只使用当前身份已验证的详情字段；列表缺失或字段缺失均不能推断参与方式。
    const targetMembership = group.membership ?? store.membership;
    // 使用原始筛选 setter，避免手动切换入口清空当前群/会话选中态。
    if (store.membership !== targetMembership) store.setMembership(targetMembership);
    store.setGroupSearchText('');
    store.setGroupKindFilter('all');
    store.setSessionSearchText('');
    store.setSessionTabForGroup(group.groupId, 'all');
    setRequested({ group, session, identityId, membership: targetMembership, request: ++requestSequence.current });
    if (window.matchMedia?.('(max-width: 1023px)').matches) openMobileList?.();
  }, [group, session, identityId, openMobileList]);
  useEffect(() => {
    if (!routeRequest || handledRouteRequest.current === routeRequest.request) return;
    if (pendingFilter.current?.request !== routeRequest.request) {
      pendingFilter.current = { request: routeRequest.request, key: filterKey };
    }
    // 收到意图后产生的新筛选、显式关闭或确定的验证失败优先于自动定位。
    if (targetUnavailable || pendingFilter.current.key !== filterKey) {
      handledRouteRequest.current = routeRequest.request;
      return;
    }
    // 等详情时用户已切身份/选中项：取消旧意图，不能被晚到响应带回原会话。
    if (identityId !== routeRequest.identityId || selectedGroupId !== routeRequest.groupId
      || selectedSessionId !== routeRequest.sessionId) {
      handledRouteRequest.current = routeRequest.request;
      return;
    }
    if (group?.groupId !== routeRequest.groupId || session?.sessionId !== routeRequest.sessionId
      || session.groupId !== group.groupId) return;
    handledRouteRequest.current = routeRequest.request;
    locate();
  }, [filterKey, group, identityId, locate, routeRequest, selectedGroupId, selectedSessionId, session, targetUnavailable]);
  return { locate, location, dismissLocation };
}
