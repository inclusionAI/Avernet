import { clampView, getAvailableViews } from '@/domain/collaboration/availableViews';
import type { IdentityView } from '@/domain/collaboration/types';
import type { WorkspaceRouteState } from '@/domain/workspaceRoute';
import { useWorkspaceStore } from '@/stores/workspaceStore';

export type WorkspaceRouteHydrationResult =
  | { status: 'waiting_for_identities' }
  | { status: 'applied'; unresolvedGroupSessionId?: string };

function resolveRouteIdentity(route: WorkspaceRouteState, identities: IdentityView[]): IdentityView | null {
  const store = useWorkspaceStore.getState();
  const requestedIdentityId = route.currentIdentityId ?? route.legacyGroupIdentityId;
  const requested = requestedIdentityId
    ? identities.find((identity) => identity.id === requestedIdentityId) ?? null
    : null;
  if (requested) return requested;
  const active = identities.find((identity) => identity.id === store.activeIdentityId) ?? null;
  // 兼容旧版内部 tab=group 切换产生的无 current、无 session URL：保持当前 Bot 身份。
  if (!requestedIdentityId && route.view === 'group' && !route.sessionId && active) return active;
  const user = identities.find((identity) => identity.kind === 'user');
  if (user) return user;
  return active ?? identities[0] ?? null;
}

function closeExpandedGroup(): void {
  const store = useWorkspaceStore.getState();
  const expandedGroupId = Object.keys(store.expandedGroupIds)[0];
  if (expandedGroupId) store.toggleGroupExpanded(expandedGroupId);
}

function closeExpandedBot(): void {
  const store = useWorkspaceStore.getState();
  const expandedBotId = Object.keys(store.expandedBotIds)[0];
  if (expandedBotId) store.toggleBotExpanded(expandedBotId);
  else if (store.selectedBotSessionId) store.selectBotSession(null);
}

function closeExpandedFriendUser(): void {
  const store = useWorkspaceStore.getState();
  if (store.expandedFriendUserId) store.setExpandedFriendUser(null);
  else if (store.selectedFriendUserSessionId) store.selectFriendUserSession(null);
}

function applyGroupRoute(route: WorkspaceRouteState): WorkspaceRouteHydrationResult {
  let store = useWorkspaceStore.getState();
  const membership = route.membership ?? 'direct';
  if (store.membership !== membership) store.setMembership(membership);

  if (!route.groupId) {
    if (store.selectedGroupId) store.selectGroup(null);
    if (store.selectedSessionId) store.selectSession(null);
    closeExpandedGroup();
    return route.sessionId ? { status: 'applied', unresolvedGroupSessionId: route.sessionId } : { status: 'applied' };
  }

  if (store.selectedGroupId !== route.groupId) store.selectGroup(route.groupId);
  store = useWorkspaceStore.getState();
  if (!store.expandedGroupIds[route.groupId]) store.toggleGroupExpanded(route.groupId);
  store = useWorkspaceStore.getState();
  if (store.selectedSessionId !== (route.sessionId ?? null)) {
    store.selectSession(route.sessionId ?? null);
    if (route.sessionId) store.bumpHistoryRefresh();
  }
  return { status: 'applied' };
}

function applyHumanBotChatRoute(route: WorkspaceRouteState): WorkspaceRouteHydrationResult {
  closeExpandedFriendUser();
  const targetBotId = route.targetBotId;
  if (!targetBotId) {
    closeExpandedBot();
    return { status: 'applied' };
  }

  let store = useWorkspaceStore.getState();
  if (!store.expandedBotIds[targetBotId]) store.toggleBotExpanded(targetBotId);
  store = useWorkspaceStore.getState();
  if (!store.expandedBotSectionKey[targetBotId]) store.setBotExpandedSection(targetBotId, 'mine');
  store = useWorkspaceStore.getState();
  if (store.selectedBotSessionId !== (route.sessionId ?? null)) {
    store.selectBotSession(route.sessionId ?? null);
    if (route.sessionId) store.bumpHistoryRefresh();
  }
  return { status: 'applied' };
}

function applyBotFriendChatRoute(route: WorkspaceRouteState): WorkspaceRouteHydrationResult {
  closeExpandedBot();
  const targetHumanId = route.targetHumanId;
  if (!targetHumanId) {
    closeExpandedFriendUser();
    return { status: 'applied' };
  }

  let store = useWorkspaceStore.getState();
  if (store.expandedFriendUserId !== targetHumanId) store.setExpandedFriendUser(targetHumanId);
  store = useWorkspaceStore.getState();
  if (store.selectedFriendUserSessionId !== (route.sessionId ?? null)) {
    store.selectFriendUserSession(route.sessionId ?? null);
  }
  return { status: 'applied' };
}

function applyChatRoute(route: WorkspaceRouteState, identity: IdentityView): WorkspaceRouteHydrationResult {
  return identity.kind === 'bot' ? applyBotFriendChatRoute(route) : applyHumanBotChatRoute(route);
}

/**
 * 将一个完整 Workspace URL 原子语义地回填到 Store。
 * 身份变化必须走 setActiveIdentity，确保身份级记忆、视图钳制和临时状态清理保持一致。
 */
export function hydrateWorkspaceRoute(route: WorkspaceRouteState): WorkspaceRouteHydrationResult {
  if (!route.explicit) return { status: 'applied' };
  const identities = useWorkspaceStore.getState().identities;
  if (identities.length === 0) {
    // 群深链可先展示目标选中态，但身份切换必须等待真实 identities 后再执行。
    if (route.view === 'group' && route.groupId) {
      let store = useWorkspaceStore.getState();
      if (route.membership && store.membership !== route.membership) store.setMembership(route.membership);
      if (store.selectedGroupId !== route.groupId) store.selectGroup(route.groupId);
      store = useWorkspaceStore.getState();
      if (!store.expandedGroupIds[route.groupId]) store.toggleGroupExpanded(route.groupId);
      store = useWorkspaceStore.getState();
      if (store.selectedSessionId !== (route.sessionId ?? null)) store.selectSession(route.sessionId ?? null);
    }
    return { status: 'waiting_for_identities' };
  }

  const targetIdentity = resolveRouteIdentity(route, identities);
  if (!targetIdentity) return { status: 'waiting_for_identities' };
  let store = useWorkspaceStore.getState();
  if (store.activeIdentityId !== targetIdentity.id) store.setActiveIdentity(targetIdentity.id);

  store = useWorkspaceStore.getState();
  const requestedView = route.view ?? store.view;
  const resolvedView = clampView(getAvailableViews(targetIdentity), requestedView);
  if (store.view !== resolvedView) store.setView(resolvedView);

  // current 指向的身份不支持 URL 请求的视图时，以身份能力为准，并清理另一视图的悬空选中。
  if (resolvedView !== requestedView) {
    if (resolvedView === 'group') {
      closeExpandedBot();
      closeExpandedFriendUser();
    } else closeExpandedGroup();
    return { status: 'applied' };
  }
  return resolvedView === 'group' ? applyGroupRoute(route) : applyChatRoute(route, targetIdentity);
}

export function applyResolvedGroupSession(groupId: string, sessionId: string): void {
  let store = useWorkspaceStore.getState();
  if (store.selectedGroupId !== groupId) store.selectGroup(groupId);
  store = useWorkspaceStore.getState();
  if (!store.expandedGroupIds[groupId]) store.toggleGroupExpanded(groupId);
  store = useWorkspaceStore.getState();
  if (store.selectedSessionId !== sessionId) {
    store.selectSession(sessionId);
    store.bumpHistoryRefresh();
  }
}
