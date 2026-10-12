import type { GroupView, SessionView } from '@/domain/collaboration';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { groupService } from './groupService';
import type { DomainResult } from './identityService';
import { sessionService } from './sessionService';

export interface CollaborationScope {
  identityId: string;
  group: GroupView;
  session: SessionView | null;
}

function failure(code: string, friendlyMessage: string): DomainResult<never> {
  return { ok: false, error: { code, friendlyMessage, canRetry: false } };
}

/** 初次装载和范围内刷新共用相同归属/解散校验，不允许刷新后换成其他群。 */
export async function loadScopedGroup(groupId: string, identityId: string): Promise<DomainResult<GroupView>> {
  const group = await groupService.loadGroupDetailOrBcs(groupId, identityId);
  if (!group.ok) return group;
  if (group.data.groupId !== groupId || group.data.status === 'dissolved') {
    return failure('GROUP_UNAVAILABLE', '指定协作群不存在、已解散或无权访问。');
  }
  return group;
}

export const collaborationScopeService = {
  async load(
    groupId: string,
    identityId: string,
    sessionId?: string,
  ): Promise<DomainResult<Omit<CollaborationScope, 'identityId'>>> {
    const group = await loadScopedGroup(groupId, identityId);
    if (!group.ok) return group;
    if (!sessionId) return { ok: true, data: { group: group.data, session: null } };
    const session = await sessionService.getSessionDetail(sessionId);
    if (!session.ok) return session;
    if (session.data.groupId !== groupId || session.data.sessionId !== sessionId) {
      return failure('SESSION_GROUP_MISMATCH', '指定会话不属于当前协作群，请检查链接。');
    }
    return { ok: true, data: { group: group.data, session: session.data } };
  },

  activate(scope: CollaborationScope): void {
    let store = useWorkspaceStore.getState();
    if (store.activeIdentityId !== scope.identityId) store.setActiveIdentity(scope.identityId);
    store = useWorkspaceStore.getState();
    store.setIsGroupsLoading(false);
    store.selectGroup(scope.group.groupId);
    store.selectSession(scope.session?.sessionId ?? null);
    store.setMembership(scope.group.membership ?? 'direct');
    store.setSessionSearchText('');
    if (!store.expandedGroupIds[scope.group.groupId]) store.toggleGroupExpanded(scope.group.groupId);
  },
};
