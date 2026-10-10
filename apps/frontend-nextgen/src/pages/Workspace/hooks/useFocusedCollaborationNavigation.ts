import { fullCollaborationUrl, type CollaborationPresentation } from '@/domain/collaborationPresentation';
import { serializeWorkspaceRoute } from '@/domain/workspaceRoute';
import type { CollaborationScope } from '@/services/workspace/collaborationScopeService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { history, useLocation } from '@umijs/max';
import { useEffect, type MutableRefObject } from 'react';

export function useFocusedCollaborationNavigation(
  mode: CollaborationPresentation,
  scope: CollaborationScope | null,
  projectedQuery?: MutableRefObject<string | null>,
) {
  const location = useLocation();
  const currentIdentityId = useWorkspaceStore((s) => s.activeIdentityId);
  const groupId = useWorkspaceStore((s) => s.selectedGroupId);
  const sessionId = useWorkspaceStore((s) => s.selectedSessionId);
  const membership = useWorkspaceStore((s) => s.membership);
  const valid =
    mode === 'only' || Boolean(scope && scope.identityId === currentIdentityId && scope.group.groupId === groupId);
  const validSessionId = mode === 'session' && scope?.session?.sessionId !== sessionId ? null : sessionId;
  const query = valid
    ? serializeWorkspaceRoute({ currentIdentityId, groupId, sessionId: validSessionId, membership })
    : '';
  useEffect(() => {
    // 单会话链接绝不随删除/退出回落；单群页只投影群内选择，保留当前 pathname。
    if (mode !== 'group' || !valid || location.search.replace(/^\?/, '') === query) return;
    if (projectedQuery) projectedQuery.current = query;
    history.replace(`${location.pathname}?${query}`);
  }, [location.pathname, location.search, mode, query, valid, projectedQuery]);
  const returnUrl = valid
    ? fullCollaborationUrl({ currentIdentityId, groupId, sessionId: validSessionId, membership })
    : fullCollaborationUrl({});
  return { goBack: () => history.push(returnUrl) };
}
